"""Full-resolution, local DNG development into the application's SDR working path."""

from contextlib import contextmanager
import mmap
import os

from ..errors import RetouchingError
from ..pipeline.image_state import SourceMetadata
from .exif import TIFF_LAYOUT_TAGS, read_tiff_directory, write_exif
from .jpeg import MAX_HEADER_BYTES, MAX_PIXELS, JpegHeader


def require_dng():
    try:
        import rawpy
        if rawpy.__version__ != "0.28.0":
            raise ValueError(f"Installed rawpy version is {rawpy.__version__}")
    except (ImportError, OSError, ValueError) as exc:
        raise RetouchingError("DNG_CODEC_UNAVAILABLE", "Install the pinned DNG decoder with "
                              r".venv\Scripts\python -m pip install --require-hashes --no-deps -r requirements/import-dng.lock") from exc
    return rawpy


def dng_versions():
    codec = require_dng()
    return {"rawpy": codec.__version__, "libraw": ".".join(map(str, codec.libraw_version))}


def _metadata(stream, source_id):
    from PIL.ExifTags import Base

    # A DNG is a TIFF with RAW calibration data, potentially very large embedded
    # originals, and preview/RAW SubIFDs. Read capture fields from IFD0 and its
    # complete EXIF/GPS/interop children without copying RAW image directories.
    primary_tags = ({int(tag) for tag in Base if tag < 50706} - TIFF_LAYOUT_TAGS
                    - {330, 33421, 33422, 347, 339}) | {34675, 50706, 50708, 50735, 50736}
    with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
        exif = read_tiff_directory(mapped, byte_limit=MAX_HEADER_BYTES,
                                   primary_filter=primary_tags.__contains__, read_thumbnail=False)
    version = exif.primary.pop(50706, None)
    if version is None or version.kind != 1 or version.count != 4 or version.value[0] != 1:
        raise ValueError("Missing or unsupported DNGVersion in the primary TIFF directory")
    # Carry DNG-specific camera identity into the equivalent standard EXIF fields.
    for tag, fields, target in ((50708, exif.primary, 272), (50735, exif.capture, 42033),
                                (50736, exif.capture, 42034)):
        entry = exif.primary.pop(tag, None)
        if entry is not None:
            fields.setdefault(target, entry)
    resources = {}
    for tag in (34675, 700, 33723):
        entry = exif.primary.pop(tag, None)
        resources[tag] = entry.value if entry else b""
    return SourceMetadata(source_id, write_exif(exif), resources[34675], resources[700], resources[33723])


@contextmanager
def _sensor(stream, source_id, *, indexing=False):
    codec = require_dng()
    file_bytes = os.fstat(stream.fileno()).st_size
    if file_bytes > 4 * MAX_PIXELS + MAX_HEADER_BYTES:
        raise RetouchingError("INPUT_IMAGE_TOO_LARGE", "DNG exceeds the compressed input buffer limit.")
    try:
        metadata = _metadata(stream, source_id)
    except Exception as exc:
        raise RetouchingError("INPUT_METADATA_INVALID", "Cannot read DNG identification or capture metadata. "
                              "Check that this is an intact classic-TIFF DNG.") from exc
    with codec.RawPy() as raw:
        try:
            if indexing:
                raw.open_file(stream.name)  # Header inspection does not unpack sensor pixels.
            else:
                stream.seek(0)
                raw.open_buffer(stream)  # Decode from the same handle used for job content verification.
        except codec.LibRawError as exc:
            raise RetouchingError("INPUT_DNG_UNSUPPORTED", f"LibRaw could not open this DNG: {exc}") from exc
        sizes = raw.sizes
        width, height = sizes.width, sizes.height
        for w, h in ((width, height), (sizes.raw_width, sizes.raw_height)):
            if min(w, h) < 1 or max(w, h) > 65500 or w * h > MAX_PIXELS:
                raise RetouchingError("INPUT_IMAGE_TOO_LARGE", "DNG sensor or active image exceeds the working dimensions.")
        if sizes.flip & 4:
            width, height = height, width
        metadata_bytes = sum(map(len, (metadata.exif, metadata.icc_profile, metadata.xmp, metadata.iptc)))
        yield raw, metadata, JpegHeader(width, height, min(file_bytes, max(1, metadata_bytes)))


def inspect_dng(stream):
    with _sensor(stream, "header", indexing=True) as (_, _, header):
        return header


@contextmanager
def open_dng(stream, source_id):
    from PIL import Image

    codec = require_dng()
    with _sensor(stream, source_id) as (raw, metadata, header):
        try:
            pixels = raw.postprocess(use_camera_wb=True, no_auto_bright=True, half_size=False,
                                     output_color=codec.ColorSpace.sRGB, output_bps=8, gamma=(2.4, 12.92))
        except codec.LibRawError as exc:
            raise RetouchingError("INPUT_DNG_DECODE_FAILED", f"LibRaw could not develop the full-resolution DNG: {exc}") from exc
        if pixels.shape != (header.height, header.width, 3):
            raise RetouchingError("INPUT_DNG_GEOMETRY_UNSUPPORTED",
                                  "Developed DNG dimensions differ from the indexed active image.")
    with Image.fromarray(pixels) as image:
        # LibRaw applies the camera orientation and converts camera color to sRGB.
        yield image, metadata, 1
