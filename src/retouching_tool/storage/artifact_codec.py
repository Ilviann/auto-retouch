"""Version-1 lossless state manifest codec. No pickle or executable payloads.

Dense masks need a separate versioned codec when a mask contract is introduced.
Unknown payloads fail rather than being dropped or coerced to measurements.
"""

from dataclasses import asdict
import math
import re

from ..pipeline.definition import ContextSpec
from ..pipeline.image_state import ContextEntry, ImageState, ImageStatistics, SourceMetadata

REPRESENTATION = "linear-srgb-float32"
MAX_METADATA_BYTES = 16 * 1024 * 1024
OPTIONAL_METADATA = ("xmp", "iptc")
MAX_CONTEXT = 256


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def fields(value, names):
    require(isinstance(value, dict) and set(value) == set(names.split()), "Unexpected manifest fields")


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def text(value):
    return isinstance(value, str) and 0 < len(value) <= 256


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_context(entry, state):
    spec = entry.spec
    require(isinstance(spec, ContextSpec) and text(spec.key) and integer(spec.version, 1)
            and type(spec.pixel_sensitive) is bool, "Invalid context specification")
    require(text(entry.producer_id) and text(entry.producer_operation) and integer(entry.producer_version, 1)
            and digest(entry.producer_settings_hash), "Invalid context producer/settings identity")
    require(all(integer(v) for v in (entry.pixel_revision, entry.geometry_revision))
            and integer(entry.width, 1) and integer(entry.height, 1), "Invalid context coordinates/revisions")
    require(entry.coordinates == "full-resolution-oriented-pixels" and entry.compatible(state),
            "Stale context or incompatible source/coordinates")
    require(entry.model_ref is None or text(entry.model_ref), "Invalid context model reference")
    require(entry.confidence is None or finite(entry.confidence) and 0 <= entry.confidence <= 1,
            "Invalid context confidence")
    value = entry.value
    if isinstance(value, ImageStatistics):
        require(all(finite(v) and 0 <= v <= 1 for v in (value.mean_luminance, value.minimum, value.maximum))
                and value.minimum <= value.mean_luminance <= value.maximum, "Invalid statistics range")
        require(type(value.histogram) is tuple and len(value.histogram) == 256
                and all(integer(v) for v in value.histogram), "Invalid statistics histogram")
        require(integer(value.pixel_count, 1) and value.pixel_count == entry.width * entry.height
                and sum(value.histogram) == value.pixel_count
                and all(integer(v) and v <= value.pixel_count for v in (value.clipped_low, value.clipped_high)),
                "Invalid statistics counts")
    else:
        require(type(value) is tuple and len(value) <= 4096 and all(finite(v) for v in value),
                "Unsupported context payload; a versioned codec is required")


def validate_state(state, max_pixel_bytes):
    import numpy as np
    require(isinstance(state, ImageState) and state.representation == REPRESENTATION,
            "Unsupported image state representation")
    pixels = state.pixels
    require(isinstance(pixels, np.ndarray) and pixels.dtype == np.dtype("<f4")
            and pixels.ndim == 3 and pixels.shape[2] == 3 and pixels.shape[0] > 0 and pixels.shape[1] > 0
            and pixels.flags.c_contiguous and not pixels.flags.writeable,
            "Expected contiguous read-only little-endian float32 RGB pixels")
    require(pixels.nbytes <= max_pixel_bytes, "Image exceeds configured artifact read/write byte limit")
    require(integer(state.pixel_revision) and integer(state.geometry_revision), "Invalid image revisions")
    require(isinstance(state.metadata, SourceMetadata) and text(state.metadata.source_id)
            and all(type(v) is bytes and len(v) <= MAX_METADATA_BYTES
                    for v in (state.metadata.exif, state.metadata.icc_profile, state.metadata.xmp, state.metadata.iptc)),
            "Invalid source metadata")
    for start in range(0, state.height, 64):
        strip = pixels[start:start + 64]
        require(np.isfinite(strip).all() and strip.min() >= 0 and strip.max() <= 1, "Invalid float32 samples")
    require(type(state.context) is tuple and len(state.context) <= MAX_CONTEXT, "Too many context entries")
    keys = set()
    for entry in state.context:
        require(isinstance(entry, ContextEntry), "Invalid context entry")
        validate_context(entry, state)
        require(entry.spec.key not in keys, "Duplicate context key")
        keys.add(entry.spec.key)


def encode_context(entry):
    data = asdict(entry)
    data["value"] = ({"kind": "statistics-v1", "data": asdict(entry.value)}
                     if isinstance(entry.value, ImageStatistics)
                     else {"kind": "measurements-v1", "data": list(entry.value)})
    return data


def decode_context(data):
    fields(data, "spec producer_id producer_operation producer_version pixel_revision geometry_revision width height value "
           "source_id producer_settings_hash confidence model_ref coordinates")
    fields(data["spec"], "key version pixel_sensitive")
    fields(data["value"], "kind data")
    payload = data["value"]["data"]
    if data["value"]["kind"] == "statistics-v1":
        fields(payload, "mean_luminance minimum maximum histogram clipped_low clipped_high pixel_count")
        require(isinstance(payload["histogram"], list), "Expected histogram list")
        value = ImageStatistics(**{**payload, "histogram": tuple(payload["histogram"])})
    elif data["value"]["kind"] == "measurements-v1":
        require(isinstance(payload, list), "Expected measurement list")
        value = tuple(payload)
    else:
        raise ValueError("Unknown context payload codec")
    return ContextEntry(**{**data, "spec": ContextSpec(**data["spec"]), "value": value})
