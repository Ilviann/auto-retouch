"""Whole-frame IAT lighting estimation with a native-resolution correction."""

from ..config import ScalingSettings
from ..errors import RetouchingError
from ..imaging.scaling import fit_longest_side, resize_rgb
from ..pipeline.assets import ModelFile, ModelManifest
from ..pipeline.definition import IntegerParameter, OperationDescriptor, SCALING_PARAMETERS
from ..pipeline.image_state import ImageState
from .onnx_session import open_session


REVISION = "6a2bfefd823bbe167fbbfcba13296899ea398afc"
MODEL = ModelManifest(
    "iat-exposure", REVISION,
    (ModelFile("iat-exposure/6a2bfef/iat_exposure.onnx",
               "bd16c959a336b99ff15bef071ad6e9b081017241e5575d9e391653b676f2a5c6"),
     ModelFile("iat-exposure/6a2bfef/iat_exposure.onnx.data",
               "f92c0ab92266a096d117d3747fbbce244ca91e8a3f28f70db5b2048f478418a8")),
    ("cpu", "directml", "cuda"),
    f"https://huggingface.co/eleasm/IAT-ONNX/tree/{REVISION}/onnx",
    "The eleasm/IAT-ONNX model card declares Apache-2.0 for the published exposure conversion.",
)
DESCRIPTOR = OperationDescriptor(
    "retouch.iat_exposure", 1, "Adaptive exposure correction (IAT)",
    parameters=(IntegerParameter("analysis_size", "Analysis longest side (px)", 512, 128, 1024),
                *SCALING_PARAMETERS),
    changes_pixels=True, model_refs=(MODEL.ref,), backends=MODEL.backends,
)


def _linear(rgb):
    import numpy as np

    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


class AdaptiveExposure:
    def __init__(self, stage):
        self.limit = stage.parameter("analysis_size")
        self.methods = ScalingSettings(**{k: v for k, v in stage.portable_parameters().items()
                                          if k in ("downscale", "upscale")})
        self.session = None
        self.session_size = None

    def prepare(self, runtime):
        self.runtime = runtime

    def _session_for(self, size):
        if size == self.session_size:
            return self.session
        # Fix the shape expressions at load time so DirectML can own the graph.
        # Release the previous shape before allocating another during a staged pass.
        self.session = self.session_size = None
        width, height = size
        session = open_session(self.runtime.checkpoints / MODEL.files[0].relative_path, self.runtime.backend,
                               free_dimensions={"batch": 1, "height": height, "width": width})
        inputs = session.get_inputs()
        if (len(inputs) != 1 or inputs[0].type != "tensor(float)" or inputs[0].shape != [1, 3, height, width]
                or not any(output.name == "enhanced" and output.type == "tensor(float)"
                           for output in session.get_outputs())):
            raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "Check the IAT Exposure revision. "
                                  "Expected one float32 RGB input and an enhanced float32 output.")
        self.input_name = inputs[0].name
        self.session, self.session_size = session, size
        self.runtime.emit("AI_SESSION_READY", "Opened the local IAT Exposure model.", model_version=MODEL.version,
                          registered_providers=session.get_providers(), analysis_size=size)
        return session

    def process(self, state):
        import numpy as np

        native_size = (state.width, state.height)
        size = fit_longest_side(native_size, self.limit)
        session = self._session_for(size)
        guide = resize_rgb(state.pixels, size, self.methods, encode_srgb=True)
        tensor = np.ascontiguousarray(guide.transpose(2, 0, 1)[None])
        try:
            enhanced, = session.run(["enhanced"], {self.input_name: tensor})
        except Exception as exc:
            raise RetouchingError("AI_INFERENCE_FAILED", f"IAT Exposure failed on {self.runtime.backend} "
                                  f"at {size[0]} × {size[1]}. Check the device error and analysis size.") from exc
        if enhanced.shape != tensor.shape or enhanced.dtype != np.float32 or not np.isfinite(enhanced).all():
            raise RetouchingError("AI_MODEL_CONTRACT_INVALID", "IAT Exposure returned invalid RGB samples. "
                                  "Check the model revision and inference backend.")
        edited = _linear(np.clip(enhanced[0].transpose(1, 2, 0), 0, 1))
        if size != native_size:
            # Transfer only the lighting difference. The source supplies native detail.
            delta = edited - _linear(guide)
            edited = resize_rgb(delta, native_size, self.methods)
            np.add(edited, state.pixels, out=edited)
            np.clip(edited, 0, 1, out=edited)
        self.runtime.emit("IAT_EXPOSURE_APPLIED", "Applied the adaptive exposure correction.",
                          model_version=MODEL.version, analysis_size=size, image_size=native_size)
        return ImageState.from_pixels(edited, state.metadata, pixel_revision=state.pixel_revision + 1,
                                      geometry_revision=state.geometry_revision)

    def release(self):
        self.session = self.session_size = self.runtime = None
