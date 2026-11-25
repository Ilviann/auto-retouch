"""Bounded automatic exposure for oriented linear-sRGB images."""

import math

from ..pipeline.definition import OperationDescriptor, Parameter
from ..pipeline.image_state import ImageState
from .color_curves import apply_curve, exposure_graph
from .onnx_session import open_session


DESCRIPTOR = OperationDescriptor(
    "core.auto_exposure", 1, "Auto exposure and tone",
    parameters=(
        Parameter("strength", "Strength (0–1)", 0.5, 0, 1, preview_on_change=True),
        Parameter("max_stops", "Maximum correction (stops)", 1.0, 0, 2, preview_on_change=True),
    ),
    changes_pixels=True,
    inference_runtime="onnxruntime", backends=("cpu", "directml", "cuda"),
)

STRIP_ROWS = 64
HISTOGRAM_BINS = 1024
USABLE_LUMINANCE = (0.025, 0.85)
MIN_USABLE_FRACTION = 0.05
TARGET_LUMINANCE = (0.14, 0.30)


def _measure_midtones(state):
    import numpy as np

    histogram = np.zeros(HISTOGRAM_BINS, dtype=np.int64)
    for start in range(0, state.height, STRIP_ROWS):
        strip = state.pixels[start:start + STRIP_ROWS]
        luminance = strip[:, :, 0] * np.float32(0.2126)
        luminance += strip[:, :, 1] * np.float32(0.7152)
        luminance += strip[:, :, 2] * np.float32(0.0722)
        counts, _ = np.histogram(luminance, bins=HISTOGRAM_BINS, range=USABLE_LUMINANCE)
        histogram += counts

    count = int(histogram.sum())
    fraction = count / (state.height * state.width)
    if fraction < MIN_USABLE_FRACTION:
        return None, fraction
    middle_bins = np.searchsorted(histogram.cumsum(), ((count + 1) // 2, count // 2 + 1))
    low, high = USABLE_LUMINANCE
    return low + (float(middle_bins.mean()) + 0.5) * (high - low) / HISTOGRAM_BINS, fraction


class AutoExposure:
    def __init__(self, stage):
        self.strength = stage.parameter("strength")
        self.max_stops = stage.parameter("max_stops")
        self.session = None

    def prepare(self, runtime):
        self.runtime = runtime

    def release(self):
        self.runtime = self.session = None

    def process(self, state):
        import numpy as np

        if self.strength == 0 or self.max_stops == 0:
            self.runtime.emit("AUTO_EXPOSURE_UNCHANGED", "Strength or maximum correction is zero.")
            return state

        median, fraction = _measure_midtones(state)
        if median is None:
            self.runtime.emit("AUTO_EXPOSURE_INSUFFICIENT_MIDTONES",
                              "Too few midtones for automatic exposure. Review the photo in preview.",
                              level="WARNING", usable_fraction=fraction)
            return state

        low, high = TARGET_LUMINANCE
        if low <= median <= high:
            self.runtime.emit("AUTO_EXPOSURE_UNCHANGED", "Measured midtone is already in the target range.",
                              median_luminance=median, usable_fraction=fraction)
            return state

        target = low if median < low else high
        # An exposure shift in log-odds makes the curve reach the requested
        # target at full strength for neutral pixels, with smooth toe and shoulder.
        requested = math.log2((target / (1 - target)) / (median / (1 - median)))
        stops = self.strength * max(-self.max_stops, min(self.max_stops, requested))
        if self.session is None:
            self.session = open_session(exposure_graph(), self.runtime.backend)
        pixels = apply_curve(self.session, state.pixels, gain=np.asarray([2 ** stops], dtype=np.float32))
        result = ImageState.from_pixels(pixels, state.metadata, pixel_revision=state.pixel_revision + 1,
                                        geometry_revision=state.geometry_revision)
        self.runtime.emit("AUTO_EXPOSURE_APPLIED", "Applied a bounded exposure and tone correction.",
                          median_luminance=median, usable_fraction=fraction,
                          requested_stops=requested, applied_stops=stops)
        return result
