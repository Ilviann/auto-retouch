"""sRGB code values used at the SDR image boundary."""


def encode_srgb_strip(strip):
    """Convert normalized linear RGB samples to 8-bit sRGB codes."""
    import numpy as np

    encoded = np.where(strip <= .0031308, 12.92 * strip, 1.055 * strip ** (1 / 2.4) - .055)
    return np.rint(encoded * 255).astype(np.uint8)


def srgb8_to_linear(codes):
    import numpy as np

    encoded = np.asarray(codes, dtype=np.float32) / 255
    return np.where(encoded <= .04045, encoded / 12.92, ((encoded + .055) / 1.055) ** 2.4)
