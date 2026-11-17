"""JPEG boundary for oriented linear-sRGB image states and verified exports."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
from time import perf_counter

from .. import PRODUCT_NAME, __version__
from ..errors import RetouchingError
from ..pipeline.image_state import ImageState, SourceMetadata
from .exif import EXIF_HEADER, ORIENTATION, for_output, read_exif, write_exif
from .iptc import PHOTOSHOP, iptc_marker, read_iptc
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
class JpegSettings:
    quality: int = 92
    subsampling: str = "4:4:4"
    progressive: bool = False

    def __post_init__(self):
        if (type(self.quality) is not int or not 1 <= self.quality <= 95
                or self.subsampling not in ("4:4:4", "4:2:2", "4:2:0")
                or type(self.progressive) is not bool):
            raise ValueError("Set JPEG quality to 1–95, chroma to 4:4:4/4:2:2/4:2:0 and progressive to on/off")


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
                                  "Cannot convert the embedded ICC profile. Check the source JPEG or its color profile.") from exc
        return converted, "embedded-icc"
    spaces = (exif.color_space, xmp_color_space)
    interop = (exif.interop or {}).get(1)
    if (image.mode not in ("RGB", "L") or any(space not in (None, 1, 65535) for space in spaces)
            or (interop is not None and interop.value.rstrip(b"\0") != b"R98")):
        raise RetouchingError("INPUT_COLOR_PROFILE_REQUIRED",
                              "This JPEG needs an embedded ICC profile. Supply an original JPEG with the correct profile.")
    if 65535 in spaces or (1 not in spaces and interop is None):
        runtime.emit("INPUT_SRGB_ASSUMED", "Used sRGB for JPEG samples without a usable color-space declaration",
                     level="WARNING", source_path=str(path), exif_color_space=spaces[0], xmp_color_space=spaces[1])
    return (image if image.mode == "RGB" else image.convert("RGB")), "srgb"


def _linear_pixels(rgb):
    import numpy as np
    encoded = np.arange(256, dtype=np.float32) / 255
    lut = np.where(encoded <= .04045, encoded / 12.92, ((encoded + .055) / 1.055) ** 2.4)
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


def decode_jpeg(path, *, source_id, runtime: JpegRuntime, expected_sha256: str | None = None):
    """Open one JPEG and retain its original EXIF, ICC, XMP and IPTC-IIM bytes."""
    started = perf_counter()
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
                image.load()
                oriented = _orient(image, orientation)
                rgb, handling = _convert_to_srgb(oriented, metadata, exif, xmp_color_space, runtime, path)
                state = ImageState.from_pixels(_linear_pixels(rgb), metadata)
        runtime.emit("INPUT_JPEG_DECODED", "Decoded JPEG to oriented linear sRGB", source_path=str(path),
                     source_id=source_id, width=state.width, height=state.height, orientation=orientation,
                     color_handling=handling, duration_ms=(perf_counter() - started) * 1000)
        return state
    except Exception as exc:
        _report_failure(runtime, exc, path, "INPUT_DECODE_FAILED",
                        "Cannot decode source JPEG. Check that the file is readable and complete.", "source_path")
        raise


def _encoded_pixels(state):
    import numpy as np

    if not isinstance(state, ImageState) or state.representation != "linear-srgb-float32":
        raise ValueError("Expected a linear-sRGB float32 image state")
    pixels = state.pixels
    if (not isinstance(pixels, np.ndarray) or pixels.dtype != np.float32 or pixels.ndim != 3
            or pixels.shape[2] != 3 or pixels.flags.writeable
            or not 1 <= state.width <= 65500 or not 1 <= state.height <= 65500
            or state.width * state.height > MAX_PIXELS):
        raise ValueError("Invalid working RGB layout/dimensions for JPEG export")
    result = np.empty(pixels.shape, dtype=np.uint8)
    for start in range(0, state.height, STRIP_ROWS):
        strip = pixels[start:start + STRIP_ROWS]
        if not np.isfinite(strip).all() or strip.min() < 0 or strip.max() > 1:
            raise ValueError("JPEG export requires finite linear samples in [0, 1]")
        encoded = np.where(strip <= .0031308, 12.92 * strip, 1.055 * strip ** (1 / 2.4) - .055)
        result[start:start + STRIP_ROWS] = np.rint(encoded * 255).astype(np.uint8)
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
    """Verify a pending JPEG, optionally journal it, then publish without replacing a file."""
    pending = None
    started = perf_counter()
    try:
        from PIL import Image

        destination = Path(destination)
        if destination.suffix.lower() not in (".jpg", ".jpeg") or os.path.isreserved(destination.name):
            raise RetouchingError("OUTPUT_PATH_INVALID", "Choose a valid .jpg or .jpeg destination filename.")
        destination = destination.parent.resolve(strict=True) / destination.name
        if os.path.lexists(destination):
            raise RetouchingError("OUTPUT_COLLISION", "JPEG destination already exists. Choose a different filename.")
        if not isinstance(settings, JpegSettings) or not isinstance(pipeline_sha256, str) or not re.fullmatch(
                r"[0-9a-f]{64}", pipeline_sha256):
            raise RetouchingError("OUTPUT_SETTINGS_INVALID", "JPEG export requires compression settings and a pipeline SHA-256.")
        try:
            pixels = _encoded_pixels(state)
        except ValueError as exc:
            raise RetouchingError("OUTPUT_STATE_INVALID", "The image state is invalid or exceeds the JPEG working limit.") from exc
        size = (state.width, state.height)
        try:
            original = read_exif(state.metadata.exif)
            expected, previous = for_output(original, size, SOFTWARE)
            raw_exif = write_exif(expected)
            xmp = Xmp(state.metadata.xmp).for_output(
                size, SOFTWARE, datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                pipeline_sha256, previous)
            profile = _srgb().tobytes()
            extra = iptc_marker(state.metadata.iptc)
        except RetouchingError:
            raise
        except Exception as exc:
            raise RetouchingError("OUTPUT_METADATA_INVALID",
                                  "Cannot prepare verified EXIF, XMP and IPTC metadata for this export.") from exc
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".auto-retouch-", suffix=".pending",
                                         delete=False) as stream:
            pending = Path(stream.name)
            with Image.fromarray(pixels) as image:
                image.save(stream, format="JPEG", quality=settings.quality, subsampling=settings.subsampling,
                           progressive=settings.progressive, exif=raw_exif, icc_profile=profile, xmp=xmp, extra=extra)
            stream.flush()
            os.fsync(stream.fileno())
        del pixels
        try:
            _verify(pending, size, expected, raw_exif, xmp, profile, state.metadata.iptc)
        except Exception as exc:
            raise RetouchingError("OUTPUT_VALIDATION_FAILED",
                                  "JPEG did not pass verification. Check technical details before retrying.") from exc
        if before_publish is not None:
            before_publish(pending, destination)
        try:
            pending.rename(destination)
        except FileExistsError as exc:
            raise RetouchingError("OUTPUT_COLLISION", "JPEG destination was created during export. Choose another filename.") from exc
        pending = None
        runtime.emit("OUTPUT_JPEG_VERIFIED", "Published JPEG with verified capture metadata and processing history",
                     output_path=str(destination), width=size[0], height=size[1], pipeline_sha256=pipeline_sha256,
                     quality=settings.quality, subsampling=settings.subsampling, progressive=settings.progressive,
                     removed_exif_thumbnail=original.had_thumbnail, duration_ms=(perf_counter() - started) * 1000)
        return destination
    except Exception as exc:
        _report_failure(runtime, exc, destination, "OUTPUT_WRITE_FAILED",
                        "Cannot write JPEG export. Check the destination permissions and free space.", "output_path")
        raise
    finally:
        if pending is not None:
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                runtime.emit("OUTPUT_CLEANUP_FAILED", "Could not remove the incomplete JPEG export. Inspect the pending file.",
                             output_path=str(destination), pending_path=str(pending), level="ERROR", exc_info=True)
