"""PyTorch inference for the three networks in Alibaba's two skin-retouching checkpoints."""

from ..errors import RetouchingError
from ..imaging.scaling import resize_rgb


def _sample(image, size, methods, *, align_corners=False):
    import torch.nn.functional as F

    current = image.shape[-2:]
    modes = [methods.downscale if target < source else methods.upscale
             for source, target in zip(current, size, strict=True)]

    def sample(tensor, shape, mode):
        if mode == "nearest":
            return F.interpolate(tensor, shape, mode="nearest-exact")
        return F.interpolate(tensor, shape, mode="bilinear", align_corners=align_corners)

    if modes[0] == modes[1]:
        return sample(image, size, modes[0])
    return sample(sample(image, (size[0], current[1]), modes[0]), size, modes[1])


class SkinNetworks:
    def __init__(self, generator_file, joint_file, *, device):
        import torch
        from .alibaba_networks.unet_deploy import UNet
        from .alibaba_networks.detection_model.detection_unet_in import DetectionUNet
        from .alibaba_networks.inpainting_model.inpainting_unet import RetouchingNet

        self.device = torch.device(device)
        self.generator = self.detector = self.repair = None
        if generator_file is not None:
            self.generator = UNet(3, 3, init_weights=False)
            self.generator.load_state_dict(torch.load(generator_file, map_location="cpu", weights_only=True)["generator"])
            self.generator.to(self.device).eval()
        if joint_file is not None:
            joint = torch.load(joint_file, map_location="cpu", weights_only=True)
            self.detector = DetectionUNet(3, 1, init_weights=False)
            self.repair = RetouchingNet(4, 3, init_weights=False)
            self.detector.load_state_dict(joint["detection_net"])
            self.repair.load_state_dict(joint["inpainting_net"])
            self.detector.to(self.device).eval()
            self.repair.to(self.device).eval()

    def _blemish_weights(self, roi, methods):
        import torch
        import torch.nn.functional as F

        detection = self.detector(_sample(roi, (768, 768), methods, align_corners=True)).sigmoid()
        if not detection.isfinite().all():
            raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "Alibaba returned invalid blemish probabilities.")
        weight = torch.where(detection < .35, 0., torch.where(detection >= .5, 1., detection))
        return F.interpolate(weight, roi.shape[-2:], mode="nearest")

    def _tone_maps(self, roi, methods):
        import torch
        import torch.nn.functional as F

        size = roi.shape[-2:]
        gain = self.generator(_sample(roi, (512, 512), methods))
        # The publisher feathers the gain towards neutral over the outer 4% of the crop.
        axis = torch.arange(500, dtype=roi.dtype, device=roi.device)
        ramp = torch.minimum(axis, 500 - axis).div_(20).clamp_(0, 1)
        feather = torch.minimum(ramp[:, None], ramp[None, :])[None, None]
        return _sample(gain, size, methods), F.interpolate(feather, size, mode="bilinear", align_corners=False)

    def apply(self, pixels, methods, strength):
        import torch
        import numpy as np

        height, width = pixels.shape[:2]
        # Keep model calculations in normalized nonlinear RGB, retaining float precision at both boundaries.
        encoded = resize_rgb(pixels, (width, height), methods, encode_srgb=True)
        roi = torch.from_numpy(encoded).to(self.device).permute(2, 0, 1).unsqueeze(0).mul_(2).sub_(1)
        with torch.inference_mode():
            del encoded
            weight = None
            if self.detector is not None:
                weight = self._blemish_weights(roi, methods)
                roi = self._repair_tiles(roi, weight)
            if self.generator is not None:
                weight = None
                gain, feather = self._tone_maps(roi, methods)
            rgb = roi.add_(1).mul_(.5)
            output = np.empty_like(pixels)
            for y in range(0, height, 64):
                rows = slice(y, y + 64)
                base = rgb[:, :, rows]
                if self.generator is not None:
                    correction = (gain[:, :, rows] * 2 - 1) * feather[:, :, rows]
                    base = base + correction * base * (1 - base)
                base = base.clamp(0, 1)
                linear = torch.where(base <= .04045, base / 12.92, ((base + .055) / 1.055).pow(2.4))
                output[rows] = linear[0].permute(1, 2, 0).cpu().numpy()
                if weight is not None:
                    # Keep untouched pixels bit-exact through the sRGB round trip.
                    untouched = (weight[0, 0, rows] == 0).cpu().numpy()
                    np.copyto(output[rows], pixels[rows], where=untouched[:, :, None])
                if strength != 1:
                    output[rows] = pixels[rows] + strength * (output[rows] - pixels[rows])
            return output

    def _repair_tiles(self, roi, weight):
        import torch
        import torch.nn.functional as F

        height, width = roi.shape[2:]
        result = roi.clone()
        # Read all halos from the original crop. Retain only the central 512 × 512 of each prediction.
        for y in range(0, height, 512):
            for x in range(0, width, 512):
                h, w = min(512, height - y), min(512, width - x)
                rows, cols = slice(y, y + h), slice(x, x + w)
                if not torch.any(weight[:, :, rows, cols]):
                    continue
                top, left = max(0, y - 32), max(0, x - 32)
                bottom, right = min(height, y + 544), min(width, x + 544)
                padding = (left - x + 32, x + 544 - right, top - y + 32, y + 544 - bottom)
                patch = F.pad(roi[:, :, top:bottom, left:right], padding)
                keep = F.pad(1 - weight[:, :, top:bottom, left:right], padding)
                masked = patch * keep
                predicted = self.repair(masked, keep)
                core = (slice(None), slice(None), slice(32, 32 + h), slice(32, 32 + w))
                result[:, :, rows, cols] = masked[core] + weight[:, :, rows, cols] * predicted[core]
        return result
