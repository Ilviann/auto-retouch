"""Attenuate localized near-neutral reflections on exposed skin in linear light."""

from ..imaging.skin_matte import full_skin_matte, neighborhood_sums
from ..pipeline.definition import OperationDescriptor, Parameter
from ..pipeline.image_state import EXPOSED_SKIN, ImageState


DESCRIPTOR = OperationDescriptor(
    "retouch.skin_shine_reduction", 1, "Skin shine reduction",
    parameters=(Parameter("strength", "Strength", 0.5, 0, 1, preview_on_change=True),),
    requires=(EXPOSED_SKIN,), changes_pixels=True, preview_affected_areas=True,
    backends=("cpu",), inference_runtime="cpu",
)
TILE_SIZE = 256


def _reflection(rgb, coverage, rows, cols, radius):
    """Estimate an additive neutral component, bounded by two local measurements."""
    import numpy as np

    luma = rgb @ np.array([.2126, .7152, .0722], dtype=np.float32)
    peak = rgb.max(axis=2)
    span = peak - rgb.min(axis=2)
    # Use well-covered, unclipped skin to estimate diffuse luminance and color.
    weight = np.clip((coverage.astype(np.float32) - 192) / 63, 0, 1)
    weight *= np.clip((.98 - peak) / .10, 0, 1)
    h, w = luma.shape
    integral = np.zeros((h + 1, w + 1, 3), dtype=np.float64)
    integral[1:, 1:, 0] = weight
    integral[1:, 1:, 1] = weight * luma
    integral[1:, 1:, 2] = weight * span
    mass, sum_luma, sum_span = neighborhood_sums(integral, rows, cols, radius).transpose(2, 0, 1)
    reference = sum_luma / np.maximum(mass, 1e-8)
    chroma = sum_span / np.maximum(sum_luma, 1e-8)
    light = luma[rows, cols]
    # Adding neutral light increases luminance while preserving the RGB channel span.
    # A brighter patch of the same chromaticity therefore yields zero color evidence.
    brightness_excess = light - reference * 1.08 - .008
    neutral_excess = light * .92 - span[rows, cols] / np.maximum(chroma, 1e-8)
    reflection = np.maximum(0, np.minimum(brightness_excess, neutral_excess))
    reflection = np.minimum(reflection, .30 * light)
    confidence = np.clip((mass - 4) / 12, 0, 1) * np.clip((chroma - .08) / .16, 0, 1)
    confidence *= np.clip((1 - peak[rows, cols]) / .04, 0, 1)
    return (reflection * confidence * (coverage[rows, cols] / 255)).astype(np.float32)


class SkinShineReduction:
    def __init__(self, stage):
        self.strength = stage.parameter("strength")

    def prepare(self, runtime):
        self.runtime = runtime

    def release(self):
        self.runtime = None

    def process(self, state):
        import numpy as np

        mask = state.entry(EXPOSED_SKIN.key).value
        if not self.strength:
            self.runtime.emit("SKIN_SHINE_UNCHANGED", "Skin shine reduction strength is zero.")
            return state
        if not any(mask.labels):
            self.runtime.emit("SKIN_SHINE_EMPTY_MASK", "No exposed skin was detected. Inspect the Exposed Skin Mask preview.",
                              level="WARNING")
            return state

        matte = full_skin_matte(mask, (state.width, state.height))
        radius = max(8, min(96, round(min(state.width, state.height) * .025)))
        output = None
        changed = 0
        for y in range(0, state.height, TILE_SIZE):
            bottom = min(state.height, y + TILE_SIZE)
            for x in range(0, state.width, TILE_SIZE):
                right = min(state.width, x + TILE_SIZE)
                if not matte[y:bottom, x:right].any():
                    continue
                top, left = max(0, y - radius), max(0, x - radius)
                halo = (slice(top, bottom + radius), slice(left, right + radius))
                selection = (slice(y, bottom), slice(x, right))
                source = state.pixels[selection]
                reflection = _reflection(state.pixels[halo], matte[halo], slice(y - top, bottom - top),
                                         slice(x - left, right - left), radius)
                # Cap neutral subtraction at the available room in the darkest channel.
                correction = self.strength * np.minimum(reflection, source.min(axis=2))
                edited = source - correction[:, :, None]
                count = np.count_nonzero(np.any(edited != source, axis=2))
                if count:
                    if output is None:
                        output = state.pixels.copy()
                    output[selection] = edited
                    changed += int(count)
        if output is None:
            self.runtime.emit("SKIN_SHINE_UNCHANGED", "No eligible skin highlights were found.")
            return state
        self.runtime.emit("SKIN_SHINE_APPLIED", "Reduced localized shine on exposed skin.",
                          changed_pixels=changed, strength=self.strength, radius_px=radius)
        return ImageState.from_pixels(output, state.metadata, pixel_revision=state.pixel_revision + 1,
                                      geometry_revision=state.geometry_revision)
