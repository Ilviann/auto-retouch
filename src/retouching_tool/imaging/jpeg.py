"""JPEG boundary for oriented linear-sRGB image states and verified exports."""

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
import hashlib
from io import BytesIO
import os
from pathlib import Path
from time import perf_counter

from .. import PRODUCT_NAME, __version__
from ..errors import RetouchingError
from ..pipeline.image_state import ImageState, SourceMetadata
from .export_settings import JpegSettings
from .exif import EXIF_HEADER, ORIENTATION, read_exif
from .iptc import PHOTOSHOP, read_iptc
from .transfer import encode_srgb_strip, srgb8_to_linear
from .xmp import EXIF, EXTENDED_XMP_HEADER, TIFF, XMP_HEADER, Xmp

MAX_PIXELS = 80_000_000
MAX_HEADER_BYTES = 24 * 1024 * 1024
ICC_HEADER = b"ICC_PROFILE\0"
SOFTWARE = f"{PRODUCT_NAME} {__version__}"
STRIP_ROWS = 32


@dataclass(frozen=True)
class JpegRuntime:
    diagnostics: object
    image_id: str
    job_id: str | None = None
    attempt: int = 1
    preview_request_id: str | None = None

    def emit(self, code, message, **details):
        self.diagnostics.emit(code, message, component="imaging", image_id=self.image_id,
                              job_id=self.job_id, attempt=self.attempt,
                              preview_request_id=self.preview_request_id, **details)


@dataclass(frozen=True)
class JpegHeader:
    width: int
    height: int
    header_bytes: int


def _bound_header(stream):
    if stream.read(2) != b"\xff\xd8":
        raise ValueError("Input is not a JPEG")
    while stream.tell() < MAX_HEADER_BYTES:
        start, marker = stream.read(1), stream.read(1)
        while marker == b"\xff" and stream.tell() < MAX_HEADER_BYTES:
            marker = stream.read(1)
        if start != b"\xff" or not marker or marker in (b"\0", b"\xff", b"\xd8", b"\xd9"):
            raise ValueError("Invalid JPEG header before scan data")
        if marker == b"\xda":
            header_bytes = stream.tell()
            stream.seek(0)
            return header_bytes
        if marker == b"\x01" or 0xD0 <= marker[0] <= 0xD7:
            raise ValueError("Unexpected standalone marker before JPEG scan data")
        raw_length = stream.read(2)
        length = int.from_bytes(raw_length, "big")
        if len(raw_length) != 2 or length < 2:
            raise ValueError("Invalid JPEG segment length")
        stream.seek(length - 2, os.SEEK_CUR)
    raise ValueError("JPEG headers exceed the supported size limit")


@contextmanager
def _open_jpeg(stream):
    from PIL import Image

    header_bytes = _bound_header(stream)
    try:
        opened = Image.open(stream, formats=("JPEG",))
    except Image.DecompressionBombError as exc:
        raise RetouchingError("INPUT_IMAGE_TOO_LARGE",
                              f"JPEG exceeds the {MAX_PIXELS:,}-pixel working limit.") from exc
    with opened as image:
        if image.format != "JPEG":
            raise ValueError("Expected a single JPEG image")
        if image.width * image.height > MAX_PIXELS:
            raise RetouchingError("INPUT_IMAGE_TOO_LARGE",
                                  f"JPEG exceeds the {MAX_PIXELS:,}-pixel working limit.")
        yield image, header_bytes


def inspect_jpeg_header(stream) -> JpegHeader:
    """Read stored dimensions without decoding pixels or validating capture metadata."""
    with _open_jpeg(stream) as (image, header_bytes):
        return JpegHeader(image.width, image.height, header_bytes)


def _metadata(image, source_id):
    exif = xmp = iptc = None
    icc_chunks = {}
    icc_count = None
    for marker, data in image.applist:
        if marker == "APP1" and data.startswith(b"Exif"):
            if exif is not None or not data.startswith(EXIF_HEADER):
                raise ValueError("Duplicate or invalid JPEG EXIF segment")
            exif = data
        elif marker == "APP1" and data.startswith(XMP_HEADER):
            if xmp is not None or data == XMP_HEADER:
                raise ValueError("Duplicate or empty JPEG XMP packet")
            xmp = data[len(XMP_HEADER):]
        elif marker == "APP1" and data.startswith(EXTENDED_XMP_HEADER):
            raise ValueError("Extended JPEG XMP is unsupported")
        elif marker == "APP13" and data.startswith(PHOTOSHOP):
            packet = read_iptc(data)
            if packet is not None:
                if iptc is not None:
                    raise ValueError("Multiple JPEG Photoshop IPTC-IIM resources")
                iptc = packet
        elif marker == "APP2" and data.startswith(b"ICC_PROFILE"):
            if not data.startswith(ICC_HEADER) or len(data) <= len(ICC_HEADER) + 2:
                raise ValueError("Invalid JPEG ICC segment")
            index, count = data[len(ICC_HEADER):len(ICC_HEADER) + 2]
            if not 1 <= index <= count or index in icc_chunks or icc_count not in (None, count):
                raise ValueError("Inconsistent JPEG ICC segment numbering")
            icc_count = count
            icc_chunks[index] = data[len(ICC_HEADER) + 2:]
    if icc_count is not None and len(icc_chunks) != icc_count:
        raise ValueError("Incomplete JPEG ICC profile")
    profile = b"".join(icc_chunks[index] for index in range(1, (icc_count or 0) + 1))
    if profile and (len(profile) < 128 or profile[36:40] != b"acsp"
                    or int.from_bytes(profile[:4], "big") != len(profile)):
        raise ValueError("Invalid JPEG ICC profile header or length")
    return SourceMetadata(source_id, exif or b"", profile, xmp or b"", iptc or b"")


@lru_cache(maxsize=1)
def _srgb():
    from PIL import ImageCms
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))


def _orientation(exif, xmp, runtime, path):
    orientation = xmp.number(TIFF, "Orientation")
    if orientation is None:
        return exif.orientation
    if not 1 <= orientation <= 8:
        raise ValueError("Invalid XMP orientation")
    if ORIENTATION not in exif.primary:
        return orientation
    if orientation != exif.orientation:
        runtime.emit("INPUT_ORIENTATION_CONFLICT", "Applied EXIF orientation because XMP disagrees",
                     level="WARNING", source_path=str(path), exif_orientation=exif.orientation,
                     xmp_orientation=orientation)
    return exif.orientation


def _orient(image, orientation):
    from PIL.Image import Transpose
    transforms = {2: Transpose.FLIP_LEFT_RIGHT, 3: Transpose.ROTATE_180, 4: Transpose.FLIP_TOP_BOTTOM,
                  5: Transpose.TRANSPOSE, 6: Transpose.ROTATE_270, 7: Transpose.TRANSVERSE,
                  8: Transpose.ROTATE_90}
    return image if orientation == 1 else image.transpose(transforms[orientation])


def _convert_to_srgb(image, metadata, exif, xmp_color_space, runtime, path):
    from PIL import ImageCms
    if metadata.icc_profile:
        try:
            profile = ImageCms.ImageCmsProfile(BytesIO(metadata.icc_profile))
            converted = ImageCms.profileToProfile(image, profile, _srgb(), outputMode="RGB",
                                                  renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
                                                  flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
        except (OSError, ImageCms.PyCMSError, ValueError) as exc:
            raise RetouchingError("INPUT_COLOR_PROFILE_INVALID",
                                  "Cannot convert the embedded ICC profile. Check the source image or its color profile.") from exc
        return converted, "embedded-icc"
    spaces = (exif.color_space, xmp_color_space)
    interop = (exif.interop or {}).get(1)
    if (image.mode not in ("RGB", "L") or any(space not in (None, 1, 65535) for space in spaces)
            or (interop is not None and interop.value.rstrip(b"\0") != b"R98")):
        raise RetouchingError("INPUT_COLOR_PROFILE_REQUIRED",
                              "This image needs an embedded ICC profile. Supply an original with the correct profile.")
    if 65535 in spaces or (1 not in spaces and interop is None):
        runtime.emit("INPUT_SRGB_ASSUMED", "Used sRGB for source samples without a usable color-space declaration",
                     level="WARNING", source_path=str(path), exif_color_space=spaces[0], xmp_color_space=spaces[1])
    return (image if image.mode == "RGB" else image.convert("RGB")), "srgb"


def _linear_pixels(rgb):
    import numpy as np
    lut = srgb8_to_linear(np.arange(256))
    pixels = np.empty((rgb.height, rgb.width, 3), np.float32)
    for top in range(0, rgb.height, STRIP_ROWS):
        bottom = min(top + STRIP_ROWS, rgb.height)
        pixels[top:bottom] = lut[np.asarray(rgb.crop((0, top, rgb.width, bottom)))]
    return pixels


def _report_failure(runtime, error, path, fallback_code, fallback_message, path_key):
    if isinstance(error, RetouchingError):
        runtime.emit(error.code, str(error), level="ERROR", exc_info=True, **{path_key: str(path)})
        return
    runtime.emit(fallback_code, fallback_message, level="ERROR", exc_info=True, **{path_key: str(path)})
    raise RetouchingError(fallback_code, fallback_message) from error


@contextmanager
def read_srgb_jpeg(path, *, source_id, runtime: JpegRuntime, expected_sha256=None, draft_size=None):
    """Share orientation and color handling with local display reads."""
    try:
        with Path(path).open("rb") as stream:
            if expected_sha256 is not None:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha256:
                    raise RetouchingError("SOURCE_CHANGED", "JPEG content differs from the frozen job input. Re-index it for a new job.")
                stream.seek(0)
            with _open_jpeg(stream) as (image, _):
                try:
                    metadata = _metadata(image, source_id)
                    exif, xmp = read_exif(metadata.exif), Xmp(metadata.xmp)
                    orientation = _orientation(exif, xmp, runtime, path)
                    xmp_color_space = xmp.number(EXIF, "ColorSpace")
                except Exception as exc:
                    raise RetouchingError("INPUT_METADATA_INVALID",
                                          "Cannot read JPEG metadata safely. Inspect the EXIF, ICC, XMP and IPTC technical details.") from exc
                if exif.thumbnail_issue:
                    runtime.emit("INPUT_EXIF_THUMBNAIL_INVALID", "Ignored an invalid EXIF preview thumbnail.",
                                 level="WARNING", source_path=str(path), technical_cause=exif.thumbnail_issue)
                if draft_size is not None:
                    image.draft(image.mode, (draft_size, draft_size))
                image.load()
                oriented = _orient(image, orientation)
                rgb, handling = _convert_to_srgb(oriented, metadata, exif, xmp_color_space, runtime, path)
                yield rgb, metadata, orientation, handling
    except Exception as exc:
        _report_failure(runtime, exc, path, "INPUT_DECODE_FAILED",
                        "Cannot decode source JPEG. Check that the file is readable and complete.", "source_path")
        raise


def decode_jpeg(path, *, source_id, runtime: JpegRuntime, expected_sha256: str | None = None):
    """Open one JPEG and retain its original EXIF, ICC, XMP and IPTC-IIM bytes."""
    started = perf_counter()
    with read_srgb_jpeg(path, source_id=source_id, runtime=runtime, expected_sha256=expected_sha256) as (
            rgb, metadata, orientation, handling):
        state = ImageState.from_pixels(_linear_pixels(rgb), metadata)
        runtime.emit("INPUT_JPEG_DECODED", "Decoded JPEG to oriented linear sRGB", source_path=str(path),
                     source_id=source_id, width=state.width, height=state.height, orientation=orientation,
                     color_handling=handling, duration_ms=(perf_counter() - started) * 1000)
    return state


def encode_rgb_samples(state):
    import numpy as np

    if not isinstance(state, ImageState) or state.representation != "linear-srgb-float32":
        raise ValueError("Expected a linear-sRGB float32 image state")
    pixels = state.pixels
    if (not isinstance(pixels, np.ndarray) or pixels.dtype != np.float32 or pixels.ndim != 3
            or pixels.shape[2] != 3 or pixels.flags.writeable
            or not 1 <= state.width <= 65500 or not 1 <= state.height <= 65500
            or state.width * state.height > MAX_PIXELS):
        raise ValueError("Invalid working RGB layout/dimensions for RGB export")
    result = np.empty(pixels.shape, dtype=np.uint8)
    for start in range(0, state.height, STRIP_ROWS):
        strip = pixels[start:start + STRIP_ROWS]
        if not np.isfinite(strip).all() or strip.min() < 0 or strip.max() > 1:
            raise ValueError("RGB export requires finite linear samples in [0, 1]")
        result[start:start + STRIP_ROWS] = encode_srgb_strip(strip)
    return result


def _verify(path, size, expected_exif, raw_exif, xmp, profile, iptc):
    from PIL import Image

    with path.open("rb") as stream:
        stream.seek(-2, os.SEEK_END)
        if stream.read() != b"\xff\xd9":
            raise ValueError("Written JPEG is missing its end-of-image marker")
        stream.seek(0)
        _bound_header(stream)
        with Image.open(stream, formats=("JPEG",)) as image:
            metadata = _metadata(image, "export-verification")
            if (image.size != size or image.mode != "RGB" or metadata.icc_profile != profile
                    or metadata.xmp != xmp or metadata.iptc != iptc or metadata.exif != raw_exif
                    or read_exif(metadata.exif) != expected_exif):
                raise ValueError("Written JPEG dimensions, ICC, EXIF, XMP or IPTC differ from the verified export metadata")
            image.load()


def export_jpeg(state, destination, *, settings: JpegSettings, pipeline_sha256: str,
                runtime: JpegRuntime, before_publish=None):
    from .export import export_image
    jpeg_settings = settings if isinstance(settings, JpegSettings) else None
    return export_image(state, destination, settings=jpeg_settings, pipeline_sha256=pipeline_sha256,
                        runtime=runtime, before_publish=before_publish)
