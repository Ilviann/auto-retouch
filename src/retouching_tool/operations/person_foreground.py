"""PP-HumanSeg foreground analysis with a fixed-size inference working set."""

from dataclasses import replace

from ..errors import RetouchingError
from ..pipeline.assets import ModelFile, ModelManifest
from ..pipeline.definition import OperationDescriptor
from ..pipeline.image_state import PERSON_FOREGROUND, ContextEntry, PersonForegroundMask
from .onnx_session import open_session

MODEL = ModelManifest(
    "opencv-pphumanseg-2023mar", "2023mar-fp32",
    (ModelFile("human_segmentation_pphumanseg_2023mar.onnx",
               "552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24"),),
    ("cpu", "directml", "cuda"),
    "https://huggingface.co/opencv/human_segmentation_pphumanseg/blob/main/human_segmentation_pphumanseg_2023mar.onnx",
    "Apache-2.0 for all files, including the ONNX weights: https://huggingface.co/opencv/human_segmentation_pphumanseg#license",
)
DESCRIPTOR = OperationDescriptor("analysis.person_foreground", 1, "Person foreground (AI)",
    provides=(PERSON_FOREGROUND,), model_refs=(MODEL.ref,), backends=MODEL.backends)
MODEL_SIZE = 192
SCORES = "save_infer_model/scale_0.tmp_1"


def _model_input(pixels):
    import numpy as np

    # Sample four sRGB neighbors per model pixel. Even a full-size portrait
    # needs only model-sized temporary arrays, including during color conversion.
    def sample_axis(length):
        center = (np.arange(MODEL_SIZE, dtype=np.float32) + .5) * (length / MODEL_SIZE) - .5
        center = np.clip(center, 0, length - 1)
        low = center.astype(np.intp)
        return low, np.minimum(low + 1, length - 1), center - low.astype(np.float32)

    def srgb(rows, cols):
        linear = pixels[rows[:, None], cols[None, :]]
        return np.where(linear <= .0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - .055)

    y0, y1, fy = sample_axis(pixels.shape[0])
    x0, x1, fx = sample_axis(pixels.shape[1])
    x, y = fx[None, :, None], fy[:, None, None]
    top = srgb(y0, x0) * (1 - x) + srgb(y0, x1) * x
    bottom = srgb(y1, x0) * (1 - x) + srgb(y1, x1) * x
    rgb = (top * (1 - y) + bottom * y) * 2 - 1
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None, ...], dtype=np.float32)


class PersonForeground:
    def __init__(self, stage):
        self.stage = stage
        self._session = None

    def prepare(self, runtime):
        self.runtime = runtime
        self._session = open_session(runtime.checkpoints / MODEL.files[0].relative_path, runtime.backend)
        inputs = self._session.get_inputs()
        if len(inputs) != 1 or inputs[0].type != "tensor(float)" or SCORES not in {
                output.name for output in self._session.get_outputs()}:
            raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "PP-HumanSeg has unexpected ONNX inputs or outputs.")
        self._input_name = inputs[0].name
        runtime.emit("AI_SESSION_READY", "Opened the local PP-HumanSeg inference session",
                     model_version=MODEL.version, registered_providers=self._session.get_providers(),
                     input_shape=(1, 3, MODEL_SIZE, MODEL_SIZE), precision="float32")

    def process(self, state):
        import numpy as np

        try:
            scores, = self._session.run([SCORES], {self._input_name: _model_input(state.pixels)})
        except Exception as exc:
            raise RetouchingError("AI_INFERENCE_FAILED", "PP-HumanSeg could not process this image. "
                                  "Check the device memory and inference error details.") from exc
        if (scores.shape != (1, 2, MODEL_SIZE, MODEL_SIZE) or scores.dtype != np.float32
                or not np.isfinite(scores).all()):
            raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "PP-HumanSeg returned invalid foreground scores.")
        labels = (scores[0, 1] > scores[0, 0]).tobytes()
        value = PersonForegroundMask(MODEL_SIZE, MODEL_SIZE, labels)
        stage = self.stage
        entry = ContextEntry(PERSON_FOREGROUND, stage.id, stage.operation, stage.operation_version,
                             state.pixel_revision, state.geometry_revision, state.width, state.height, value,
                             source_id=state.metadata.source_id, producer_settings_hash=stage.identity,
                             model_ref=stage.model_ref)
        if not labels.count(1):
            self.runtime.emit("PERSON_FOREGROUND_EMPTY", "PP-HumanSeg found no person foreground. Inspect the analysis preview.",
                              level="WARNING")
        return replace(state, context=tuple(e for e in state.context if e.spec != PERSON_FOREGROUND) + (entry,))

    def release(self):
        self._session = None
        self.runtime = None
