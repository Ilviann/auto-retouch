"""Exercise both Alibaba stages locally on the PyTorch device recorded by setup."""

import argparse
from pathlib import Path

import numpy as np
import torch

from retouching_tool.config import ScalingSettings, load_config
from retouching_tool.environment import read_ready_backends
from retouching_tool.operations.alibaba_inference import SkinNetworks
from retouching_tool.operations.alibaba_skin import MODEL
from retouching_tool.pipeline.assets import ModelCatalog


def check(name, module, inputs, expected_shape, device):
    output = module(*inputs)
    if (tuple(output.shape) != expected_shape or output.dtype != torch.float32
            or output.device.type != device or not torch.isfinite(output).all()):
        raise ValueError(f"{name} returned an unexpected result on {output.device}")
    print(f"{name}: {output.device}, {tuple(output.shape)}, {output.dtype}, range [{output.min():.5g}, {output.max():.5g}]")


def check_stage(name, networks, pixels):
    result = networks.apply(pixels, ScalingSettings(), 1)
    if (result.shape != pixels.shape or result.dtype != np.float32 or not np.isfinite(result).all()
            or result.min() < 0 or result.max() > 1):
        raise ValueError(f"{name} returned invalid linear RGB pixels")
    print(f"{name} returned {result.shape} linear RGB pixels")


@torch.inference_mode()
def verify(folder, device):
    print(f"PyTorch {torch.__version__}, Alibaba {MODEL.version}, device {device.upper()}")
    issues = ModelCatalog((MODEL,)).check(MODEL.ref, folder, device)
    if issues:
        raise ValueError("\n".join(issue.message for issue in issues))
    torch.manual_seed(42)
    pixels = np.random.default_rng(42).random((80, 120, 3), dtype=np.float32)
    image = torch.rand(1, 3, 512, 512, device=device) * 2 - 1
    smoothing = SkinNetworks(folder / MODEL.files[0].relative_path, None, device=device)
    check("Skin smoothing", smoothing.generator, (image,), tuple(image.shape), device)
    check_stage("Skin tone smoothing", smoothing, pixels)
    del smoothing
    cleanup = SkinNetworks(None, folder / MODEL.files[1].relative_path, device=device)
    detection = torch.nn.functional.interpolate(image, size=(768, 768), mode="bilinear", align_corners=True)
    check("Blemish detection", cleanup.detector, (detection,), (1, 1, 768, 768), device)
    patch = torch.nn.functional.pad(image, (32, 32, 32, 32))
    mask = torch.ones_like(patch[:, :1])
    mask[:, :, 280:294, 280:294] = 0
    check("Blemish repair", cleanup.repair, (patch * mask, mask), tuple(patch.shape), device)
    check_stage("Blemish cleanup", cleanup, pixels)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Application configuration with the checkpoint folder")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = load_config(root, args.config)
    _, pytorch_backend = read_ready_backends(root)
    verify(config.checkpoints, pytorch_backend)
