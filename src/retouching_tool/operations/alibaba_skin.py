"""Input geometry and compositing for the Alibaba skin-retouching adapter."""

from dataclasses import dataclass

from ..errors import ResizeApprovalRequired, RetouchingError
from ..config import ScalingSettings
from ..imaging.scaling import fit_longest_side, resize_rgb
from ..pipeline.assets import ModelFile, ModelManifest
from ..pipeline.definition import OperationDescriptor, Parameter, ResizeLimit, SCALING_PARAMETERS
from ..pipeline.image_state import FACE_REGIONS, FaceRegion, ImageState

MAX_INPUT_SIDE = 5000
MODEL = ModelManifest(
    "alibaba-skin-retouching", "16c8351a01c90746eb702e6e6c1e6a28d0e5ff74",
    (ModelFile("alibaba-skin-retouching/16c8351a/pytorch_model.pt",
               "760d0c0b95c4e0a692165677fe2b82dbdf8fbbd4a3780ca966599c627b082993"),
     ModelFile("alibaba-skin-retouching/16c8351a/joint_20210926.pth",
               "c9e1388b19d9334c90db08308c86de208f535920ee381ae8a9cb63484a78351a")),
    ("cpu",), "https://www.modelscope.ai/models/iic/cv_unet_skin_retouching_torch",
    "Publisher model repository license: Apache License 2.0. "
    "https://www.modelscope.ai/models/iic/cv_unet_skin_retouching_torch/resolve/master/README.md")
DESCRIPTOR = OperationDescriptor("retouch.alibaba_skin", 1, "Alibaba skin retouching",
    parameters=(Parameter("strength", "Strength", 1, 0, 1), *SCALING_PARAMETERS),
    optional=(FACE_REGIONS,), preserves=(FACE_REGIONS.key,), changes_pixels=True,
    model_refs=(MODEL.ref,), backends=MODEL.backends, inference_runtime="pytorch",
    resize_limit=ResizeLimit(MAX_INPUT_SIDE, bypass_if_zero="strength"))


@dataclass(frozen=True)
class FaceCrop:
    """Half-open bounds in the current oriented image, with optional crop-sized boolean mask."""

    left: int
    top: int
    right: int
    bottom: int
    mask: object | None = None
    mask_image_size: tuple[int, int] | None = None

    @classmethod
    def from_mask(cls, mask):
        """Find tight bounds for one upstream face mask in oriented-image coordinates."""
        import numpy as np

        if not isinstance(mask, np.ndarray) or mask.dtype != np.bool_ or mask.ndim != 2:
            raise RetouchingError("FACE_REGION_INVALID", "Expected a two-dimensional boolean face mask.")
        rows = np.flatnonzero(mask.any(axis=1))
        cols = np.flatnonzero(mask.any(axis=0))
        if not len(rows):
            raise RetouchingError("FACE_REGION_INVALID", "A per-face mask must contain face pixels.")
        left, right = int(cols[0]), int(cols[-1]) + 1
        top, bottom = int(rows[0]), int(rows[-1]) + 1
        return cls(left, top, right, bottom, mask[top:bottom, left:right], mask.shape[::-1])

    @property
    def size(self):
        return self.right - self.left, self.bottom - self.top

    @property
    def processing_size(self):
        return fit_longest_side(self.size, MAX_INPUT_SIDE)

    def validate(self, width, height):
        import numpy as np

        if (any(type(v) is not int for v in (self.left, self.top, self.right, self.bottom))
                or not 0 <= self.left < self.right <= width or not 0 <= self.top < self.bottom <= height):
            raise RetouchingError("FACE_REGION_INVALID", "Face bounds must fall inside the current oriented image.")
        if isinstance(self.mask, FaceRegion):
            if (self.left, self.top, self.right, self.bottom) != (
                    self.mask.left, self.mask.top, self.mask.right, self.mask.bottom):
                raise RetouchingError("FACE_REGION_INVALID", "Face mask belongs to a different rectangle.")
        elif self.mask is not None and (not isinstance(self.mask, np.ndarray) or self.mask.dtype != np.bool_
                                      or self.mask.shape != self.size[::-1]
                                      or self.mask_image_size is not None and self.mask_image_size != (width, height)):
            raise RetouchingError("FACE_REGION_INVALID", "Supply a boolean face mask aligned to its crop.")


def retouch_regions(pixels, face_crops, *, methods, resize_approved, infer, emit, stage_id=None):
    """Run a linear-RGB crop processor against full resolution or validated upstream regions.

    The caller owns the model and supplies the stage-scoped approval and diagnostic emitter.
    All regions are checked before inference, so retrying with approval repeats no partial edits.
    """
    import numpy as np

    height, width = pixels.shape[:2]
    crops = (FaceCrop(0, 0, width, height),) if face_crops is None else tuple(face_crops)
    if not crops:
        emit("RETOUCH_NO_FACES", "The upstream stage found no faces.", level="WARNING")
        return pixels
    for crop in crops:
        crop.validate(width, height)
    for index, crop in enumerate(crops):
        if crop.processing_size != crop.size:
            details = dict(region_index=index, input_size=crop.size, processing_size=crop.processing_size)
            message = (f"Alibaba skin retouching: resize {crop.size[0]} × {crop.size[1]} to {crop.processing_size[0]} × "
                       f"{crop.processing_size[1]} for retouching and restore the original dimensions. "
                       "Fine detail may be lost.")
            if resize_approved is not True:
                emit("RETOUCH_RESIZE_APPROVAL_REQUIRED", message, level="WARNING", **details)
                raise ResizeApprovalRequired(stage_id, message)
            emit("RETOUCH_RESIZE_APPROVED", message, level="WARNING", **details)

    output = pixels.copy() if face_crops is not None else None
    for crop in crops:
        area = (slice(crop.top, crop.bottom), slice(crop.left, crop.right))
        source = pixels[area]
        reduced = crop.processing_size != crop.size
        model_input = resize_rgb(source, crop.processing_size, methods) if reduced else source
        edited = infer(model_input)
        if not isinstance(edited, np.ndarray) or edited.shape != model_input.shape or edited.dtype != np.float32:
            raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "Expected a float32 RGB result matching the input crop.")
        for row in range(0, edited.shape[0], 64):
            strip = edited[row:row + 64]
            if not np.isfinite(strip).all() or strip.min() < 0 or strip.max() > 1:
                raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "Retouching returned invalid RGB samples.")
        if reduced:
            edited = resize_rgb(edited, crop.size, methods)
        if output is None:
            return edited
        if crop.mask is None:
            output[area] = edited
        elif isinstance(crop.mask, FaceRegion):
            region = crop.mask
            mask = np.unpackbits(np.frombuffer(region.mask, dtype=np.uint8),
                                 count=region.mask_width * region.mask_height).reshape(region.mask_height, region.mask_width)
            width, height = crop.size
            xs = (2 * np.arange(width) + 1) * region.mask_width // (2 * width)
            for row in range(0, height, 64):
                rows = slice(row, min(row + 64, height))
                ys = (2 * np.arange(rows.start, rows.stop) + 1) * region.mask_height // (2 * height)
                np.copyto(output[area][rows], edited[rows], where=mask[ys[:, None], xs].astype(bool)[:, :, None])
        else:
            np.copyto(output[area], edited, where=crop.mask[:, :, None])
        del model_input, edited
    return output


class AlibabaSkin:
    def __init__(self, stage):
        self.strength = stage.parameter("strength")
        params = stage.portable_parameters()
        self.methods = ScalingSettings(**{key: params[key] for key in ("downscale", "upscale") if key in params})
        self.networks = None

    def prepare(self, runtime):
        from .alibaba_inference import SkinNetworks
        self.runtime = runtime
        if self.strength:
            try:
                self.networks = SkinNetworks(*(runtime.checkpoints / f.relative_path for f in MODEL.files))
            except Exception as exc:
                raise RetouchingError("AI_SESSION_FAILED", "Could not load the Alibaba checkpoints on PyTorch CPU. "
                                      "Check the model files and technical details.") from exc
            runtime.emit("AI_SESSION_READY", "Loaded the Alibaba smoothing, blemish detection and repair networks.",
                         model_version=MODEL.version, precision="float32")

    def process(self, state):
        if not self.strength:
            return state
        entry = state.entry(FACE_REGIONS.key)
        crops = None if entry is None else tuple(
            FaceCrop(r.left, r.top, r.right, r.bottom, r if r.mask else None) for r in entry.value.regions)
        try:
            pixels = retouch_regions(state.pixels, crops, methods=self.methods,
                                     resize_approved=self.runtime.resize_approved,
                                     infer=lambda rgb: self.networks.apply(rgb, self.methods, self.strength),
                                     emit=self.runtime.emit, stage_id=self.runtime.stage.id)
        except RetouchingError:
            raise
        except Exception as exc:
            raise RetouchingError("AI_INFERENCE_FAILED", "Alibaba skin retouching failed on CPU. "
                                  "Inspect the stage diagnostics and available memory.") from exc
        if pixels is state.pixels:
            return state
        return ImageState.from_pixels(pixels, state.metadata, pixel_revision=state.pixel_revision + 1,
                                      geometry_revision=state.geometry_revision)

    def release(self):
        self.networks = None
        self.runtime = None
