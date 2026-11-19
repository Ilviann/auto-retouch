"""MediaPipe SelfieMulticlass v1, locally evaluated using the published ONNX conversion."""

from dataclasses import replace

from ..config import ScalingSettings
from ..errors import RetouchingError
from ..imaging.scaling import resize_rgb
from ..pipeline.assets import ModelFile, ModelManifest
from ..pipeline.definition import OperationDescriptor, SCALING_PARAMETERS
from ..pipeline.image_state import EXPOSED_SKIN, ContextEntry, ExposedSkinMask
from .onnx_session import open_session


PUBLISHER = "https://huggingface.co/senty-au/selfie_multiclass_256x256-ONNX"
MODEL = ModelManifest(
    "selfie-multiclass-256x256-onnx", "float32-v1-opset17",
    (ModelFile("selfie-multiclass-256x256/model.onnx",
               "35ec1ecd9ee7f85073c99c00020b7f6751b69506eeacf683bc8665f6117f85b0"),),
    ("cpu", "directml", "cuda"), PUBLISHER,
    f"The conversion publisher explicitly identifies the Google weights as Apache-2.0: {PUBLISHER}#license",
)
DESCRIPTOR = OperationDescriptor("analysis.exposed_skin", 1, "Exposed Skin Mask",
    parameters=SCALING_PARAMETERS, provides=(EXPOSED_SKIN,), model_refs=(MODEL.ref,), backends=MODEL.backends)
SIZE = 256


class ExposedSkin:
    def __init__(self, stage):
        self.stage = stage
        self.methods = ScalingSettings(**stage.portable_parameters())
        self.session = None

    def prepare(self, runtime):
        self.runtime = runtime
        self.session = open_session(runtime.checkpoints / MODEL.files[0].relative_path, runtime.backend)
        for nodes, name, channels in ((self.session.get_inputs(), "input_29", 3),
                                      (self.session.get_outputs(), "Identity", 6)):
            if (len(nodes) != 1 or nodes[0].name != name or nodes[0].type != "tensor(float)"
                    or nodes[0].shape != [1, SIZE, SIZE, channels]):
                raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "Unexpected SelfieMulticlass tensor signature. "
                                      "Recheck the required float32 ONNX conversion.")
        runtime.emit("AI_SESSION_READY", "Opened the local exposed skin segmentation model.",
                     model_version=MODEL.version, registered_providers=self.session.get_providers())

    def process(self, state):
        import numpy as np

        rgb = resize_rgb(state.pixels, (SIZE, SIZE), self.methods, encode_srgb=True)
        try:
            scores, = self.session.run(["Identity"], {"input_29": rgb[None]})
        except Exception as exc:
            raise RetouchingError("AI_INFERENCE_FAILED", "Exposed skin analysis failed. "
                                  "Inspect the inference error and device memory.") from exc
        if (not isinstance(scores, np.ndarray) or scores.dtype != np.float32
                or scores.shape != (1, SIZE, SIZE, 6) or not np.isfinite(scores).all()):
            raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "Exposed skin analysis returned invalid class scores.")
        # Argmax is invariant under softmax. Keep only body (2) and face (3).
        classes = np.argmax(scores[0], axis=-1)
        labels = np.array([0, 0, 1, 2, 0, 0], dtype=np.uint8)[classes].tobytes()
        mask = ExposedSkinMask(SIZE, SIZE, labels)
        stage = self.stage
        entry = ContextEntry(EXPOSED_SKIN, stage.id, stage.operation, stage.operation_version,
                             state.pixel_revision, state.geometry_revision, state.width, state.height, mask,
                             source_id=state.metadata.source_id, producer_settings_hash=stage.identity,
                             model_ref=stage.model_ref)
        if not any(labels):
            self.runtime.emit("EXPOSED_SKIN_EMPTY", "No exposed skin was detected. Inspect the mask preview.",
                              level="WARNING")
        return replace(state, context=tuple(e for e in state.context if e.spec != EXPOSED_SKIN) + (entry,))

    def release(self):
        self.session = None
        self.runtime = None
