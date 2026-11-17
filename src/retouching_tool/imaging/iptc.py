"""Carry Photoshop APP13 IPTC-IIM data without carrying old previews or editing state."""

import hashlib
import struct

from .exif import MAX_APP_PAYLOAD

PHOTOSHOP = b"Photoshop 3.0\0"
IPTC_IIM = 1028
IPTC_DIGEST = 1061


def read_iptc(payload):
    """Read one bounded Photoshop resource block and extract its IIM resource."""
    iptc = None
    offset = len(PHOTOSHOP)
    while offset < len(payload):
        if payload[offset:offset + 4] != b"8BIM" or offset + 7 > len(payload):
            raise ValueError("Invalid Photoshop APP13 resource header")
        resource, name_length = struct.unpack_from(">HB", payload, offset + 4)
        padded_name = (name_length + 2) & ~1  # Pascal string, padded to even bytes.
        length_offset = offset + 6 + padded_name
        if length_offset + 4 > len(payload):
            raise ValueError("Truncated Photoshop APP13 resource length")
        length, = struct.unpack_from(">I", payload, length_offset)
        start = length_offset + 4
        offset = start + ((length + 1) & ~1)
        if offset > len(payload):
            raise ValueError("Truncated Photoshop APP13 resource data")
        if resource == IPTC_IIM:
            if iptc is not None:
                raise ValueError("Multiple IPTC-IIM resources in Photoshop APP13")
            iptc = payload[start:start + length]
    return iptc


def iptc_marker(data):
    if not data:
        return b""
    if len(data) > MAX_APP_PAYLOAD:
        raise ValueError("IPTC-IIM data exceeds one JPEG APP13 segment")

    def resource(code, value):
        return b"8BIM" + struct.pack(">H2xI", code, len(value)) + value + b"\0" * (len(value) % 2)

    checksum = hashlib.md5(data, usedforsecurity=False).digest()  # Photoshop IPTC digest format.
    payload = PHOTOSHOP + resource(IPTC_IIM, data) + resource(IPTC_DIGEST, checksum)
    if len(payload) > MAX_APP_PAYLOAD:
        raise ValueError("IPTC-IIM data and digest exceed one JPEG APP13 segment")
    return b"\xff\xed" + struct.pack(">H", len(payload) + 2) + payload
