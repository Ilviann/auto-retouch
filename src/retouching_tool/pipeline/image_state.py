"""Owned read-only pixels with immutable source metadata and revisioned analysis."""

from dataclasses import dataclass
import math

from ..errors import RetouchingError
from .definition import ContextSpec


@dataclass(frozen=True)
class SourceMetadata:
    source_id: str
    exif: bytes = b""
    icc_profile: bytes = b""

    def __post_init__(self):
        if not isinstance(self.source_id, str) or not self.source_id or type(self.exif) is not bytes or type(self.icc_profile) is not bytes:
            raise ValueError("Source metadata requires an identity and immutable EXIF/profile bytes")


@dataclass(frozen=True)
class ImageStatistics:
    mean_luminance: float
    minimum: float
    maximum: float
    histogram: tuple[int, ...]
    clipped_low: int
    clipped_high: int
    pixel_count: int


@dataclass(frozen=True)
class ContextEntry:
    spec: ContextSpec
    producer_id: str
    producer_operation: str
    producer_version: int
    pixel_revision: int
    geometry_revision: int
    width: int
    height: int
    value: ImageStatistics | tuple[float, ...]
    source_id: str = ""
    producer_settings_hash: str = ""
    confidence: float | None = None
    model_ref: str | None = None
    coordinates: str = "full-resolution-oriented-pixels"

    def __post_init__(self):
        if (self.confidence is not None and (not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1)
                or self.coordinates != "full-resolution-oriented-pixels"):
            raise ValueError("Context confidence/coordinate convention is invalid")
        if not isinstance(self.value, ImageStatistics) and not (
                isinstance(self.value, tuple) and all(type(v) in (int, float) and math.isfinite(v) for v in self.value)):
            raise ValueError("Context payload must be immutable typed measurements")

    def compatible(self, state):
        return (self.source_id == state.metadata.source_id and self.geometry_revision == state.geometry_revision and self.width == state.width
                and self.height == state.height and self.pixel_revision <= state.pixel_revision
                and (not self.spec.pixel_sensitive or self.pixel_revision == state.pixel_revision))


@dataclass(frozen=True)
class ImageState:
    pixels: object
    metadata: SourceMetadata
    context: tuple[ContextEntry, ...] = ()
    pixel_revision: int = 0
    geometry_revision: int = 0
    representation: str = "linear-srgb-float32"

    @classmethod
    def from_pixels(cls, pixels, metadata, *, pixel_revision=0, geometry_revision=0, context=()):
        import numpy as np
        if any(type(revision) is not int or revision < 0 for revision in (pixel_revision, geometry_revision)):
            raise RetouchingError("IMAGE_REVISION_INVALID", "Image revisions must be nonnegative integers")
        array = np.asarray(pixels)
        if (array.dtype != np.float32 or array.ndim != 3 or array.shape[2] != 3
                or not array.shape[0] or not array.shape[1]):
            raise RetouchingError("IMAGE_REPRESENTATION_INVALID", "Expected nonempty H×W×3 float32 normalized linear-sRGB pixels")
        # Check strips instead of allocating a full-image validation mask.
        for start in range(0, array.shape[0], 64):
            strip = array[start:start + 64]
            if not np.isfinite(strip).all() or strip.min() < 0 or strip.max() > 1:
                raise RetouchingError("IMAGE_RANGE_INVALID", "Reference pixels must be finite and within [0, 1]")
        # A bytes-backed view cannot be made writable, unlike an owning ndarray
        # with only its writeable flag cleared. Never alias caller-owned memory.
        owned = np.frombuffer(array.tobytes(), dtype=np.float32).reshape(array.shape)
        return cls(owned, metadata, tuple(context), pixel_revision, geometry_revision)

    @property
    def width(self):
        return self.pixels.shape[1]

    @property
    def height(self):
        return self.pixels.shape[0]

    def entry(self, key):
        return next((entry for entry in self.context if entry.spec.key == key), None)
