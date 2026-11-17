"""Strict uncompressed float32 RGB TIFF adapter; original EXIF stays separate.

The TIFF describes the oriented linear working pixels, not the camera original.
Only this deliberately narrow storage profile is accepted for recovery.
"""

import hashlib

from .. import __version__
from ..errors import RetouchingError
from .artifact_codec import digest, fields, integer, require

TIFF_OVERHEAD_LIMIT = 65536
DESCRIPTION = "Auto ReTouch working pixels: linear-srgb-float32; full-resolution-oriented-pixels"


def _library():
    try:
        import tifffile
        return tifffile
    except ImportError as exc:
        raise RetouchingError("DEPENDENCY_MISSING", "TIFF artifacts require tifffile==2026.10.15 in the project .venv; prepare the pinned shared dependencies before processing") from exc


def pixel_digest(pixels):
    return hashlib.sha256(memoryview(pixels).cast("B")).hexdigest()


def filename(pixel_sha256, width, height):
    # Equal sample streams with different dimensions are different TIFF layouts.
    layout = f"tiff-float32-rgb-v1:{width}:{height}:{pixel_sha256}".encode("ascii")
    return hashlib.sha256(layout).hexdigest() + ".tif"


def describe(path, pixel_sha256):
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"sha256": checksum, "bytes": path.stat().st_size, "pixel_sha256": pixel_sha256, "format": "tiff-float32-rgb-v1"}


def validate_descriptor(descriptor, max_pixel_bytes):
    fields(descriptor, "sha256 bytes pixel_sha256 format")
    require(digest(descriptor["sha256"]) and digest(descriptor["pixel_sha256"])
            and integer(descriptor["bytes"], 1) and descriptor["bytes"] <= max_pixel_bytes + TIFF_OVERHEAD_LIMIT
            and descriptor["format"] == "tiff-float32-rgb-v1", "Invalid TIFF blob descriptor")


def read(path, descriptor, width, height, max_pixel_bytes):
    import numpy as np
    validate_descriptor(descriptor, max_pixel_bytes)
    require(integer(width, 1) and integer(height, 1) and width * height * 12 <= max_pixel_bytes,
            "TIFF dimensions exceed configured pixel budget")
    require(path.stat().st_size == descriptor["bytes"], "TIFF size mismatch")
    require(describe(path, descriptor["pixel_sha256"]) == descriptor, "TIFF size/checksum mismatch")
    with _library().TiffFile(path) as tiff:
        require(tiff.byteorder == "<" and len(tiff.pages) == 1, "Expected one little-endian TIFF image")
        page = tiff.pages[0]
        require(page.shape == (height, width, 3) and page.dtype == np.dtype("<f4")
                and page.compression == 1 and page.photometric == 2 and page.planarconfig == 1
                and page.sampleformat == 3 and page.bitspersample == 32 and page.samplesperpixel == 3
                and page.description == DESCRIPTION and not page.is_tiled
                and page.tags.get("Orientation") is None and page.tags.get("Predictor") is None,
                "Unsupported TIFF layout, precision or working representation")
        require(sum(page.databytecounts) == width * height * 12
                and all(offset >= 0 and offset + count <= descriptor["bytes"]
                        for offset, count in zip(page.dataoffsets, page.databytecounts)),
                "Truncated or inconsistent TIFF pixel segments")
        pixels = page.asarray(maxworkers=1)
    require(pixel_digest(pixels) == descriptor["pixel_sha256"], "TIFF sample checksum mismatch")
    return pixels


def write(path, pixels):
    # Explicitly omit timestamps, source ICC and source EXIF. Source ICC can
    # describe encoded camera colors, not normalized linear-sRGB working data.
    with path.open("xb") as stream:
        _library().imwrite(stream, pixels, photometric="rgb", planarconfig="contig", byteorder="<",
                           compression=None, predictor=None, metadata=None, description=DESCRIPTION,
                           software=f"Auto ReTouch {__version__}", datetime=None, rowsperstrip=pixels.shape[0])
        stream.flush()
        import os
        os.fsync(stream.fileno())
