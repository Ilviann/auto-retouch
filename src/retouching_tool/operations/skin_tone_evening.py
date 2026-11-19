"""Native-resolution, mask-weighted correction of local skin color variation."""

from ..imaging.skin_matte import matte_strips
from ..pipeline.definition import IntegerParameter, OperationDescriptor, Parameter
from ..pipeline.image_state import EXPOSED_SKIN, FACE_REGIONS, ImageState


DESCRIPTOR = OperationDescriptor(
    "retouch.local_skin_tone_evening", 1, "Local skin tone evening",
    parameters=(
        Parameter("strength", "Strength", 0.5, 0, 1, preview_on_change=True),
        IntegerParameter("radius", "Neighborhood radius (px)", 24, 1, 96),
        Parameter("max_shift", "Maximum color shift (% of luminance)", 6, 0, 20, preview_on_change=True),
    ),
    requires=(EXPOSED_SKIN,), preserves=(FACE_REGIONS.key,), changes_pixels=True,
)

TILE_SIZE = 256


def _correct_tile(rgb, coverage, rows, cols, radius, strength, max_shift):
    """Read the tile plus its halo from the original. Return just the corrected interior."""
    import numpy as np

    luminance = rgb @ np.array([.2126, .7152, .0722], dtype=np.float32)
    # Shadow noise and nearly clipped channels are unreliable guides to skin color.
    reliability = np.clip((luminance - .005) / .025, 0, 1) * np.clip((1 - rgb.max(axis=2)) / .05, 0, 1)
    # Sample high-coverage skin for the reference. Feather the actual edit at the boundary.
    sample_weight = reliability * np.clip((coverage.astype(np.float32) - 192) / 63, 0, 1)
    divisor = np.maximum(luminance, .005)
    red = rgb[:, :, 0] / divisor
    blue = rgb[:, :, 2] / divisor

    # Summed-area tables give the same clipped square neighborhood on either side
    # of a tile seam. Accumulate in double precision before subtracting nearby sums.
    height, width = luminance.shape
    integral = np.zeros((height + 1, width + 1, 4), dtype=np.float64)
    moments = integral[1:, 1:]
    moments[:, :, 0] = sample_weight
    moments[:, :, 1] = sample_weight * red
    moments[:, :, 2] = sample_weight * blue
    moments[:, :, 3] = sample_weight * (red * red + blue * blue)
    np.cumsum(moments, axis=0, out=moments)
    np.cumsum(moments, axis=1, out=moments)
    y = np.arange(rows.start, rows.stop)[:, None]
    x = np.arange(cols.start, cols.stop)
    top, bottom = np.maximum(0, y - radius), np.minimum(height, y + radius + 1)
    left, right = np.maximum(0, x - radius), np.minimum(width, x + radius + 1)
    sums = integral[bottom, right] - integral[top, right] - integral[bottom, left] + integral[top, left]
    weight = sums[:, :, 0]
    mean = sums[:, :, 1:] / np.maximum(weight[:, :, None], 1e-12)
    mean_red, mean_blue, mean_square = mean.transpose(2, 0, 1)
    variance = np.maximum(0, mean_square - mean_red ** 2 - mean_blue ** 2)

    original = rgb[rows, cols]
    dr = mean_red - red[rows, cols]
    db = mean_blue - blue[rows, cols]
    # Solve for the green adjustment that keeps linear-sRGB luminance fixed.
    dg = -(.2126 * dr + .0722 * db) / .7152
    shift = np.stack((dr, dg, db), axis=2)
    distance = np.max(np.abs(shift), axis=2)
    # Suppress high-contrast color boundaries and isolated strongly colored features.
    amount = strength * (.01 / (.01 + variance)) * np.clip(1 - (distance / .45) ** 2, 0, 1)
    amount *= reliability[rows, cols] * (coverage[rows, cols] / 255.0)
    amount *= (weight >= 1) & (distance > 1e-6)
    amount *= np.minimum(1, max_shift / np.maximum(distance, 1e-12))
    delta = shift * (luminance[rows, cols] * amount)[:, :, None]
    # Limit the whole correction vector at a gamut boundary to retain its luminance.
    room = np.where(delta > 0, 1 - original, original)
    channel_limits = np.divide(room, np.abs(delta), out=np.ones_like(delta), where=delta != 0)
    gamut = np.minimum(1, channel_limits.min(axis=2))
    return np.clip(original + gamut[:, :, None] * delta, 0, 1).astype(np.float32)


class LocalSkinToneEvening:
    def __init__(self, stage):
        self.strength = stage.parameter("strength")
        self.radius = stage.parameter("radius")
        self.max_shift = stage.parameter("max_shift") / 100

    def prepare(self, runtime):
        self.runtime = runtime

    def release(self):
        self.runtime = None

    def process(self, state):
        import numpy as np

        mask = state.entry(EXPOSED_SKIN.key).value
        if not self.strength or not self.max_shift:
            self.runtime.emit("SKIN_TONE_UNCHANGED", "Strength or maximum color shift is zero.")
            return state
        if not any(mask.labels):
            self.runtime.emit("SKIN_TONE_EMPTY_MASK", "Local skin tone evening skipped because the mask is empty. "
                              "Inspect the Exposed Skin Mask preview.", level="WARNING")
            return state

        # One byte per image pixel. All neighborhood scratch is bounded by the tile and halo.
        matte = np.empty((state.height, state.width), dtype=np.uint8)
        for start, stop, strip in matte_strips(mask, (state.width, state.height), (1, 2)):
            matte[start:stop] = np.asarray(strip)
        output = None
        changed = 0
        radius = self.radius
        for y in range(0, state.height, TILE_SIZE):
            bottom = min(state.height, y + TILE_SIZE)
            for x in range(0, state.width, TILE_SIZE):
                right = min(state.width, x + TILE_SIZE)
                if not matte[y:bottom, x:right].any():
                    continue
                top, left = max(0, y - radius), max(0, x - radius)
                area = (slice(top, min(state.height, bottom + radius)), slice(left, min(state.width, right + radius)))
                tile = _correct_tile(state.pixels[area], matte[area], slice(y - top, bottom - top),
                                     slice(x - left, right - left), radius, self.strength, self.max_shift)
                count = int(np.count_nonzero(np.any(tile != state.pixels[y:bottom, x:right], axis=2)))
                if count:
                    if output is None:
                        output = state.pixels.copy()
                    output[y:bottom, x:right] = tile
                    changed += count
        if output is None:
            self.runtime.emit("SKIN_TONE_UNCHANGED", "No eligible local color variation was found.")
            return state
        self.runtime.emit("SKIN_TONE_APPLIED", "Reduced local color variation within the exposed skin mask.",
                          changed_pixels=changed, radius_px=radius, strength=self.strength, max_shift_percent=self.max_shift * 100)
        return ImageState.from_pixels(output, state.metadata, pixel_revision=state.pixel_revision + 1,
                                      geometry_revision=state.geometry_revision)
