"""Local YuNet detection and mapping to oriented, half-open face rectangles."""

from dataclasses import replace

from ..config import ScalingSettings
from ..errors import RetouchingError
from ..imaging.scaling import resize_rgb
from ..pipeline.assets import ModelFile, ModelManifest
from ..pipeline.definition import IntegerParameter, OperationDescriptor, Parameter, SCALING_PARAMETERS
from ..pipeline.image_state import FACE_REGIONS, ContextEntry, FaceRegion, FaceRegions
from .onnx_session import open_session


MODEL = ModelManifest(
    "opencv-yunet-2023mar", "2023mar-fp32",
    (ModelFile("face_detection_yunet_2023mar.onnx",
               "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),),
    ("cpu", "directml", "cuda"),
    "https://huggingface.co/opencv/face_detection_yunet/blob/main/face_detection_yunet_2023mar.onnx",
    "MIT for all directory files, including the checkpoint: https://huggingface.co/opencv/face_detection_yunet#license",
)
DESCRIPTOR = OperationDescriptor(
    "analysis.face_detection", 1, "Face detection (YuNet)",
    parameters=(Parameter("threshold", "Minimum confidence", .80, .1, 1),
                IntegerParameter("expand_pixels", "Expand each side (px)", 0, 0, 65535), *SCALING_PARAMETERS),
    provides=(FACE_REGIONS,), model_refs=(MODEL.ref,), backends=MODEL.backends,
)
SIZE = 640
STRIDES = (8, 16, 32)
OUTPUTS = tuple(f"{head}_{stride}" for stride in STRIDES for head in ("cls", "obj", "bbox"))


def _input(state, methods):
    import numpy as np

    ratio = SIZE / max(state.width, state.height)
    size = (max(1, round(state.width * ratio)), max(1, round(state.height * ratio)))
    rgb = resize_rgb(state.pixels, size, methods, encode_srgb=True)
    tensor = np.zeros((1, 3, SIZE, SIZE), dtype=np.float32)
    tensor[0, :, :size[1], :size[0]] = rgb[:, :, ::-1].transpose(2, 0, 1) * 255
    return tensor, size


def _decode(outputs, size, original, threshold, expand_pixels):
    import numpy as np

    candidates, confidences = [], []
    for i, stride in enumerate(STRIDES):
        cls, obj, offsets = outputs[3*i:3*i+3]
        side = SIZE // stride
        for head, channels in ((cls, 1), (obj, 1), (offsets, 4)):
            if (head.shape != (1, side * side, channels) or head.dtype != np.float32
                    or not np.isfinite(head).all()):
                raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "YuNet returned invalid face detection tensors.")
        scores = np.sqrt(np.clip(cls.ravel(), 0, 1) * np.clip(obj.ravel(), 0, 1))
        indices = np.flatnonzero(scores >= threshold)
        values = offsets[0, indices].astype(np.float64)
        center = (np.column_stack((indices % side, indices // side)) + values[:, :2]) * stride
        # Values above this bound already span the entire model input.
        radius = np.exp(np.minimum(values[:, 2:], 20)) * (stride / 2)
        lower = np.clip(center - radius, 0, size)
        upper = np.clip(center + radius, 0, size)
        valid = (upper > lower).all(axis=1)
        candidates.append(np.column_stack((lower, upper))[valid])
        confidences.append(scores[indices][valid])

    boxes = np.concatenate(candidates)
    order = np.argsort(-np.concatenate(confidences), kind="stable")
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    scale = np.array(original) / size
    faces = []
    while order.size:
        index, rest = order[0], order[1:]
        box = boxes[index]
        lower = np.maximum(0, np.floor(box[:2] * scale).astype(int) - expand_pixels)
        upper = np.minimum(original, np.ceil(box[2:] * scale).astype(int) + expand_pixels)
        faces.append(FaceRegion(*(int(n) for n in (*lower, *upper))))
        if len(faces) > 256:
            raise RetouchingError("FACE_REGION_LIMIT", "Detected over 256 faces. Increase minimum confidence.")
        overlap = np.maximum(0, np.minimum(box[2:], boxes[rest, 2:]) - np.maximum(box[:2], boxes[rest, :2]))
        intersection = overlap.prod(axis=1)
        order = rest[intersection <= .3 * (area[index] + area[rest] - intersection)]
    return FaceRegions(tuple(faces))


class FaceDetection:
    def __init__(self, stage):
        self.stage = stage
        self.methods = ScalingSettings(**{k: v for k, v in stage.portable_parameters().items()
                                          if k in ("downscale", "upscale")})
        self.session = None

    def prepare(self, runtime):
        self.runtime = runtime
        self.session = open_session(runtime.checkpoints / MODEL.files[0].relative_path, runtime.backend)
        inputs = self.session.get_inputs()
        if (len(inputs) != 1 or inputs[0].type != "tensor(float)" or inputs[0].shape != [1, 3, SIZE, SIZE]
                or not set(OUTPUTS) <= {output.name for output in self.session.get_outputs()}):
            raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "YuNet has unexpected ONNX inputs or outputs.")
        self.input_name = inputs[0].name
        runtime.emit("AI_SESSION_READY", "Opened the local YuNet face detector.", model_version=MODEL.version,
                     registered_providers=self.session.get_providers())

    def process(self, state):
        tensor, size = _input(state, self.methods)
        try:
            outputs = self.session.run(list(OUTPUTS), {self.input_name: tensor})
        except Exception as exc:
            raise RetouchingError("AI_INFERENCE_FAILED", "YuNet inference failed. Inspect the device error details.") from exc
        stage = self.stage
        expansion = stage.parameter("expand_pixels")
        faces = _decode(outputs, size, (state.width, state.height), stage.parameter("threshold"), expansion)
        entry = ContextEntry(FACE_REGIONS, stage.id, stage.operation, stage.operation_version, state.pixel_revision,
                             state.geometry_revision, state.width, state.height, faces, source_id=state.metadata.source_id,
                             producer_settings_hash=stage.identity, model_ref=stage.model_ref)
        if faces.regions:
            self.runtime.emit("FACES_DETECTED", "Detected face regions.", count=len(faces.regions), expand_pixels=expansion)
        else:
            self.runtime.emit("FACE_DETECTION_EMPTY", "No faces detected. Inspect the preview and minimum confidence.",
                              level="WARNING")
        return replace(state, context=tuple(e for e in state.context if e.spec != FACE_REGIONS) + (entry,))

    def release(self):
        self.session = None
        self.runtime = None
