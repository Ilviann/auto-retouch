"""Write an RGB TIFF whose primary IFD owns both the pixel layout and the source capture IFDs."""

from dataclasses import replace
import struct
import zlib

from .exif import EXIF_HEADER, Entry, write_exif


def write_tiff(stream, pixels, exif, xmp, profile, compression):
    height, width, _ = pixels.shape
    rows = max(1, 65536 // (3 * width))
    strip_count = (height + rows - 1) // rows

    def numbers(kind, *values):
        layout = "H" if kind == 3 else "I"
        return Entry(kind, len(values), struct.pack(f"{exif.order}{len(values)}{layout}", *values))

    tags = {**exif.primary, 258: numbers(3, 8, 8, 8), 259: numbers(3, 8 if compression == "Deflate" else 1),
            262: numbers(3, 2), 277: numbers(3, 3), 278: numbers(4, rows), 284: numbers(3, 1),
            700: Entry(1, len(xmp), xmp), 34675: Entry(7, len(profile), profile)}

    def header(offsets, counts):
        directory = replace(exif, primary={**tags, 273: numbers(4, *offsets), 279: numbers(4, *counts)})
        return write_exif(directory, size_limit=2 * 1024 * 1024)[len(EXIF_HEADER):]

    # Reserve the fixed-size IFDs, stream strips, then fill in their actual byte ranges.
    placeholders = [0] * strip_count
    stream.write(header(placeholders, placeholders))
    offsets, counts = [], []
    for start in range(0, height, rows):
        strip = memoryview(pixels[start:start + rows]).cast("B")
        if compression == "Deflate":
            strip = zlib.compress(strip)
        offsets.append(stream.tell())
        counts.append(stream.write(strip))
    stream.seek(0)
    metadata = header(offsets, counts)
    stream.write(metadata)
    return metadata
