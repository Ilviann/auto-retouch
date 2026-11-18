"""Exercise the original Alibaba networks locally on CPU using synthetic inputs."""

import argparse
import hashlib
from pathlib import Path

import torch

from retouching_tool.operations.alibaba_networks.detection_model.detection_unet_in import DetectionUNet
from retouching_tool.operations.alibaba_networks.inpainting_model.inpainting_unet import RetouchingNet
from retouching_tool.operations.alibaba_networks.unet_deploy import UNet
from retouching_tool.operations.alibaba_skin import MODEL


def load_checkpoint(path, expected_hash):
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_hash:
            raise ValueError(f"Publisher checksum mismatch for {path}")
        stream.seek(0)
        return torch.load(stream, map_location="cpu", weights_only=True)


def check(name, module, weights, inputs, expected_shape):
    module.load_state_dict(weights, strict=True)
    module.eval()
    output = module(*inputs)
    if tuple(output.shape) != expected_shape or not torch.isfinite(output).all() or output.device.type != "cpu":
        raise ValueError(f"{name} returned an unexpected CPU result")
    print(f"{name}: {tuple(output.shape)}, {output.dtype}, range [{output.min():.5g}, {output.max():.5g}]")


@torch.inference_mode()
def verify(folder):
    print(f"PyTorch {torch.__version__}, Alibaba {MODEL.version}, device CPU")
    torch.manual_seed(42)
    generator = load_checkpoint(folder / MODEL.files[0].relative_path, MODEL.files[0].sha256)
    image = torch.rand(1, 3, 512, 512) * 2 - 1
    check("Skin smoothing", UNet(3, 3), generator["generator"], (image,), tuple(image.shape))
    del generator
    local = load_checkpoint(folder / MODEL.files[1].relative_path, MODEL.files[1].sha256)
    detection = torch.nn.functional.interpolate(image, size=(768, 768), mode="bilinear", align_corners=True)
    check("Blemish detection", DetectionUNet(n_channels=3, n_classes=1), local["detection_net"],
          (detection,), (1, 1, 768, 768))
    patch = torch.nn.functional.pad(image, (32, 32, 32, 32))
    mask = torch.ones_like(patch[:, :1])
    mask[:, :, 280:294, 280:294] = 0
    check("Blemish repair", RetouchingNet(in_channels=4, out_channels=3), local["inpainting_net"],
          (patch * mask, mask), tuple(patch.shape))


if __name__ == "__main__":
    from retouching_tool.config import load_config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Application configuration with the checkpoint folder")
    args = parser.parse_args()
    config = load_config(Path(__file__).resolve().parents[1], args.config)
    verify(config.checkpoints)
