"""Inspect the JPEG XL canvas before asking the pinned codec to allocate pixels."""

from contextlib import contextmanager
from io import BytesIO
import os

from ..errors import RetouchingError
from ..pipeline.image_state import SourceMetadata
from .export_settings import require_jxl
from .jpeg import MAX_HEADER_BYTES, MAX_PIXELS, JpegHeader, _metadata, _open_jpeg


def _codestream_prefix(stream, end):
    signature = stream.read(12)
    if signature.startswith(b"\xff\x0a"):
        stream.seek(0)
        return stream.read(64)
    if signature != b"\0\0\0\x0cJXL \r\n\x87\n":
        raise ValueError("Invalid JPEG XL signature")
    prefix = bytearray()
    while stream.tell() < end and len(prefix) < 64:
        start = stream.tell()
        header = stream.read(8)
        size, kind = int.from_bytes(header[:4], "big"), header[4:]
        if size == 1:
            size = int.from_bytes(stream.read(8), "big")
        elif size == 0:
            size = end - start
        stop = start + size
        if len(header) != 8 or not stream.tell() <= stop <= end:
            raise ValueError("Invalid JPEG XL box length")
        if kind == b"jxlp":
            if stop - stream.tell() < 4:
                raise ValueError("Incomplete JPEG XL codestream segment")
            stream.read(4)
        if kind in (b"jxlc", b"jxlp"):
            prefix.extend(stream.read(min(stop - stream.tell(), 64 - len(prefix))))
        stream.seek(stop)
    return prefix


def inspect_jxl(stream):
    require_jxl()
    end = os.fstat(stream.fileno()).st_size
    if end > 4 * MAX_PIXELS + MAX_HEADER_BYTES:
        raise RetouchingError("INPUT_IMAGE_TOO_LARGE", "JPEG XL exceeds the compressed input size limit.")
    prefix = _codestream_prefix(stream, end)
    if prefix[:2] != b"\xff\x0a":
        raise ValueError("Missing JPEG XL codestream")
    bits = int.from_bytes(prefix, "little")
    offset = 16

    def take(count):
        nonlocal offset
        if offset + count > len(prefix) * 8:
            raise ValueError("Truncated JPEG XL size header")
        value = (bits >> offset) & ((1 << count) - 1)
        offset += count
        return value

    # ISO/IEC 18181-1 SizeHeader, followed by the optional ImageMetadata fields.
    def size_header(preview=False):
        small = take(1)

        def dimension():
            if preview:
                bases, widths = ((16, 32, 1, 33), (0, 0, 5, 9)) if small else (
                    (1, 65, 321, 1345), (6, 8, 10, 12))
                selector = take(2)
                return (bases[selector] + take(widths[selector])) * (8 if small else 1)
            if small:
                return (1 + take(5)) * 8
            return 1 + take((9, 13, 18, 30)[take(2)])

        height = dimension()
        ratio = take(3)
        if not ratio:
            return dimension(), height
        num, den = ((1, 1), (6, 5), (4, 3), (3, 2), (16, 9), (5, 4), (2, 1))[ratio - 1]
        return height * num // den, height

    width, height = size_header()
    if width * height > MAX_PIXELS or max(width, height) > 65500:
        raise RetouchingError("INPUT_IMAGE_TOO_LARGE", "JPEG XL exceeds the working image dimensions.")
    if not take(1) and take(1):
        take(3)  # Codestream orientation is applied by libjxl.
        if take(1):
            size_header()
        if take(1):
            size_header(preview=True)
        if take(1):
            raise RetouchingError("INPUT_IMAGE_SEQUENCE", "Choose a still JPEG XL photo or extract one frame.")
    return JpegHeader(width, height, min(end, MAX_HEADER_BYTES))


def _metadata_boxes(boxes):
    packets = {}
    for box in boxes:
        kind = box.box_type
        if kind not in (b"Exif", b"xml ", b"brob"):
            continue
        data = box.data
        if kind == b"brob":
            kind = data[:4]
            if kind not in (b"Exif", b"xml "):
                continue
            data = _decompress_metadata(data[4:])
        if kind in packets or not data or len(data) > MAX_HEADER_BYTES:
            raise ValueError("Duplicate, empty or oversized JPEG XL metadata box")
        if kind == b"Exif" and not data.startswith((b"II", b"MM", b"Exif\0\0")):
            data = data[4 + int.from_bytes(data[:4], "big"):]
            if not data:
                raise ValueError("Invalid TIFF offset in JPEG XL Exif box")
        packets[kind] = data
    return packets


def _decompress_metadata(data):
    # Use the same Brotli decoder shipped with the pinned Windows JPEG XL wheel.
    import ctypes
    from importlib.metadata import distribution

    dist = distribution("pillow-jxl-plugin")
    dll, = (dist.locate_file(f) for f in dist.files if f.name.startswith("libbrotlidec-") and f.suffix == ".dll")
    decompress = ctypes.CDLL(str(dll)).BrotliDecoderDecompress
    decompress.argtypes = (ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p)
    output = ctypes.create_string_buffer(65536 + 4)
    size = ctypes.c_size_t(len(output))
    if decompress(len(data), data, ctypes.byref(size), output) != 1:
        raise ValueError("Invalid or oversized compressed JPEG XL capture metadata")
    return output[:size.value]


@contextmanager
def open_jxl(stream, source_id):
    from PIL import Image
    from pillow_jxl import Decoder

    stream.seek(0)
    reconstructed, info, raw, profile, boxes = Decoder(num_threads=2)(stream.read())
    if reconstructed:
        with _open_jpeg(BytesIO(raw)) as (image, _):
            yield image, _metadata(image, source_id), None
        return
    packets = _metadata_boxes(boxes)
    metadata = SourceMetadata(source_id, packets.get(b"Exif", b""), profile or b"", packets.get(b"xml ", b""))
    with Image.frombytes(info.mode, (info.width, info.height), raw) as image:
        yield image, metadata, 1
