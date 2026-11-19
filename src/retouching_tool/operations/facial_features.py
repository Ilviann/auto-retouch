"""Locate eye, brow and lip contours on oriented YuNet face crops using FaceMesh V2."""

from dataclasses import replace

from ..config import ScalingSettings
from ..errors import RetouchingError
from ..imaging.scaling import resize_rgb
from ..pipeline.assets import ModelFile, ModelManifest
from ..pipeline.definition import OperationDescriptor, SCALING_PARAMETERS
from ..pipeline.image_state import FACIAL_FEATURES, FACE_REGIONS, ContextEntry, FacialFeatureMask, ProtectedFace
from .onnx_session import open_session


PUBLISHER = "https://huggingface.co/senty-au/face_landmarks_detector-ONNX"
MODEL = ModelManifest(
    "mediapipe-face-mesh-v2", "float16-bundle-v1-onnx-opset17",
    (ModelFile("mediapipe/face_mesh_v2/model.onnx",
               "7d6e82dee82a1dca5fbddb282b3cc74571833a530de317fc22ae325c3358beeb"),),
    ("cpu", "directml", "cuda"), PUBLISHER,
    f"Google FaceMesh V2 weights and the ONNX conversion are Apache-2.0: {PUBLISHER}#license",
)
DESCRIPTOR = OperationDescriptor(
    "analysis.protect_facial_features", 1, "Protect facial features", parameters=SCALING_PARAMETERS,
    requires=(FACE_REGIONS,), provides=(FACIAL_FEATURES,), model_refs=(MODEL.ref,), backends=MODEL.backends,
)
SIZE = 256
# Closed paths along the MediaPipe Face Mesh eye, eyebrow and outer lip connections.
CONTOURS = (
    (33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246),
    (263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466),
    (70, 63, 105, 66, 107, 55, 65, 52, 53, 46),
    (336, 296, 334, 293, 300, 276, 283, 282, 295, 285),
    (61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291, 375, 321, 405, 314, 17, 84, 181, 91, 146),
)


def _face_contours(state, face, methods, session):
    import numpy as np

    box = (face.left, face.top, face.right, face.bottom)
    side = 1.5 * max(face.right - face.left, face.bottom - face.top)
    left = (face.left + face.right - side) / 2
    top = (face.top + face.bottom - side) / 2
    rgb = resize_rgb(state.pixels, (SIZE, SIZE), methods, encode_srgb=True,
                     box=(left, top, left + side, top + side))
    try:
        points, presence = session.run(["Identity", "Identity_1"], {"input_12": rgb[None]})
    except Exception as exc:
        raise RetouchingError("AI_INFERENCE_FAILED", "FaceMesh inference failed. Inspect the device error details.") from exc
    for tensor, shape in ((points, (1, 1, 1, 1434)), (presence, (1, 1, 1, 1))):
        if (not isinstance(tensor, np.ndarray) or tensor.shape != shape or tensor.dtype != np.float32
                or not np.isfinite(tensor).all()):
            raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "FaceMesh returned invalid landmarks or presence scores.")
    # A nonpositive logit means the model assigns at most 50% face presence.
    if presence.item() <= 0:
        return ProtectedFace(box, ())
    xy = points.reshape(478, 3)[:, :2].astype(np.float64) * (side / SIZE) + (left, top)
    # A crop can include a neighbouring person. Its predicted nose must belong to this detection.
    nose_x, nose_y = xy[1]
    if not (face.left <= nose_x < face.right and face.top <= nose_y < face.bottom):
        return ProtectedFace(box, ())
    polygons = []
    for indices in CONTOURS:
        contour = xy[list(indices)]
        if ((contour < 0).any() or (contour >= (state.width, state.height)).any()
                or np.ptp(contour[:, 0]) < 1):
            return ProtectedFace(box, ())
        polygons.append(tuple(int(n) for n in np.floor(contour).ravel()))
    return ProtectedFace(box, tuple(polygons))


class ProtectFacialFeatures:
    def __init__(self, stage):
        self.stage = stage
        self.methods = ScalingSettings(**stage.portable_parameters())
        self.session = None

    def prepare(self, runtime):
        self.runtime = runtime
        self.session = open_session(runtime.checkpoints / MODEL.files[0].relative_path, runtime.backend)
        inputs = self.session.get_inputs()
        outputs = {node.name: node for node in self.session.get_outputs()}

        def matches(node, dimensions):
            return (node is not None and node.type == "tensor(float)" and node.shape[1:] == dimensions
                    and (node.shape[0] in (None, 1) or isinstance(node.shape[0], str)))

        if (len(inputs) != 1 or inputs[0].name != "input_12" or not matches(inputs[0], [SIZE, SIZE, 3])
                or not matches(outputs.get("Identity"), [1, 1, 1434])
                or not matches(outputs.get("Identity_1"), [1, 1, 1])):
            raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "Unexpected FaceMesh V2 tensor signature. Check the required ONNX conversion.")
        runtime.emit("AI_SESSION_READY", "Opened the local FaceMesh V2 landmark model.",
                     registered_providers=self.session.get_providers(), model_version=MODEL.version)

    def process(self, state):
        regions = state.entry(FACE_REGIONS.key).value.regions
        faces = tuple(_face_contours(state, face, self.methods, self.session) for face in regions)
        skipped = sum(not face.contours for face in faces)
        if skipped:
            self.runtime.emit("FACIAL_FEATURES_UNCERTAIN",
                              f"Protected {skipped} entire face box(es) because landmarks were unreliable. Inspect the overlay.",
                              level="WARNING", detected=len(faces), uncertain=skipped)
        if not faces:
            self.runtime.emit("FACIAL_FEATURES_NO_FACES", "No face boxes. Protected shine reduction will use only body skin. Check face detection.",
                              level="WARNING")
        self.runtime.emit("FACIAL_FEATURES_ANALYZED", "Located facial protection regions.", detected=len(faces),
                          landmarked=len(faces) - skipped)
        stage = self.stage
        entry = ContextEntry(FACIAL_FEATURES, stage.id, stage.operation, stage.operation_version,
                             state.pixel_revision, state.geometry_revision, state.width, state.height,
                             FacialFeatureMask(faces), source_id=state.metadata.source_id,
                             producer_settings_hash=stage.identity, model_ref=stage.model_ref)
        return replace(state, context=tuple(e for e in state.context if e.spec.key != FACIAL_FEATURES.key) + (entry,))

    def release(self):
        self.session = None
        self.runtime = None
