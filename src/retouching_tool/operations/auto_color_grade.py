"""Whole-photo color grading through the local ModelScope CSRNet checkpoint."""

from ..errors import RetouchingError
from ..pipeline.assets import ModelFile, ModelManifest
from ..pipeline.definition import OperationDescriptor, Parameter
from ..pipeline.image_state import ImageState


MODEL = ModelManifest(
    "csrnet-color-grade", "60f19c2350d379bd4bb7a19da34be44fd451d829",
    (ModelFile("csrnet/60f19c23/pytorch_model.pt",
               "d9e9f3182b5bfa609f34c3c8322046438759585bf77c708cbe1b8a8761bc29ea"),),
    ("cpu", "cuda"), "https://www.modelscope.ai/models/iic/cv_csrnet_image-color-enhance-models",
    "The publisher declares Apache License 2.0 on the model card at the pinned checkpoint revision.")
DESCRIPTOR = OperationDescriptor(
    "retouch.auto_color_grade", 1, "Auto color grade",
    parameters=(Parameter("strength", "Strength", 0.5, 0, 1, preview_on_change=True),),
    changes_pixels=True, model_refs=(MODEL.ref,), backends=MODEL.backends, inference_runtime="pytorch")


class AutoColorGrade:
    def __init__(self, stage):
        self.strength = stage.parameter("strength")
        self.network = None

    def prepare(self, runtime):
        self.runtime = runtime
        if self.strength:
            try:
                from .csrnet import load_network
                self.network = load_network(runtime.checkpoints / MODEL.files[0].relative_path, runtime.backend)
            except Exception as exc:
                raise RetouchingError("AI_SESSION_FAILED", f"Could not load CSRNet on PyTorch {runtime.backend}. "
                                      "Check the model file and the prepared device.") from exc
            runtime.emit("AI_SESSION_READY", "Loaded CSRNet for auto color grade.",
                         model_version=MODEL.version, precision="float32")

    def process(self, state):
        if not self.strength:
            return state
        try:
            pixels = self.network.grade(state.pixels, self.strength)
        except Exception as exc:
            raise RetouchingError("AI_INFERENCE_FAILED", f"Auto color grade failed on PyTorch {self.runtime.backend}. "
                                  "Check device memory and the technical details.") from exc
        self.runtime.emit("AUTO_COLOR_GRADE_APPLIED", "Applied this photo's CSRNet color grade.",
                          strength=self.strength, model_version=MODEL.version)
        return ImageState.from_pixels(pixels, state.metadata, pixel_revision=state.pixel_revision + 1,
                                      geometry_revision=state.geometry_revision)

    def release(self):
        release_cuda = self.network is not None and self.runtime.backend == "cuda"
        self.network = self.runtime = None
        if release_cuda:
            import torch
            torch.cuda.empty_cache()
