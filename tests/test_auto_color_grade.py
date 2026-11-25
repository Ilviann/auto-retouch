"""Numerical regression for the tiled CSRNet conditioning and pointwise grade."""

import unittest

import numpy as np
import torch

from retouching_tool.operations.csrnet import CSRNet


class ColorGradeTests(unittest.TestCase):
    @torch.inference_mode()
    def test_tiled_grade_matches_one_full_frame_including_edges(self):
        with torch.random.fork_rng():
            torch.manual_seed(80)
            network = CSRNet().eval()
        for height, width in ((1, 4), (11, 13), (517, 531), (526, 520)):
            with self.subTest(height=height, width=width):
                pixels = np.random.default_rng(93).random((height, width, 3), dtype=np.float32)
                linear = torch.from_numpy(pixels).permute(2, 0, 1)[None]
                rgb = torch.where(linear <= .0031308, linear * 12.92, 1.055 * linear.pow(1/2.4) - .055)
                cond = torch.nn.functional.pad(rgb, (0, max(5-width, 0), 0, max(5-height, 0)), mode="replicate")
                for conv in (network.cond_net.conv1, network.cond_net.conv2, network.cond_net.conv3):
                    cond = conv(cond).relu()
                cond = cond.mean((2, 3))
                for index in (1, 2, 3):
                    values = getattr(network, f"conv{index}")(rgb)
                    scale = getattr(network, f"cond_scale{index}")(cond)[:, :, None, None]
                    shift = getattr(network, f"cond_shift{index}")(cond)[:, :, None, None]
                    rgb = values * scale + shift + values
                    if index < 3:
                        rgb = rgb.relu()
                rgb = rgb.clamp(0, 1)
                expected = torch.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055).pow(2.4))
                expected = (expected * .5 + linear * .5)[0].permute(1, 2, 0).numpy()
                np.testing.assert_allclose(network.grade(pixels, .5), expected, rtol=0, atol=2e-6)
