"""Source discovery and SDR decoding for local JPEG and still AVIF photos."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from time import perf_counter

from ..errors import RetouchingError
from ..pipeline.image_state import ImageState, SourceMetadata
from .exif import read_exif
from .jpeg import (MAX_HEADER_BYTES, MAX_PIXELS, JpegHeader, _convert_to_srgb, _linear_pixels,
                   _orient, _report_failure, decode_jpeg, inspect_jpeg_header, read_srgb_jpeg)
from .xmp import EXIF, Xmp

SOURCE_EXTENSIONS = (".jpg", ".jpeg", ".avif")
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
    if path.suffix.lower() == ".avif":
        with _open_avif(stream) as (image, _, _, _, _, metadata_bytes):
            return JpegHeader(image.width, image.height, metadata_bytes)
    return inspect_jpeg_header(stream)


@contextmanager
def _read_avif(path, *, source_id, runtime, expected_sha256=None):
    from PIL import Image

    try:
        with Path(path).open("rb") as stream:
            if expected_sha256 is not None:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha256:
                    raise RetouchingError("SOURCE_CHANGED", "AVIF content changed. Re-index the source for a new job.")
                stream.seek(0)
            with _open_avif(stream) as (image, icc, raw_exif, orientation, raw_xmp, _):
                metadata = SourceMetadata(source_id, raw_exif or b"", icc or b"", raw_xmp or b"")
                try:
                    exif = read_exif(metadata.exif)
                    color_space = Xmp(metadata.xmp).number(EXIF, "ColorSpace")
                except Exception as exc:
                    raise RetouchingError("INPUT_METADATA_INVALID", "Cannot read the AVIF capture metadata.") from exc
                if exif.thumbnail_issue:
                    runtime.emit("INPUT_EXIF_THUMBNAIL_INVALID", "Ignored an invalid EXIF preview thumbnail.",
                                 level="WARNING", source_path=str(path), technical_cause=exif.thumbnail_issue)
                image.load()
                oriented = _orient(image, orientation)
                if oriented.mode == "RGBA":
                    # The portrait pipeline is opaque RGB. Composite transparency on white.
                    background = Image.new("RGB", oriented.size, "white")
                    background.paste(oriented, mask=oriented.getchannel("A"))
                    oriented = background
                rgb, handling = _convert_to_srgb(oriented, metadata, exif, color_space, runtime, path)
                yield rgb, metadata, orientation, handling
    except Exception as exc:
        _report_failure(runtime, exc, path, "INPUT_DECODE_FAILED",
                        "Cannot decode source AVIF. Check that the file is readable and complete.", "source_path")
        raise


def read_srgb_source(path, *, source_id, runtime, draft_size=None):
    if Path(path).suffix.lower() == ".avif":
        return _read_avif(path, source_id=source_id, runtime=runtime)
    return read_srgb_jpeg(path, source_id=source_id, runtime=runtime, draft_size=draft_size)


def decode_source(path, *, source_id, runtime, expected_sha256=None):
    if Path(path).suffix.lower() != ".avif":
        return decode_jpeg(path, source_id=source_id, runtime=runtime, expected_sha256=expected_sha256)
    started = perf_counter()
    with _read_avif(path, source_id=source_id, runtime=runtime, expected_sha256=expected_sha256) as (
            rgb, metadata, orientation, handling):
        state = ImageState.from_pixels(_linear_pixels(rgb), metadata)
        runtime.emit("INPUT_AVIF_DECODED", "Decoded still AVIF through 8-bit RGB to oriented linear sRGB",
                     source_path=str(path), source_id=source_id, width=state.width, height=state.height,
                     orientation=orientation, color_handling=handling, duration_ms=(perf_counter() - started) * 1000)
    return state
