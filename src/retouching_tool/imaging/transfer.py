"""sRGB code values used at the SDR image boundary."""


def srgb8_to_linear(codes):
    import numpy as np

    encoded = np.asarray(codes, dtype=np.float32) / 255
    return np.where(encoded <= .04045, encoded / 12.92, ((encoded + .055) / 1.055) ** 2.4)
