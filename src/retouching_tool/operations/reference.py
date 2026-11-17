"""CPU contract references, not accepted portrait correction or retouching presets."""

from dataclasses import replace

from ..pipeline.definition import ContextSpec, OperationDescriptor, Parameter
from ..pipeline.image_state import ContextEntry, ImageState, ImageStatistics

LUMINANCE = ContextSpec("image.luminance", 1)
STATISTICS = OperationDescriptor("analysis.image_statistics", 1, "Image statistics (reference)",
                                 provides=(LUMINANCE,))
EXPOSURE = OperationDescriptor("core.exposure", 1, "Exposure (reference)",
    parameters=(Parameter("stops", "Exposure stops", 0, -3, 3),), changes_pixels=True,
    invalidates=(LUMINANCE.key,))


class ReferenceOperation:
    def __init__(self, stage):
        self.stage = stage

    def prepare(self, runtime):
        self.runtime = runtime

    def release(self):
        self.runtime = None


class Statistics(ReferenceOperation):
    def process(self, state):
        import numpy as np
        histogram = np.zeros(256, dtype=np.int64)
        total, low, high = 0.0, 0, 0
        minimum, maximum = 1.0, 0.0
        for start in range(0, state.height, 64):
            strip = state.pixels[start:start + 64]
            luminance = strip[:, :, 0] * np.float32(.2126)
            luminance += strip[:, :, 1] * np.float32(.7152)
            luminance += strip[:, :, 2] * np.float32(.0722)
            total += float(luminance.sum(dtype=np.float64))
            minimum, maximum = min(minimum, float(luminance.min())), max(maximum, float(luminance.max()))
            histogram += np.histogram(luminance, bins=256, range=(0, 1))[0]
            low += int(np.count_nonzero(luminance <= 0))
            high += int(np.count_nonzero(luminance >= 1))
        count = state.width * state.height
        value = ImageStatistics(total / count, minimum, maximum, tuple(int(v) for v in histogram), low, high, count)
        entry = ContextEntry(LUMINANCE, self.stage.id, self.stage.operation, self.stage.operation_version,
                             state.pixel_revision, state.geometry_revision, state.width, state.height, value,
                             source_id=state.metadata.source_id, producer_settings_hash=self.stage.identity)
        self.runtime.emit("IMAGE_STATISTICS", "Measured current linear-sRGB luminance", mean=value.mean_luminance,
                          clipped_low=low, clipped_high=high, pixel_count=count)
        if low or high:
            self.runtime.emit("IMAGE_CLIPPING_DETECTED", "Source luminance includes clipped pixels; inspect highlights/shadows",
                              level="WARNING", clipped_low=low, clipped_high=high)
        return replace(state, context=tuple(e for e in state.context if e.spec.key != LUMINANCE.key) + (entry,))


class Exposure(ReferenceOperation):
    def process(self, state):
        import numpy as np
        stops = self.stage.parameter("stops")
        if stops == 0:
            return state
        pixels = np.empty_like(state.pixels)
        clipped = 0
        for start in range(0, state.height, 64):
            strip = pixels[start:start + 64]
            np.multiply(state.pixels[start:start + 64], np.float32(2 ** stops), out=strip)
            clipped += int(np.count_nonzero(strip > 1))
            np.clip(strip, 0, 1, out=strip)
        if clipped:
            self.runtime.emit("EXPOSURE_CLIPPED", "Exposure clipped channel samples; reduce exposure stops to preserve highlights",
                              level="WARNING", clipped_samples=clipped)
        return ImageState.from_pixels(pixels, state.metadata, pixel_revision=state.pixel_revision + 1,
                                      geometry_revision=state.geometry_revision)
