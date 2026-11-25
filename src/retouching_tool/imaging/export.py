"""Encode and verify one pending local export before journaling and atomic publication."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
import os
from pathlib import Path
import re
import struct
import tempfile
from time import perf_counter

from ..errors import RetouchingError
from ..storage.batch_paths import check_output_target, publish_pending
from .exif import EXIF_HEADER, MAX_APP_PAYLOAD, Entry, Exif, for_output, read_exif, write_exif
from .export_settings import EXTENSIONS, ExportSettings, require_jxl
from .iptc import iptc_marker
from .jpeg import SOFTWARE, STRIP_ROWS, _report_failure, _srgb, _verify, encode_rgb_samples
from .tiff_export import write_tiff
from .xmp import Xmp


@dataclass
class ExportMetadata:
    exif: Exif
    raw_exif: bytes
    xmp: bytes
    profile: bytes
    iptc: bytes
    removed_thumbnail: bool


def _metadata(state, size, pipeline_sha256, fmt):
    original = read_exif(state.metadata.exif)
    expected, previous = for_output(original, size, SOFTWARE)
    iptc = state.metadata.iptc
    embedded_iptc = original.primary.get(33723)
    if embedded_iptc is not None:
        if iptc and iptc != embedded_iptc.value:
            raise RetouchingError("OUTPUT_METADATA_UNSUPPORTED", "Conflicting embedded IPTC resources.")
        iptc = embedded_iptc.value
    if fmt != "JPEG":
        expected.capture.pop(37121)  # JPEG YCbCr component configuration
        if iptc:
            expected.primary[33723] = Entry(7, len(iptc), iptc)
    raw = write_exif(expected, size_limit=MAX_APP_PAYLOAD if fmt == "JPEG" else 2 * MAX_APP_PAYLOAD + 1024)
    xmp = Xmp(state.metadata.xmp).for_output(size, SOFTWARE, datetime.now(timezone.utc).isoformat(
        timespec="milliseconds"), pipeline_sha256, previous, output_format=fmt)
    return ExportMetadata(expected, raw, xmp, _srgb().tobytes(), iptc, original.had_thumbnail)


def _save(stream, pixels, settings, meta):
    from PIL import Image, PngImagePlugin

    if settings.format == "TIFF":
        return write_tiff(stream, pixels, meta.exif, meta.xmp, meta.profile, settings.compression)
    with Image.fromarray(pixels) as image:
        if settings.format == "JPEG":
            image.save(stream, format="JPEG", **asdict(settings), exif=meta.raw_exif, xmp=meta.xmp,
                       icc_profile=meta.profile, extra=iptc_marker(meta.iptc))
        elif settings.format == "PNG":
            info = PngImagePlugin.PngInfo()
            info.add_itxt("XML:com.adobe.xmp", meta.xmp.decode("utf-8"))
            image.save(stream, format="PNG", compress_level=settings.compression, exif=meta.raw_exif,
                       icc_profile=meta.profile, pnginfo=info)
        elif settings.format == "JPEG XL":
            require_jxl()
            # The JXL Exif box starts with a four-byte offset to the TIFF header.
            image.save(stream, format="JXL", **asdict(settings), use_container=True, num_threads=2,
                       exif=bytes(4) + meta.raw_exif[len(EXIF_HEADER):], xmp=meta.xmp)
        else:
            image.save(stream, format="BMP", dpi=(settings.dpi, settings.dpi))


def _verify_jxl_xmp(path, xmp):
    # The Pillow plugin exposes EXIF and the decoded profile, but not the XML box.
    with path.open("rb") as stream:
        end = os.fstat(stream.fileno()).st_size
        while stream.tell() < end:
            size, kind = struct.unpack(">I4s", stream.read(8))
            if size < 8 or size - 8 > end - stream.tell():
                raise ValueError("Invalid JPEG XL container box")
            if kind == b"xml ":
                if size != len(xmp) + 8 or stream.read(len(xmp)) != xmp:
                    raise ValueError("Written JPEG XL XMP differs from the processing history")
                return
            stream.seek(size - 8, os.SEEK_CUR)
    raise ValueError("Missing JPEG XL XMP box")


def _verify_export(path, settings, size, pixels, meta, tiff_header):
    import numpy as np
    from PIL import Image, ImageCms

    fmt = settings.format
    if fmt == "JPEG":
        _verify(path, size, meta.exif, meta.raw_exif, meta.xmp, meta.profile, meta.iptc)
        return
    if fmt == "TIFF":
        with path.open("rb") as stream:
            if stream.read(len(tiff_header)) != tiff_header:
                raise ValueError("Written TIFF capture metadata or strip directory changed")
    if fmt == "JPEG XL":
        _verify_jxl_xmp(path, meta.xmp)
    with Image.open(path, formats=("JXL" if fmt == "JPEG XL" else fmt,)) as image:
        if image.size != size or image.mode != "RGB":
            raise ValueError("Written image dimensions or RGB mode differ")
        image.load()
        if fmt == "PNG":
            if (image.info.get("icc_profile") != meta.profile or image.info.get("exif") != meta.raw_exif
                    or image.info.get("XML:com.adobe.xmp") != meta.xmp.decode("utf-8")):
                raise ValueError("Written PNG ICC, EXIF or XMP differs")
        elif fmt == "JPEG XL":
            profile = ImageCms.ImageCmsProfile(BytesIO(image.info["icc_profile"]))
            if (image.info.get("exif") != meta.raw_exif[len(EXIF_HEADER):]
                    or ImageCms.getProfileDescription(profile).strip() != "sRGB"):
                raise ValueError("Written JPEG XL EXIF or decoded sRGB profile differs")
            if not settings.lossless:
                return
        elif fmt == "BMP":
            if image.getexif() or any(abs(value - settings.dpi) > 0.02 for value in image.info["dpi"]):
                raise ValueError("Written BMP contains EXIF or has the wrong resolution")
        for y in range(0, image.height, STRIP_ROWS):
            bottom = min(y + STRIP_ROWS, image.height)
            if not np.array_equal(np.asarray(image.crop((0, y, image.width, bottom))), pixels[y:bottom]):
                raise ValueError(f"Lossless {fmt} pixels differ at row {y}")


def export_image(state, destination, *, settings, pipeline_sha256, runtime, before_publish=None, replace_stamp=None):
    pending = None
    started = perf_counter()
    try:
        if not isinstance(settings, ExportSettings) or not isinstance(pipeline_sha256, str) or not re.fullmatch(
                r"[0-9a-f]{64}", pipeline_sha256):
            raise RetouchingError("OUTPUT_SETTINGS_INVALID", "Export requires valid settings and a pipeline SHA-256.")
        fmt = settings.format
        destination = Path(destination)
        if destination.suffix.lower() not in EXTENSIONS[fmt] or os.path.isreserved(destination.name):
            raise RetouchingError("OUTPUT_PATH_INVALID", f"Choose a valid {fmt} destination filename.")
        destination = destination.parent.resolve(strict=True) / destination.name
        check_output_target(destination, replace_stamp)
        try:
            pixels = encode_rgb_samples(state)
        except ValueError as exc:
            raise RetouchingError("OUTPUT_STATE_INVALID", "Invalid RGB image state or dimensions for export.") from exc
        size = (state.width, state.height)
        try:
            meta = None if fmt == "BMP" else _metadata(state, size, pipeline_sha256, fmt)
        except RetouchingError:
            raise
        except Exception as exc:
            raise RetouchingError("OUTPUT_METADATA_INVALID", "Cannot prepare capture metadata for export.") from exc
        del state
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".auto-retouch-", suffix=".pending",
                                         delete=False) as stream:
            pending = Path(stream.name)
            header = _save(stream, pixels, settings, meta)
            stream.flush()
            os.fsync(stream.fileno())
        if fmt == "JPEG" or (fmt == "JPEG XL" and not settings.lossless):
            pixels = None
        try:
            _verify_export(pending, settings, size, pixels, meta, header)
        except Exception as exc:
            raise RetouchingError("OUTPUT_VALIDATION_FAILED", f"{fmt} failed export verification. Check technical details.") from exc
        if before_publish is not None:
            before_publish(pending, destination)
        try:
            publish_pending(pending, destination, replace_stamp)
        except FileExistsError as exc:
            raise RetouchingError("OUTPUT_COLLISION", "Destination was created during export. Choose another filename.") from exc
        pending = None
        code = "JXL" if fmt == "JPEG XL" else fmt
        runtime.emit(f"OUTPUT_{code}_VERIFIED", f"Published verified {fmt} export", output_path=str(destination),
                     width=size[0], height=size[1], pipeline_sha256=pipeline_sha256, **asdict(settings),
                     metadata_policy="omitted" if meta is None else "standard_exif",
                     removed_exif_thumbnail=meta.removed_thumbnail if meta else False,
                     duration_ms=(perf_counter() - started) * 1000)
        return destination
    except Exception as exc:
        _report_failure(runtime, exc, destination, "OUTPUT_WRITE_FAILED",
                        "Cannot write export. Check the destination permissions and free space.", "output_path")
        raise
    finally:
        if pending is not None:
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                runtime.emit("OUTPUT_CLEANUP_FAILED", "Could not remove the incomplete export. Inspect the pending file.",
                             output_path=str(destination), pending_path=str(pending), level="ERROR", exc_info=True)
