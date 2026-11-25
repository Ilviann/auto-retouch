"""Manual Levels on continuous sRGB values, with linear float32 input and output."""

from ..imaging.transfer import srgb8_to_linear
from ..onnx_graph import FloatGraph
from ..pipeline.definition import ChoiceParameter, IntegerParameter, OperationDescriptor, Parameter
from ..pipeline.image_state import ImageState
from .color_curves import RGB_SHAPE, apply_curve, decode_srgb, encode_srgb
from .onnx_session import open_session


CHANNELS = ("RGB", "Red", "Green", "Blue")
DESCRIPTOR = OperationDescriptor(
    "core.levels", 1, "Levels",
    parameters=(
        ChoiceParameter("channel", "Channel", "RGB", CHANNELS, preview_on_change=True),
        IntegerParameter("input_black", "Input black", 0, 0, 254, preview_on_change=True),
        Parameter("gamma", "Midtone gamma", 1.0, 0.1, 10.0, preview_on_change=True),
        IntegerParameter("input_white", "Input white", 255, 1, 255, preview_on_change=True),
        IntegerParameter("output_black", "Output black", 0, 0, 255, preview_on_change=True),
        IntegerParameter("output_white", "Output white", 255, 0, 255, preview_on_change=True),
    ),
    ordered_parameter_pairs=(("input_black", "input_white"),),
    input_histogram=True,
    changes_pixels=True, inference_runtime="onnxruntime", backends=("cpu", "directml", "cuda"),
)


def levels_graph(channel, input_black, input_white, gamma, output_black, output_white):
    graph = FloatGraph()
    op = graph.op
    source = "rgb"
    shape = RGB_SHAPE if channel == "RGB" else ["pixels", 1]
    black, white, low, high = srgb8_to_linear([input_black, input_white, output_black, output_white])
    # A clamp can leave channels exactly unchanged. The shared blend modes use
    # this distinction to keep untouched pixels intact.
    if low == high or (gamma == 1 and input_black == output_black and input_white == output_white):
        return graph.encode("levels", {source: shape}, op("Clip", source, low, high), shape)

    encoded = encode_srgb(graph, source)
    unit = op("Clip", op("Div", op("Sub", op("Mul", encoded, 255), input_black),
                         input_white - input_black), 0, 1)
    shaped = op("Pow", unit, 1 / gamma)
    mapped = op("Add", output_black / 255, op("Mul", shaped, (output_white - output_black) / 255))
    linear = decode_srgb(graph, mapped)
    # Compare endpoints in the decoder's working space. An encode roundoff just
    # above black can otherwise become visible when raised to a small exponent.
    linear = op("Where", op("LessOrEqual", source, black), low,
                op("Where", op("GreaterOrEqual", source, white), high, linear))
    output = op("Clip", linear, 0, 1)
    return graph.encode("levels", {source: shape}, output, shape)


class Levels:
    def __init__(self, stage):
        self.settings = {p.name: stage.parameter(p.name) for p in DESCRIPTOR.parameters}
        self.neutral = all(self.settings[p.name] == p.default for p in DESCRIPTOR.parameters if p.name != "channel")
        self.session = None

    def prepare(self, runtime):
        self.runtime = runtime

    def process(self, state):
        if self.neutral:
            self.runtime.emit("LEVELS_UNCHANGED", "Levels controls are neutral.")
            return state
        if self.session is None:
            self.session = open_session(levels_graph(**self.settings), self.runtime.backend)
        channel = self.settings["channel"]
        index = None if channel == "RGB" else CHANNELS.index(channel) - 1
        pixels = apply_curve(self.session, state.pixels, channel_index=index)
        self.runtime.emit("LEVELS_APPLIED", "Applied manual levels.", **self.settings)
        return ImageState.from_pixels(pixels, state.metadata, pixel_revision=state.pixel_revision + 1,
                                      geometry_revision=state.geometry_revision)

    def release(self):
        self.session = self.runtime = None
