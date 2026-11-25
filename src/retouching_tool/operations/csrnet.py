"""Full-resolution, memory-bounded evaluation of the CSRNet inference architecture."""

import numpy as np
import torch
from torch import nn


def _srgb_tensor(pixels, device):
    linear = torch.tensor(pixels, device=device).permute(2, 0, 1).unsqueeze(0)
    return torch.where(linear <= .0031308, linear * 12.92, 1.055 * linear.pow(1 / 2.4) - .055)


class CSRNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.cond_net = nn.Module()
        self.cond_net.conv1 = nn.Conv2d(3, 32, 7, stride=2, padding=1)
        self.cond_net.conv2 = nn.Conv2d(32, 32, 3, stride=2, padding=1)
        self.cond_net.conv3 = nn.Conv2d(32, 32, 3, stride=2, padding=1)
        self.conv1 = nn.Conv2d(3, 64, 1)
        self.conv2 = nn.Conv2d(64, 64, 1)
        self.conv3 = nn.Conv2d(64, 3, 1)
        self.cond_scale1, self.cond_shift1 = nn.Linear(32, 64), nn.Linear(32, 64)
        self.cond_scale2, self.cond_shift2 = nn.Linear(32, 64), nn.Linear(32, 64)
        self.cond_scale3, self.cond_shift3 = nn.Linear(32, 3), nn.Linear(32, 3)

    def _condition(self, pixels, device):
        # The first padded 7x7 convolution needs at least five input samples per axis.
        height, width = pixels.shape[:2]
        if min(height, width) < 5:
            pixels = np.pad(pixels, ((0, max(0, 5 - height)), (0, max(0, 5 - width)), (0, 0)), mode="edge")
        # Three stride-two convolutions. Each final cell sees input [8*i-7, 8*i+11].
        rows, cols = ((length + 3) // 8 for length in pixels.shape[:2])
        total = torch.zeros((1, 32), device=device, dtype=torch.float64)
        block = 64
        for y in range(0, rows, block):
            for x in range(0, cols, block):
                ny, nx = min(block, rows - y), min(block, cols - x)
                top, left = max(0, 8 * y - 8), max(0, 8 * x - 8)
                patch = pixels[top:8 * (y + ny) + 8, left:8 * (x + nx) + 8]
                features = _srgb_tensor(patch, device)
                for conv in (self.cond_net.conv1, self.cond_net.conv2, self.cond_net.conv3):
                    features = conv(features).relu_()
                dy, dx = y - top // 8, x - left // 8
                total += features[:, :, dy:dy + ny, dx:dx + nx].sum((2, 3), dtype=torch.float64)
        return (total / (rows * cols)).float()

    @torch.inference_mode()
    def grade(self, pixels, strength):
        device = self.conv1.weight.device
        condition = self._condition(pixels, device)
        layers = ((self.conv1, self.cond_scale1, self.cond_shift1),
                  (self.conv2, self.cond_scale2, self.cond_shift2),
                  (self.conv3, self.cond_scale3, self.cond_shift3))
        modulation = [(conv, scale(condition)[:, :, None, None], shift(condition)[:, :, None, None])
                      for conv, scale, shift in layers]
        output = np.empty_like(pixels)
        for y in range(0, pixels.shape[0], 256):
            for x in range(0, pixels.shape[1], 256):
                area = (slice(y, y + 256), slice(x, x + 256))
                source = pixels[area]
                tile = _srgb_tensor(source, device)
                for conv, scale, shift in modulation:
                    values = conv(tile)
                    tile = values * scale + shift + values
                    if conv is not self.conv3:
                        tile.relu_()
                if not torch.isfinite(tile).all():
                    raise ValueError("CSRNet produced nonfinite RGB values.")
                tile.clamp_(0, 1)
                linear = torch.where(tile <= .04045, tile / 12.92, ((tile + .055) / 1.055).pow(2.4))
                edited = linear[0].permute(1, 2, 0).cpu().numpy()
                output[area] = source + strength * (edited - source) if strength < 1 else edited
        return output


def load_network(path, device):
    network = CSRNet()
    network.load_state_dict(torch.load(path, map_location="cpu", weights_only=True)["params"], strict=True)
    return network.eval().to(device)
