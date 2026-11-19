"""Owned read-only pixels with immutable source metadata and revisioned analysis."""

from dataclasses import dataclass
import math
import re

from ..errors import RetouchingError
from .definition import ContextSpec

RGB_FLOAT32_BYTES_PER_PIXEL = 3 * 4
PERSON_FOREGROUND = ContextSpec("person.foreground", 1)
EXPOSED_SKIN = ContextSpec("skin.exposed", 1)
FACE_REGIONS = ContextSpec("face.regions", 1, pixel_sensitive=False)
MAX_FOREGROUND_GRID = 512


@dataclass(frozen=True)
class SourceMetadata:
    source_id: str
    exif: bytes = b""
    icc_profile: bytes = b""
    xmp: bytes = b""
    iptc: bytes = b""

    def __post_init__(self):
        if (not isinstance(self.source_id, str) or not self.source_id
                or any(type(value) is not bytes for value in (self.exif, self.icc_profile, self.xmp, self.iptc))):
            raise ValueError("Source metadata requires an identity and immutable EXIF/profile/XMP/IPTC bytes")


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
class PersonForegroundMask:
    """Binary row-major grid covering the entire oriented image rectangle."""

    width: int
    height: int
    labels: bytes

    def __post_init__(self):
        if (not all(type(n) is int and 1 <= n <= MAX_FOREGROUND_GRID for n in (self.width, self.height))
                or type(self.labels) is not bytes or len(self.labels) != self.width * self.height
                or self.labels.count(0) + self.labels.count(1) != len(self.labels)):
            raise ValueError("Person foreground requires a bounded binary grid")


@dataclass(frozen=True)
class ExposedSkinMask:
    """Full-frame oriented grid. Zero excludes a cell, 1 selects body and 2 selects face."""

    width: int
    height: int
    labels: bytes

    def __post_init__(self):
        if (not all(type(n) is int and 1 <= n <= 256 for n in (self.width, self.height))
                or type(self.labels) is not bytes or len(self.labels) != self.width * self.height
                or sum(self.labels.count(i) for i in (0, 1, 2)) != len(self.labels)):
            raise ValueError("Exposed skin requires a bounded grid of background, body and face labels")


@dataclass(frozen=True)
class FaceRegion:
    """Half-open oriented pixel bounds. Optional packed binary mask spans the rectangle, row-major MSB first."""

    left: int
    top: int
    right: int
    bottom: int
    mask_width: int = 0
    mask_height: int = 0
    mask: bytes = b""

    def __post_init__(self):
        if (not all(type(v) is int for v in (self.left, self.top, self.right, self.bottom,
                                            self.mask_width, self.mask_height))
                or not 0 <= self.left < self.right or not 0 <= self.top < self.bottom
                or type(self.mask) is not bytes or self.mask_width < 0 or self.mask_height < 0
                or bool(self.mask_width) != bool(self.mask_height)
                or len(self.mask) != (self.mask_width * self.mask_height + 7) // 8):
            raise ValueError("Invalid face rectangle or packed mask dimensions")


@dataclass(frozen=True)
class FaceRegions:
    regions: tuple[FaceRegion, ...]

    def __post_init__(self):
        if (type(self.regions) is not tuple or len(self.regions) > 256
                or not all(isinstance(item, FaceRegion) for item in self.regions)
                or sum(len(item.mask) for item in self.regions) > 512 * 1024):
            raise ValueError("Face regions exceed the supported context budget")


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
    value: ImageStatistics | PersonForegroundMask | ExposedSkinMask | FaceRegions | tuple[float, ...]
    source_id: str = ""
    producer_settings_hash: str = ""
    confidence: float | None = None
    model_ref: str | None = None
    coordinates: str = "full-resolution-oriented-pixels"

    def __post_init__(self):
        if (self.confidence is not None and (not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1)
                or self.coordinates != "full-resolution-oriented-pixels"):
            raise ValueError("Context confidence/coordinate convention is invalid")
        is_foreground = isinstance(self.value, PersonForegroundMask)
        if is_foreground != (self.spec == PERSON_FOREGROUND):
            raise ValueError("Person foreground context requires the versioned foreground mask payload")
        if isinstance(self.value, ExposedSkinMask) != (self.spec.key == EXPOSED_SKIN.key):
            raise ValueError("Exposed skin context requires a typed mask")
        if isinstance(self.value, ExposedSkinMask) and (self.spec != EXPOSED_SKIN or
                not isinstance(self.model_ref, str) or not self.model_ref):
            raise ValueError("Invalid exposed skin model or context version")
        if isinstance(self.value, FaceRegions) != (self.spec.key == FACE_REGIONS.key):
            raise ValueError("Face region context requires a typed payload")
        if isinstance(self.value, (ExposedSkinMask, FaceRegions)) and (
                not all(isinstance(v, str) and v for v in (self.source_id, self.producer_id, self.producer_operation))
                or type(self.producer_version) is not int or self.producer_version < 1
                or not isinstance(self.producer_settings_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.producer_settings_hash)
                or not all(type(v) is int and v > 0 for v in (self.width, self.height))
                or not all(type(v) is int and v >= 0 for v in (self.geometry_revision, self.pixel_revision))):
            raise ValueError("Invalid region context provenance or image dimensions")
        if isinstance(self.value, FaceRegions) and (self.spec != FACE_REGIONS
                or any(face.right > self.width or face.bottom > self.height for face in self.value.regions)):
            raise ValueError("Invalid face region provenance, version or bounds")
        if not isinstance(self.value, (ImageStatistics, PersonForegroundMask, ExposedSkinMask, FaceRegions)) and not (
                isinstance(self.value, tuple) and all(type(v) in (int, float) and math.isfinite(v) for v in self.value)):
            raise ValueError("Context payload must be immutable typed data")

    def compatible(self, state):
        """Stored outputs describe the producer's pixels in this image's coordinate frame."""
        return (self.source_id == state.metadata.source_id and self.geometry_revision == state.geometry_revision and self.width == state.width
                and self.height == state.height and self.pixel_revision <= state.pixel_revision)


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
