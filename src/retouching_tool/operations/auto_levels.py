"""Measure black and white points on each incoming image and stretch its sRGB range."""

from ..imaging.transfer import encode_srgb_strip, srgb8_to_linear
from ..onnx_graph import FloatGraph
from ..pipeline.definition import ChoiceParameter, OperationDescriptor, Parameter
from ..pipeline.image_state import ImageState
from .color_curves import RGB_SHAPE, apply_curve, decode_srgb, encode_srgb
from .onnx_session import open_session


LINKED = "Linked RGB"
DESCRIPTOR = OperationDescriptor(
    "core.auto_levels", 1, "Auto Levels",
    parameters=(
        ChoiceParameter("mode", "Channels", LINKED, (LINKED, "Per channel"), preview_on_change=True),
        Parameter("shadow_clip", "Shadow clipping (%)", 0.1, 0, 5, preview_on_change=True),
        Parameter("highlight_clip", "Highlight clipping (%)", 0.1, 0, 5, preview_on_change=True),
    ),
    changes_pixels=True, inference_runtime="onnxruntime", backends=("cpu", "directml", "cuda"),
)
HISTOGRAM_BLOCK_PIXELS = 65_536
MIN_SPAN = 8  # Avoid stretching a few sRGB codes of noise across the entire display range.


def _measure(pixels, shadow_clip, highlight_clip):
    import numpy as np

    samples = pixels.reshape(-1, 3)
    counts = np.zeros((3, 256), dtype=np.int64)
    for start in range(0, len(samples), HISTOGRAM_BLOCK_PIXELS):
        codes = encode_srgb_strip(samples[start:start + HISTOGRAM_BLOCK_PIXELS])
        for channel in range(3):
            counts[channel] += np.bincount(codes[:, channel], minlength=256)
    cumulative = counts.cumsum(axis=1)
    # Zero clipping selects the observed extrema, including on very small images.
    ranks = (int(len(samples) * shadow_clip / 100) + 1,
             len(samples) - int(len(samples) * highlight_clip / 100))
    return np.array([np.searchsorted(channel, ranks) for channel in cumulative]).T


def _stretch_graph():
    graph = FloatGraph()
    op = graph.op
    codes = op("Mul", encode_srgb(graph, "rgb"), 255)
    unit = op("Clip", op("Div", op("Sub", codes, "black"), op("Sub", "white", "black")), 0, 1)
    linear = decode_srgb(graph, unit)
    linear = op("Where", op("LessOrEqual", "rgb", "linear_black"), 0,
                op("Where", op("GreaterOrEqual", "rgb", "linear_white"), 1, linear))
    identity = op("And", op("Equal", "black", 0), op("Equal", "white", 255))
    output = op("Where", identity, "rgb", linear)
    return graph.encode("auto-levels", {"rgb": RGB_SHAPE, "black": [3], "white": [3],
                                        "linear_black": [3], "linear_white": [3]}, output, RGB_SHAPE)


class AutoLevels:
    def __init__(self, stage):
        self.mode = stage.parameter("mode")
        self.shadow_clip = stage.parameter("shadow_clip")
        self.highlight_clip = stage.parameter("highlight_clip")
        self.session = None

    def prepare(self, runtime):
        self.runtime = runtime

    def release(self):
        self.session = self.runtime = None

    def process(self, state):
        import numpy as np

        black, white = _measure(state.pixels, self.shadow_clip, self.highlight_clip)
        flat = white - black < MIN_SPAN
        details = dict(mode=self.mode, measured_black=black.tolist(), measured_white=white.tolist(),
                       shadow_clip=self.shadow_clip, highlight_clip=self.highlight_clip)
        if flat.all():
            ranges = " · ".join(f"{channel} {lo}–{hi}" for channel, lo, hi in zip("RGB", black, white))
            self.runtime.emit("AUTO_LEVELS_LOW_CONTRAST", "Too little tonal variation to stretch this photo safely. "
                              "Review the stage input or reduce clipping.", level="WARNING",
                              result_caption=f"Measured levels · {ranges}", **details)
            return state
        if self.mode == LINKED:
            black.fill(black.min())
            white.fill(white.max())
            caption = f"Input levels · RGB {black[0]}–{white[0]}"
        else:
            black[flat], white[flat] = 0, 255
            caption = "Input levels · " + " · ".join(
                f"{channel} {lo}–{hi}" for channel, lo, hi in zip("RGB", black, white))
        details.update(input_black=black.tolist(), input_white=white.tolist(), result_caption=caption)
        if (black == 0).all() and (white == 255).all():
            self.runtime.emit("AUTO_LEVELS_UNCHANGED", "The photo already uses the output range.", **details)
            return state

        if self.session is None:
            self.session = open_session(_stretch_graph(), self.runtime.backend)
        pixels = apply_curve(self.session, state.pixels, black=black.astype(np.float32), white=white.astype(np.float32),
                             linear_black=srgb8_to_linear(black), linear_white=srgb8_to_linear(white))
        result = ImageState.from_pixels(pixels, state.metadata, pixel_revision=state.pixel_revision + 1,
                                        geometry_revision=state.geometry_revision)
        self.runtime.emit("AUTO_LEVELS_APPLIED", "Applied the black and white points measured for this photo.", **details)
        return result
