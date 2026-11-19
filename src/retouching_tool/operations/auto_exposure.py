"""Bounded automatic exposure for oriented linear-sRGB images."""

import math

from ..pipeline.definition import OperationDescriptor, Parameter
from ..pipeline.image_state import ImageState


DESCRIPTOR = OperationDescriptor(
    "core.auto_exposure", 1, "Auto exposure and tone",
    parameters=(
        Parameter("strength", "Strength (0–1)", 0.5, 0, 1, preview_on_change=True),
        Parameter("max_stops", "Maximum correction (stops)", 1.0, 0, 2, preview_on_change=True),
    ),
    changes_pixels=True,
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


def _apply_tone_curve(state, stops):
    import numpy as np

    gain = np.float32(2 ** stops)
    pixels = np.empty_like(state.pixels)
    for start in range(0, state.height, STRIP_ROWS):
        strip = state.pixels[start:start + STRIP_ROWS]
        # Apply a monotone curve to the largest channel, scaling all channels
        # together. Black and the gamut boundary stay fixed without clipping.
        peak = strip.max(axis=2)
        divisor = peak + (1 - peak) / gain
        np.divide(strip, divisor[:, :, None], out=pixels[start:start + STRIP_ROWS])
    return ImageState.from_pixels(pixels, state.metadata, pixel_revision=state.pixel_revision + 1,
                                  geometry_revision=state.geometry_revision)


class AutoExposure:
    def __init__(self, stage):
        self.strength = stage.parameter("strength")
        self.max_stops = stage.parameter("max_stops")

    def prepare(self, runtime):
        self.runtime = runtime

    def release(self):
        self.runtime = None

    def process(self, state):
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
        result = _apply_tone_curve(state, stops)
        self.runtime.emit("AUTO_EXPOSURE_APPLIED", "Applied a bounded exposure and tone correction.",
                          median_luminance=median, usable_fraction=fraction,
                          requested_stops=requested, applied_stops=stops)
        return result
