"""Bounded classic-TIFF EXIF reader and writer for JPEG APP1.

Keep the byte order, type, count and value bytes of retained entries. Rebuild
known IFD pointers and omit the thumbnail IFD entirely. Final exports select
standard capture fields by directory and omit proprietary MakerNotes.
"""

from dataclasses import dataclass
import struct

from ..errors import RetouchingError

EXIF_HEADER = b"Exif\0\0"
MAX_APP_PAYLOAD = 65533
TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 129: 1}
EXIF_POINTER = 34665
GPS_POINTER = 34853
INTEROP_POINTER = 40965
POINTERS = {EXIF_POINTER, GPS_POINTER, INTEROP_POINTER}
MAKER_NOTE = 37500
ORIENTATION = 274
SOFTWARE = 305
COLOR_SPACE = 40961
PIXEL_X = 40962
PIXEL_Y = 40963

# CIPA DC-008 capture and descriptive tags that can carry over to an edited
# image. Directory pointers and output-dependent fields are written separately.
PRIMARY_CAPTURE_TAGS = {
    270, 271, 272, 282, 283, 296, 306, 315, 33432,  # description, camera, DPI, date and credits
}
EXIF_CAPTURE_TAGS = {
    0x829A, 0x829D, 0x8822, 0x8824, 0x8827, 0x8828, *range(0x8830, 0x8836),  # exposure and sensitivity
    0x9000, 0x9003, 0x9004, *range(0x9010, 0x9013),  # EXIF version, capture times and offsets
    *range(0x9201, 0x920B), 0x9214,  # exposure, focal length and subject area
    0x9286, *range(0x9290, 0x9293),  # user comment and subseconds
    *range(0x9400, 0x9406),  # environmental measurements
    0xA000, 0xA004, 0xA20B, 0xA20C, 0xA20E, 0xA20F, 0xA210, 0xA214, 0xA215, 0xA217,
    *range(0xA300, 0xA303), *range(0xA401, 0xA40D),  # sensor, scene and camera settings
    0xA420, *range(0xA430, 0xA43D), *range(0xA460, 0xA463),  # identity, credits, lens and composite images
}
# Some cameras used these IDs for private data before Exif 3.1 assigned them.
EXIF_31_CAPTURE_TAGS = {0x9287, *range(0xA40D, 0xA413)}

# Encoded layout values tied to the old image, including the obsolete JPEG-in-TIFF tags.
TIFF_LAYOUT_TAGS = {
    254, 255, 258, 259, 262, 263, 266, 273, 277, 278, 279, 284, 288, 289, 301,
    317, 318, 319, 320, 322, 323, 324, 325, 338, 339, 512, 513, 514, 515, 517, 518, 519,
    520, 521, 529, 530, 531, 532, 34675,
}


@dataclass(frozen=True)
class Entry:
    kind: int
    count: int
    value: bytes


@dataclass
class Exif:
    order: str
    primary: dict
    capture: dict
    gps: dict | None = None
    interop: dict | None = None
    had_thumbnail: bool = False
    thumbnail_issue: str | None = None

    def scalar(self, entries, tag, *, kinds=(3, 4), default=None):
        entry = entries.get(tag)
        if entry is None:
            return default
        if entry.count != 1 or entry.kind not in kinds:
            raise ValueError(f"Invalid EXIF numeric field 0x{tag:04x}")
        return struct.unpack(self.order + {3: "H", 4: "I"}[entry.kind], entry.value)[0]

    @property
    def orientation(self):
        value = self.scalar(self.primary, ORIENTATION, kinds=(3,), default=1)
        if not 1 <= value <= 8:
            raise ValueError(f"Invalid EXIF orientation {value}")
        return value

    @property
    def color_space(self):
        return self.scalar(self.capture, COLOR_SPACE, kinds=(3,))


class _Reader:
    def __init__(self, data, byte_limit):
        self.data = data
        self.remaining = byte_limit
        self.order = {b"II": "<", b"MM": ">"}.get(data[:2])
        if self.order is None or self.unpack("H", 2)[0] != 42:
            raise ValueError("Invalid EXIF TIFF header")
        self.directories = [(0, 8)]
        self.values = []

    def unpack(self, layout, offset):
        return struct.unpack_from(self.order + layout, self.data, offset)

    def ifd(self, offset, allowed_pointers=(), include=None):
        if offset < 8 or offset + 6 > len(self.data):
            raise ValueError("EXIF directory offset is outside the TIFF data")
        count, = self.unpack("H", offset)
        end = offset + 6 + count * 12
        if end > len(self.data) or any(offset < stop and start < end for start, stop in self.directories):
            raise ValueError("Truncated or overlapping EXIF directories")
        self.directories.append((offset, end))
        entries = {}
        for position in range(offset + 2, end - 4, 12):
            tag, kind, length = self.unpack("HHI", position)
            if include is not None and not include(tag):
                continue
            if tag in entries or kind not in TYPE_SIZES or not length:
                raise ValueError(f"Duplicate or invalid EXIF field 0x{tag:04x}")
            if tag == 330 or tag in POINTERS and tag not in allowed_pointers:
                raise ValueError(f"Unsupported EXIF directory reference 0x{tag:04x}")
            size = TYPE_SIZES[kind] * length
            self.remaining -= size
            if self.remaining < 0:
                raise ValueError("TIFF directory values exceed the metadata size limit")
            start = position + 8
            if size > 4:
                start, = self.unpack("I", start)
                if start < 8 or start + size > len(self.data):
                    raise ValueError(f"EXIF field 0x{tag:04x} points outside the TIFF data")
                self.values.append((start, start + size))
            entries[tag] = Entry(kind, length, self.data[start:start + size])
        return entries, self.unpack("I", end - 4)[0]

    def child(self, exif, entries, tag, allowed_pointers=()):
        pointer = exif.scalar(entries, tag, kinds=(4,))
        if pointer is None:
            return None
        fields, next_ifd = self.ifd(pointer, allowed_pointers)
        if next_ifd:
            raise ValueError("Unexpected chained EXIF subdirectory")
        del entries[tag]
        return fields


def read_exif(raw):
    if not raw:
        return Exif("<", {}, {})
    # AVIF Exif items can carry the TIFF directory without the JPEG APP1 prefix.
    data = raw.removeprefix(EXIF_HEADER)
    if len(data) < 8 or len(raw) > MAX_APP_PAYLOAD:
        if raw.startswith(EXIF_HEADER):
            raise ValueError("Invalid JPEG EXIF APP1 payload")
        raise ValueError("Invalid EXIF payload size")
    return read_tiff_directory(data, byte_limit=MAX_APP_PAYLOAD)


def read_tiff_directory(data, *, byte_limit, primary_filter=None, read_thumbnail=True):
    reader = _Reader(data, byte_limit)
    primary, thumbnail_offset = reader.ifd(reader.unpack("I", 4)[0], (EXIF_POINTER, GPS_POINTER), primary_filter)
    result = Exif(reader.order, primary, {}, had_thumbnail=bool(thumbnail_offset))
    result.capture = reader.child(result, primary, EXIF_POINTER, (INTEROP_POINTER,)) or {}
    result.gps = reader.child(result, primary, GPS_POINTER)
    result.interop = reader.child(result, result.capture, INTEROP_POINTER)
    if thumbnail_offset and read_thumbnail:
        thumbnail, next_ifd = reader.ifd(thumbnail_offset)
        start = result.scalar(thumbnail, 513)
        size = result.scalar(thumbnail, 514)
        if next_ifd:
            raise ValueError("Unexpected chained EXIF thumbnail directory")
        if (start is None) != (size is None) or (
                start is not None and (start < 8 or size < 1 or start + size > len(reader.data))):
            result.thumbnail_issue = "EXIF thumbnail data lies outside the APP1 segment or has an invalid length"
    if any(start < stop and begin < end for start, end in reader.values for begin, stop in reader.directories):
        raise ValueError("EXIF field data overlaps a directory")
    # Eagerly validate fields needed to interpret the source pixels.
    _ = result.orientation, result.color_space
    return result


def for_output(source, size, software):
    if 700 in source.primary or 34377 in source.primary:
        raise RetouchingError("OUTPUT_METADATA_UNSUPPORTED",
                              "XMP or Photoshop resources stored inside EXIF are unsupported. Keep the original JPEG.")
    old_software = source.primary.get(SOFTWARE)
    previous = ""
    if old_software is not None:
        if old_software.kind not in (2, 129) or not old_software.value.endswith(b"\0"):
            raise ValueError("Invalid EXIF Software field")
        previous = old_software.value.rstrip(b"\0").decode("utf-8" if old_software.kind == 129 else "ascii")
        if "\0" in previous:
            raise ValueError("EXIF Software contains an embedded terminator")

    def number(kind, value):
        return Entry(kind, 1, struct.pack(source.order + ("H" if kind == 3 else "I"), value))

    width, height = size
    primary = {tag: entry for tag, entry in source.primary.items() if tag in PRIMARY_CAPTURE_TAGS}
    primary.update({256: number(4, width), 257: number(4, height),
                    ORIENTATION: number(3, 1), SOFTWARE: ascii_entry(software)})
    if source.orientation >= 5 and 282 in primary and 283 in primary:
        primary[282], primary[283] = primary[283], primary[282]
    capture_tags = EXIF_CAPTURE_TAGS
    version = source.capture.get(0x9000)
    if (version and version.kind == 7 and version.count == 4
            and version.value.isdigit() and version.value >= b"0310"):
        capture_tags = capture_tags | EXIF_31_CAPTURE_TAGS
    capture = {tag: entry for tag, entry in source.capture.items() if tag in capture_tags}
    capture.update({COLOR_SPACE: number(3, 1), PIXEL_X: number(4, width), PIXEL_Y: number(4, height),
                    37121: Entry(7, 4, b"\x01\x02\x03\x00")})  # JPEG Y, Cb, Cr
    # Related image fields refer to a previous representation. R98 denotes sRGB.
    interop = None if source.interop is None else {
        **{tag: entry for tag, entry in source.interop.items() if tag == 2},  # InteroperabilityVersion
        1: ascii_entry("R98"),
    }
    gps = {tag: entry for tag, entry in (source.gps or {}).items() if 0 <= tag <= 31}
    return Exif(source.order, primary, capture, gps or None, interop), previous


def ascii_entry(value):
    encoded = value.encode("ascii") + b"\0"
    return Entry(2, len(encoded), encoded)


def write_exif(exif, *, size_limit=MAX_APP_PAYLOAD):
    order = exif.order
    data = bytearray(b"II" if order == "<" else b"MM")
    data.extend(struct.pack(order + "HI", 42, 0))

    def append(payload):
        if len(data) % 2:
            data.append(0)
        offset = len(data)
        data.extend(payload)
        if len(data) + len(EXIF_HEADER) > size_limit:
            raise ValueError("EXIF exceeds the metadata size limit after adding output fields")
        return offset

    def pointer(offset):
        return Entry(4, 1, struct.pack(order + "I", offset))

    def ifd(entries):
        directory = bytearray(struct.pack(order + "H", len(entries)))
        for tag, entry in sorted(entries.items()):
            directory.extend(struct.pack(order + "HHI", tag, entry.kind, entry.count))
            value = entry.value
            directory.extend(value.ljust(4, b"\0") if len(value) <= 4 else struct.pack(order + "I", append(value)))
        return append(directory + b"\0" * 4)

    primary, capture = dict(exif.primary), dict(exif.capture)
    if exif.interop is not None:
        capture[INTEROP_POINTER] = pointer(ifd(exif.interop))
    if exif.gps is not None:
        primary[GPS_POINTER] = pointer(ifd(exif.gps))
    primary[EXIF_POINTER] = pointer(ifd(capture))
    struct.pack_into(order + "I", data, 4, ifd(primary))
    return EXIF_HEADER + data
