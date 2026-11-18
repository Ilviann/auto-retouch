"""Pixel-center image sampling with bounded working buffers."""

from ..config import SCALING_METHODS, ScalingSettings


def fit_longest_side(size, limit):
    longest = max(size)
    if longest <= limit:
        return size
    return tuple(max(1, (side * limit + longest // 2) // longest) for side in size)


def _axis(source, target, methods):
    import numpy as np

    method = methods.downscale if target < source else methods.upscale
    if method not in SCALING_METHODS:
        raise ValueError(f"Unsupported resize method: {method}")
    centers = (np.arange(target, dtype=np.float32) + .5) * (source / target) - .5
    centers = np.clip(centers, 0, source - 1)
    if method == "nearest" or source == target:
        return (centers + .5).astype(np.intp), None, None
    low = centers.astype(np.intp)
    return low, np.minimum(low + 1, source - 1), centers - low.astype(np.float32)


def resize_rgb(pixels, size, methods: ScalingSettings, *, encode_srgb=False):
    """Resize float32 RGB; optional sRGB conversion precedes interpolation for model inputs."""
    import numpy as np

    width, height = size
    if width < 1 or height < 1:
        raise ValueError("Resize dimensions must be positive")
    x0, x1, fx = _axis(pixels.shape[1], width, methods)
    y0, y1, fy = _axis(pixels.shape[0], height, methods)

    def samples(rows, cols):
        values = pixels[rows[:, None], cols[None, :]]
        if encode_srgb:
            return np.where(values <= .0031308, 12.92 * values, 1.055 * values ** (1 / 2.4) - .055)
        return values

    def sample_rows(rows):
        left = samples(rows, x0)
        if x1 is None:
            return left
        weight = fx[None, :, None]
        return left * (1 - weight) + samples(rows, x1) * weight

    result = np.empty((height, width, 3), dtype=np.float32)
    block_rows = max(1, 65536 // width)
    for start in range(0, height, block_rows):
        block = slice(start, start + block_rows)
        top = sample_rows(y0[block])
        if y1 is not None:
            weight = fy[block, None, None]
            top = top * (1 - weight) + sample_rows(y1[block]) * weight
        result[block] = top
    return result
