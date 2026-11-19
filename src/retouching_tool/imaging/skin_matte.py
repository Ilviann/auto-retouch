"""Feather and sample an oriented skin grid identically for display and linear-light blending."""

def matte_strips(mask, size, selected):
    from PIL import Image

    width, height = size
    table = [255 if value in selected else 0 for value in range(256)]
    with (Image.frombytes("L", (mask.width, mask.height), mask.labels) as labels,
          labels.point(table) as matte):
        step = max(1, 65536 // width)
        for start in range(0, height, step):
            stop = min(height, start + step)
            box = (0, start * mask.height / height, mask.width, stop * mask.height / height)
            with matte.resize((width, stop - start), Image.Resampling.BILINEAR, box=box) as strip:
                yield start, stop, strip


def blend_skin(source, edited, mask):
    import numpy as np

    height, width = source.shape[:2]
    output = np.empty_like(source)
    for start, stop, strip in matte_strips(mask, (width, height), (1, 2)):
        original = source[start:stop]
        alpha = np.asarray(strip, dtype=np.float32)[:, :, None] / 255
        output[start:stop] = original + alpha * (edited[start:stop] - original)
    return output
