"""Full-resolution Gaussian low pass and its neutral-centered high-pass residual."""

from math import ceil

from ..onnx_graph import float_value, initializer, model, node
from ..pipeline.definition import OperationDescriptor, Parameter
from ..pipeline.image_state import ImageState
from .onnx_session import open_session


RADIUS = Parameter("radius", "Radius (px)", 3.0, 0.5, 32.0)
LOW_PASS = OperationDescriptor(
    "core.low_pass", 1, "Low Pass", parameters=(RADIUS,), changes_pixels=True,
    inference_runtime="onnxruntime", backends=("cpu", "directml", "cuda"),
)
HIGH_PASS = OperationDescriptor(
    "core.high_pass", 1, "High Pass", parameters=(RADIUS,), changes_pixels=True,
    inference_runtime="onnxruntime", backends=("cpu", "directml", "cuda"),
)

TILE_HEIGHT = 512
TILE_WIDTH = 1024


def _filter_graph(radius, high_pass):
    """Apply a separable Gaussian per RGB channel to an NCHW tile with its halo."""
    import numpy as np

    halo = ceil(3 * radius)
    offsets = np.arange(-halo, halo + 1, dtype=np.float64)
    weights = np.exp(-0.5 * (offsets / radius) ** 2)
    # Express each 1-D blur as its center sample plus a weighted sum of
    # adjacent differences. A constant neighborhood then has exactly zero
    # correction, including under blend modes sensitive to exact equality.
    tail = np.cumsum(weights[:halo] / weights.sum())
    correction = np.concatenate((-tail, tail[::-1]))
    constants = [initializer("zero", 0.0), initializer("one", 1.0),
                 initializer("start", [halo]), initializer("end", [-halo])]
    nodes = []
    source = "tile"
    for axis, suffix, output in ((3, "x", "horizontal"), (2, "y", "blur")):
        shape = (3, 1, 1, -1) if axis == 3 else (3, 1, -1, 1)
        for name, kernel in (("diff", [-1.0, 1.0]), ("weight", correction)):
            constants.append(initializer(name + suffix, np.tile(kernel, (3, 1)).reshape(shape)))
        constants.append(initializer("axis" + suffix, [axis]))
        nodes.extend([node("Conv", [source, "diff" + suffix], "delta" + suffix, group=3),
                      node("Conv", ["delta" + suffix, "weight" + suffix], "edit" + suffix, group=3),
                      node("Slice", [source, "start", "end", "axis" + suffix], "center" + suffix),
                      node("Add", ["center" + suffix, "edit" + suffix], output)])
        source = output
    if high_pass:
        constants.extend([initializer("starts", [halo, halo]), initializer("ends", [-halo, -halo]),
                          initializer("axes", [2, 3]), initializer("half", 0.5)])
        nodes.extend([node("Slice", ["tile", "starts", "ends", "axes"], "original"),
                      node("Sub", ["original", "blur"], "detail"), node("Add", ["detail", "half"], "centered")])
    nodes.append(node("Clip", ["centered" if high_pass else "blur", "zero", "one"], "output"))
    return model("frequency-filter", nodes, [float_value("tile", [1, 3, "padded_h", "padded_w"])],
                 [float_value("output", [1, 3, "height", "width"])], constants)


class FrequencyFilter:
    def __init__(self, stage):
        self.radius = stage.parameter("radius")
        self.high_pass = stage.operation == HIGH_PASS.operation
        self.halo = ceil(3 * self.radius)

    def prepare(self, runtime):
        self.runtime = runtime
        self.session = open_session(_filter_graph(self.radius, self.high_pass), runtime.backend,
                                    cuda_options={"use_tf32": "0"})

    def release(self):
        self.session = self.runtime = None

    def process(self, state):
        import numpy as np

        pixels = state.pixels
        output = np.empty_like(pixels)
        height, width = min(state.height, TILE_HEIGHT), min(state.width, TILE_WIDTH)
        for y in range(0, state.height, height):
            rows = np.arange(y - self.halo, y + height + self.halo).clip(0, state.height - 1)
            for x in range(0, state.width, width):
                # Keep a consistent tensor shape at seams and replicate the true image border.
                cols = np.arange(x - self.halo, x + width + self.halo).clip(0, state.width - 1)
                tile = np.ascontiguousarray(pixels[rows[:, None], cols].transpose(2, 0, 1)[None])
                filtered, = self.session.run(["output"], {"tile": tile})
                dest = output[y:y + height, x:x + width]
                dest[:] = filtered[0, :, :dest.shape[0], :dest.shape[1]].transpose(1, 2, 0)

        result = ImageState.from_pixels(output, state.metadata, pixel_revision=state.pixel_revision + 1,
                                        geometry_revision=state.geometry_revision)
        self.runtime.emit("FREQUENCY_FILTER_APPLIED", "Applied the full-resolution frequency filter.",
                          radius_px=self.radius)
        return result
