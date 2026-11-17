# Lossless working-state artifacts

Date: 2026-11-17. Status: adopted for the reference-state slice of milestone 3b.
Production JPEG color/EXIF handling and batch scheduling remain pending.

Use ordinary uncompressed TIFF for intermediate working pixels, through
`tifffile==2026.10.15`. The user questioned the initial raw-pixel draft and
explicitly authorized adding TIFF support to the main project virtual environment.
TIFF provides an established, inspectable container for exact float32 RGB samples.
Raw float files would require custom layout conventions; uint16 TIFF would
quantize the existing float32 representation. JPEG intermediates are excluded.

The implemented profile is one little-endian, contiguous, three-channel float32
RGB image with no compression, predictor, source orientation or embedded source
profile. Its description identifies normalized linear-sRGB and oriented full
resolution coordinates. Its Software tag identifies Auto ReTouch and its version.
Original encoded-image ICC data must not incorrectly describe working pixels.
Broader TIFF imports, compressed TIFF, working ICC interoperability and BigTIFF
acceptance require separate evidence. This is processing storage, not a new
user-facing TIFF source/export format.

Versioned JSON manifests still describe immutable source identity, dimensions,
representation, pixel/geometry revisions, typed context/provenance and checksummed
TIFF/EXIF/profile references. Original EXIF and profile bytes are separate opaque
blobs, preserving unsupported/proprietary data without reserializing it. Byte
preservation here does not validate transfer to a final JPEG.

Manifest publication follows flushed/validated blob writes. Returned references
include the canonical manifest SHA-256. Immutable cache identities bind source
content, ordered pipeline prefix, runtime/device/precision, model assets, context
dependencies and seed; callers must supply complete frozen snapshots. Context-only
results share a pixel TIFF. Explicit retirement and collection retain blobs needed
by another committed state. Unknown files and interrupted-write evidence remain.
The store is single-writer and must not collect while a commit is active.

Pixel payloads require 12 bytes/pixel plus actual TIFF overhead. Budget TIFF/JSON,
source metadata, auxiliary artifacts and transactional peaks in future disk
reservations. Current read/write validation may allocate another image-sized
buffer; buffers are per image, not per batch. A default 1 GiB sample limit is an
explicit rejection guard, never a downsampling rule; callers can configure it.
No 16 GB/500-photo resource acceptance is claimed.

`tifffile` is pure Python with NumPy, already present, as its required dependency.
No `imagecodecs` or compression extras are installed. Wheel metadata records
BSD-3-Clause; retain its copyright/license/disclaimer in distribution. No weights
are involved. The wheel SHA-256 is in `requirements/shared.lock` and the package
is a main `pyproject.toml` dependency. Installed only this package and refreshed
editable application metadata; no setup run, backend switch or neural probe was
performed. After installed-version checks, `pip check`, and TIFF checks, refreshed
only the lock hash in existing DirectML readiness metadata. Provider identity and
the other metadata fields remained unchanged.

See [verification](../verification/lossless-artifacts.md). Upstream references:
[tifffile](https://github.com/cgohlke/tifffile) and
[pinned release](https://pypi.org/project/tifffile/2026.10.15/).
