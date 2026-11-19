"""Source discovery and SDR decoding for local still photos."""

from contextlib import contextmanager
import hashlib
import mmap
import os
from pathlib import Path
from time import perf_counter

from ..errors import RetouchingError
from ..pipeline.image_state import ImageState, SourceMetadata
from .dng_source import inspect_dng, open_dng
from .exif import TIFF_LAYOUT_TAGS, read_exif, read_tiff_directory, write_exif
from .jxl_source import inspect_jxl, open_jxl
from .jpeg import (MAX_HEADER_BYTES, MAX_PIXELS, JpegHeader, _convert_to_srgb, _linear_pixels,
                   _orient, _orientation, _report_failure, decode_jpeg, inspect_jpeg_header, read_srgb_jpeg)
from .xmp import EXIF, Xmp

SOURCE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".avif": "AVIF", ".png": "PNG",
                  ".tif": "TIFF", ".tiff": "TIFF", ".bmp": "BMP", ".jxl": "JXL", ".dng": "DNG"}
SOURCE_EXTENSIONS = tuple(SOURCE_FORMATS)
# Pillow's AVIF decoder reads the entire compressed container before parsing it.
MAX_AVIF_BYTES = 4 * MAX_PIXELS + MAX_HEADER_BYTES


@contextmanager
def _open_avif(stream):
    from PIL import Image

    if os.fstat(stream.fileno()).st_size > MAX_AVIF_BYTES:
        raise RetouchingError("INPUT_IMAGE_TOO_LARGE", f"AVIF exceeds the {MAX_AVIF_BYTES:,}-byte input limit.")
    try:
        image = Image.open(stream, formats=("AVIF",))
    except Image.DecompressionBombError as exc:
        raise RetouchingError("INPUT_IMAGE_TOO_LARGE", f"AVIF exceeds the {MAX_PIXELS:,}-pixel limit.") from exc
    except Exception as exc:
        raise RetouchingError("INPUT_HEADER_INVALID", "Cannot open the AVIF container. Check the source file.") from exc
    with image:
        if image.width * image.height > MAX_PIXELS or max(image.size) > 65500:
            raise RetouchingError("INPUT_IMAGE_TOO_LARGE", "AVIF exceeds the supported working image dimensions.")
        if image.n_frames != 1:
            raise RetouchingError("INPUT_AVIF_SEQUENCE", "Choose a still AVIF photo or extract a single frame.")
        # Pinned Pillow rewrites info["exif"] to include container rotation/mirroring.
        # Keep the original capture bytes and apply the container orientation to pixels.
        _, _, _, icc, exif, orientation, xmp = image._decoder.get_info()
        metadata_bytes = sum(len(packet) for packet in (icc, exif, xmp) if packet)
        if metadata_bytes > MAX_HEADER_BYTES:
            raise RetouchingError("INPUT_METADATA_INVALID", "AVIF metadata exceeds the supported size limit.")
        yield image, icc, exif, orientation, xmp, max(1, metadata_bytes)


def inspect_source_header(path, stream):
    fmt = SOURCE_FORMATS[path.suffix.lower()]
    if fmt == "AVIF":
        with _open_avif(stream) as (image, _, _, _, _, metadata_bytes):
            return JpegHeader(image.width, image.height, metadata_bytes)
    if fmt == "JPEG":
        return inspect_jpeg_header(stream)
    if fmt == "JXL":
        return inspect_jxl(stream)
    if fmt == "DNG":
        return inspect_dng(stream)
    with _open_raster(stream, fmt) as image:
        return JpegHeader(image.width, image.height, min(os.fstat(stream.fileno()).st_size, MAX_HEADER_BYTES))


@contextmanager
def _open_raster(stream, fmt):
    from PIL import Image

    try:
        image = Image.open(stream, formats=(fmt,))
    except Image.DecompressionBombError as exc:
        raise RetouchingError("INPUT_IMAGE_TOO_LARGE", f"{fmt} exceeds the working image dimensions.") from exc
    except Exception as exc:
        raise RetouchingError("INPUT_HEADER_INVALID", f"Cannot open the {fmt} header. Check the source file.") from exc
    with image:
        if image.width * image.height > MAX_PIXELS or max(image.size) > 65500:
            raise RetouchingError("INPUT_IMAGE_TOO_LARGE", f"{fmt} exceeds the working image dimensions.")
        if getattr(image, "is_animated", False):
            raise RetouchingError("INPUT_IMAGE_SEQUENCE", f"Choose a single-image {fmt} or extract one frame or page.")
        yield image


def _tiff_metadata(stream, source_id):
    # TIFF capture fields can be anywhere in the file. Map it to read just the IFDs
    # and their bounded values, retaining typed entries and the original byte order.
    with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
        exif = read_tiff_directory(mapped, byte_limit=MAX_HEADER_BYTES)
    resources = {}
    for tag in (34675, 700, 33723):
        entry = exif.primary.pop(tag, None)
        resources[tag] = entry.value if entry else b""
    for tag in TIFF_LAYOUT_TAGS | {320, 339, 347}:
        exif.primary.pop(tag, None)
    return SourceMetadata(source_id, write_exif(exif), resources[34675], resources[700], resources[33723])


@contextmanager
def _open_photo(stream, fmt, source_id):
    if fmt == "AVIF":
        with _open_avif(stream) as (image, icc, raw_exif, orientation, xmp, _):
            yield image, SourceMetadata(source_id, raw_exif or b"", icc or b"", xmp or b""), orientation
    elif fmt == "DNG":
        with open_dng(stream, source_id) as photo:
            yield photo
    elif fmt == "JXL":
        inspect_jxl(stream)
        with open_jxl(stream, source_id) as photo:
            yield photo
    else:
        with _open_raster(stream, fmt) as image:
            if fmt == "TIFF":
                metadata = _tiff_metadata(stream, source_id)
                # Pillow applies the TIFF orientation when it loads the pixels.
                orientation = 1 if 274 in image.tag_v2 else None
            else:
                image.load()  # PNG text and EXIF chunks may follow the pixel data.
                raw_exif = image.info.get("exif", b"")
                if not raw_exif and "Raw profile type exif" in image.info:
                    raw_exif = bytes.fromhex("".join(image.info["Raw profile type exif"].splitlines()[3:]))
                xmp = image.info.get("XML:com.adobe.xmp", "").encode("utf-8")
                metadata = SourceMetadata(source_id, raw_exif, image.info.get("icc_profile") or b"", xmp)
                orientation = None
            yield image, metadata, orientation


def _opaque_pixels(image):
    from PIL import Image

    if image.has_transparency_data:
        alpha = image.convert("RGBA")
        background = Image.new("L" if image.mode == "LA" else "RGB", image.size, "white")
        background.paste(alpha, mask=alpha.getchannel("A"))
        return background
    if image.mode.startswith("I;16"):
        # Pillow convert("L") clips 16-bit grayscale samples above 255.
        return image.convert("I").point(lambda value: value * (1 / 257)).convert("L")
    if image.mode in ("P", "1"):
        return image.convert("RGB" if image.mode == "P" else "L")
    return image


@contextmanager
def _read_photo(path, fmt, *, source_id, runtime, expected_sha256=None):
    try:
        with Path(path).open("rb") as stream:
            if expected_sha256 is not None:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha256:
                    raise RetouchingError("SOURCE_CHANGED", f"{fmt} content changed. Re-index the source for a new job.")
                stream.seek(0)
            with _open_photo(stream, fmt, source_id) as (image, metadata, orientation):
                try:
                    exif = read_exif(metadata.exif)
                    xmp = Xmp(metadata.xmp)
                    color_space = xmp.number(EXIF, "ColorSpace")
                    if orientation is None:
                        orientation = _orientation(exif, xmp, runtime, path)
                except Exception as exc:
                    raise RetouchingError("INPUT_METADATA_INVALID", f"Cannot read the {fmt} capture metadata.") from exc
                if exif.thumbnail_issue:
                    runtime.emit("INPUT_EXIF_THUMBNAIL_INVALID", "Ignored an invalid EXIF preview thumbnail.",
                                 level="WARNING", source_path=str(path), technical_cause=exif.thumbnail_issue)
                image.load()
                oriented = _opaque_pixels(_orient(image, orientation))
                if fmt == "DNG":
                    rgb, handling = oriented, "libraw-camera-wb-srgb"
                else:
                    rgb, handling = _convert_to_srgb(oriented, metadata, exif, color_space, runtime, path)
                yield rgb, metadata, orientation, handling
    except Exception as exc:
        _report_failure(runtime, exc, path, "INPUT_DECODE_FAILED",
                        f"Cannot decode source {fmt}. Check the file and its capture metadata.", "source_path")
        raise


def read_srgb_source(path, *, source_id, runtime, draft_size=None):
    fmt = SOURCE_FORMATS[Path(path).suffix.lower()]
    if fmt != "JPEG":
        return _read_photo(path, fmt, source_id=source_id, runtime=runtime)
    return read_srgb_jpeg(path, source_id=source_id, runtime=runtime, draft_size=draft_size)


def decode_source(path, *, source_id, runtime, expected_sha256=None):
    fmt = SOURCE_FORMATS[Path(path).suffix.lower()]
    if fmt == "JPEG":
        return decode_jpeg(path, source_id=source_id, runtime=runtime, expected_sha256=expected_sha256)
    started = perf_counter()
    with _read_photo(path, fmt, source_id=source_id, runtime=runtime, expected_sha256=expected_sha256) as (
            rgb, metadata, orientation, handling):
        state = ImageState.from_pixels(_linear_pixels(rgb), metadata)
        runtime.emit(f"INPUT_{fmt}_DECODED", f"Decoded still {fmt} through 8-bit RGB to oriented linear sRGB",
                     source_path=str(path), source_id=source_id, width=state.width, height=state.height,
                     orientation=orientation, color_handling=handling, duration_ms=(perf_counter() - started) * 1000)
    return state
