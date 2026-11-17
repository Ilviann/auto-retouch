"""Single-writer, manifest-last local artifacts for staged schedulers.

TIFF RGB float32 pixels are little-endian, C order, exactly 12 sample bytes/pixel.
Metadata remains opaque original bytes. Content-addressed blobs let context-only
states share pixels. A job journal must persist returned references separately;
this store alone is not a job scheduler or a power-loss-safe database.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from ..errors import RetouchingError
from ..json_io import atomic_json, read_json
from ..pipeline.image_state import ImageState, SourceMetadata
from .artifact_codec import (MAX_CONTEXT, MAX_METADATA_BYTES, OPTIONAL_METADATA, REPRESENTATION, decode_context, digest,
                             encode_context, fields, integer, require, text, validate_state)
from . import tiff_pixels
from .batch_paths import is_reparse

MANIFEST_LIMIT = 1024 * 1024
DEFAULT_PIXEL_LIMIT = 1024 * 1024 * 1024


def fingerprint(value):
    """Canonical SHA-256 for JSON snapshots, never Python's process-local hash()."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArtifactIdentity:
    """Digests of frozen execution inputs; callers supply complete snapshots.

    Prefix includes ordered stages/parameters/versions/enable states. Environment
    includes backend, actual device/providers, runtime versions and precision.
    Model assets includes versions/checksums, dependencies includes context
    contracts and upstream identities. Source SHA-256 hashes original file bytes.
    """
    source_id: str
    source_sha256: str
    pipeline_prefix_sha256: str
    environment_sha256: str
    model_assets_sha256: str
    dependencies_sha256: str
    seed: int
    representation: str = REPRESENTATION

    def __post_init__(self):
        require(text(self.source_id) and all(digest(v) for v in (self.source_sha256, self.pipeline_prefix_sha256,
                self.environment_sha256, self.model_assets_sha256, self.dependencies_sha256))
                and integer(self.seed) and self.seed < 2 ** 64 and self.representation == REPRESENTATION,
                "Artifact identity requires SHA-256 snapshots, a uint64 seed and a supported representation")

    @property
    def key(self):
        return fingerprint({"artifact_schema_version": 1, **asdict(self)})


@dataclass(frozen=True)
class ArtifactRef:
    store_id: str
    key: str
    manifest_sha256: str

    def __post_init__(self):
        require(isinstance(self.store_id, str) and len(self.store_id) == 32
                and all(c in "0123456789abcdef" for c in self.store_id)
                and digest(self.key) and digest(self.manifest_sha256), "Invalid artifact reference")


def _linked(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or is_reparse(info)


class ArtifactStore:
    def __init__(self, directory, marker, diagnostics, max_pixel_bytes):
        require(integer(max_pixel_bytes, 1), "Artifact pixel limit must be a positive byte count")
        self.directory = directory
        self.marker = marker
        self.diagnostics = diagnostics
        self.max_pixel_bytes = max_pixel_bytes

    @classmethod
    def create(cls, scratch_root, job_id, diagnostics, *, max_pixel_bytes=DEFAULT_PIXEL_LIMIT, image_id=None):
        try:
            require(text(job_id), "A nonempty job identity is required")
            require(integer(max_pixel_bytes, 1), "Artifact pixel limit must be positive")
            require(image_id is None or text(image_id), "An image identity is required for a dedicated checkpoint store")
            root = Path(scratch_root).resolve()
            root.mkdir(parents=True, exist_ok=True)
            store_id = uuid.uuid4().hex
            directory = root / f"auto-retouch-job-{store_id}"
            directory.mkdir()  # Never adopt an existing folder, including a user's photo directory.
            marker = {"schema_version": 1, "store_id": store_id, "job_id": job_id, "owner": "Auto ReTouch"}
            if image_id is not None:
                marker["image_id"] = image_id
            (directory / "blobs").mkdir()
            (directory / "states").mkdir()
            atomic_json(directory / "owner.json", marker)
            diagnostics.emit("ARTIFACT_STORE_CREATED", "Created dedicated job artifact storage", component="storage",
                             job_id=job_id, image_id=image_id, store_id=store_id)
            return cls(directory, marker, diagnostics, max_pixel_bytes)
        except Exception as exc:
            diagnostics.emit("ARTIFACT_STORE_FAILED", "Cannot create job storage; check scratch permissions and free space",
                             level="ERROR", exc_info=True, component="storage", job_id=job_id, image_id=image_id)
            raise RetouchingError("ARTIFACT_STORE_FAILED", f"Cannot create job artifact storage: {exc}") from exc

    @classmethod
    def open(cls, directory, job_id, diagnostics, *, max_pixel_bytes=DEFAULT_PIXEL_LIMIT, image_id=None):
        try:
            path = Path(directory).absolute()
            require(not _linked(path), "Job directory must not be a link/junction")
            path = path.resolve(strict=True)
            require(not _linked(path / "owner.json"), "Ownership marker must not be a link")
            marker = read_json(path / "owner.json", limit=4096)
            fields(marker, "schema_version store_id job_id owner" + (" image_id" if image_id is not None else ""))
            require(type(marker["schema_version"]) is int and marker["schema_version"] == 1
                    and marker["owner"] == "Auto ReTouch" and marker["job_id"] == job_id and text(job_id)
                    and marker.get("image_id") == image_id,
                    "Job storage ownership/version does not match")
            # ArtifactRef also validates the store UUID.
            ArtifactRef(marker["store_id"], "0" * 64, "0" * 64)
            require(path.name == f"auto-retouch-job-{marker['store_id']}", "Unexpected job directory name")
            result = cls(path, marker, diagnostics, max_pixel_bytes)
            result._guard()
            return result
        except Exception as exc:
            diagnostics.emit("ARTIFACT_STORE_FAILED", "Cannot reopen owned job artifacts; inspect ownership/version and paths",
                             level="ERROR", exc_info=True, component="storage", job_id=job_id, image_id=image_id)
            raise RetouchingError("ARTIFACT_STORE_FAILED", f"Cannot reopen job artifacts: {exc}") from exc

    def _emit(self, code, message, *, runtime=None, **details):
        if runtime is not None and runtime.job_id == self.marker["job_id"]:
            runtime.emit(code, message, store_id=self.marker["store_id"], **details)
        else:
            self.diagnostics.emit(code, message, component="storage", job_id=self.marker["job_id"],
                                  store_id=self.marker["store_id"], **details)

    def _guard(self):
        require(self.directory.resolve(strict=True) == self.directory and not _linked(self.directory),
                "Job storage moved or became a filesystem alias")
        marker = self.directory / "owner.json"
        require(not _linked(marker) and read_json(marker, limit=4096) == self.marker, "Job ownership marker changed")
        for name in ("blobs", "states"):
            path = self.directory / name
            require(path.is_dir() and not _linked(path), "Artifact subdirectory is missing or is a link/junction")

    def _path(self, kind, name):
        # Names are always validated hashes or generated UUIDs, never source paths.
        path = self.directory / kind / name
        require(not _linked(path), "Artifact file must not be a link/junction")
        return path

    def _read_blob(self, descriptor, limit):
        fields(descriptor, "sha256 bytes")
        require(digest(descriptor["sha256"]) and integer(descriptor["bytes"])
                and descriptor["bytes"] <= limit, "Invalid or oversized blob descriptor")
        path = self._path("blobs", descriptor["sha256"] + ".bin")
        require(path.stat().st_size == descriptor["bytes"], "Artifact blob size mismatch")
        with path.open("rb") as stream:
            data = stream.read(descriptor["bytes"] + 1)
        require(len(data) == descriptor["bytes"] and hashlib.sha256(data).hexdigest() == descriptor["sha256"],
                "Artifact blob checksum mismatch")
        return data

    def _put_blob(self, data):
        data = memoryview(data).cast("B")
        checksum = hashlib.sha256(data).hexdigest()
        descriptor = {"sha256": checksum, "bytes": data.nbytes}
        target = self._path("blobs", checksum + ".bin")
        if target.exists():
            # Validate without allocating another full pixel buffer.
            require(target.stat().st_size == data.nbytes, "Existing blob size mismatch")
            with target.open("rb") as stream:
                require(hashlib.file_digest(stream, "sha256").hexdigest() == checksum, "Existing blob checksum mismatch")
            return descriptor
        temporary = self._path("blobs", uuid.uuid4().hex + ".pending")
        try:
            with temporary.open("xb") as stream:
                for offset in range(0, data.nbytes, 1024 * 1024):
                    chunk = data[offset:offset + 1024 * 1024]
                    require(stream.write(chunk) == len(chunk), "Incomplete artifact blob write")
                stream.flush()
                os.fsync(stream.fileno())
            # Windows rename refuses to replace an existing destination. Single writer.
            temporary.rename(target)
        finally:
            temporary.unlink(missing_ok=True)
        return descriptor

    def _put_pixels(self, pixels):
        checksum = tiff_pixels.pixel_digest(pixels)
        target = self._path("blobs", tiff_pixels.filename(checksum, pixels.shape[1], pixels.shape[0]))
        if target.exists():
            require(target.stat().st_size <= self.max_pixel_bytes + tiff_pixels.TIFF_OVERHEAD_LIMIT, "Oversized existing TIFF")
            descriptor = tiff_pixels.describe(target, checksum)
            tiff_pixels.read(target, descriptor, pixels.shape[1], pixels.shape[0], self.max_pixel_bytes)
            return descriptor
        temporary = self._path("blobs", uuid.uuid4().hex + ".pending")
        try:
            tiff_pixels.write(temporary, pixels)
            descriptor = tiff_pixels.describe(temporary, checksum)
            # Validate the actual TIFF before publishing it or a state manifest.
            tiff_pixels.read(temporary, descriptor, pixels.shape[1], pixels.shape[0], self.max_pixel_bytes)
            temporary.rename(target)
            return descriptor
        finally:
            temporary.unlink(missing_ok=True)

    def commit(self, state, identity, *, runtime):
        """Return only after all blobs and the manifest are committed; never replace a prior state."""
        try:
            self._guard()
            require(isinstance(identity, ArtifactIdentity), "Expected an immutable artifact identity")
            require(runtime.job_id == self.marker["job_id"] and runtime.image_id is not None,
                    "Artifact commit requires matching job and image-stage diagnostic context")
            if "image_id" in self.marker:
                require(runtime.image_id == identity.source_id == self.marker["image_id"],
                        "Artifact source does not belong to this image checkpoint store")
            validate_state(state, self.max_pixel_bytes)
            require(state.metadata.source_id == identity.source_id, "State source does not match frozen artifact identity")
            manifest = {"schema_version": 1, "identity": asdict(identity),
                        "source_id": state.metadata.source_id, "representation": state.representation,
                        "width": state.width, "height": state.height,
                        "pixel_revision": state.pixel_revision, "geometry_revision": state.geometry_revision,
                        "context": [encode_context(e) for e in state.context]}
            # Validate the bounded JSON before writing any potentially large blob.
            require(len(json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode("utf-8")) < MANIFEST_LIMIT - 1024,
                    "State context exceeds manifest byte limit")
            manifest["pixels"] = self._put_pixels(state.pixels)
            manifest["exif"] = self._put_blob(state.metadata.exif)
            manifest["icc_profile"] = self._put_blob(state.metadata.icc_profile)
            for name in OPTIONAL_METADATA:
                value = getattr(state.metadata, name)
                if value:
                    manifest[name] = self._put_blob(value)
            path = self._path("states", identity.key + ".json")
            if path.exists():
                old = read_json(path, limit=MANIFEST_LIMIT)
                require(fingerprint(old) == fingerprint(manifest), "Cache identity already committed with a different state")
            else:
                atomic_json(path, manifest)  # Commit point. Failures leave blobs reclaimable, no complete state.
            ref = ArtifactRef(self.marker["store_id"], identity.key, fingerprint(manifest))
            self._emit("ARTIFACT_COMMITTED", "Lossless image/context state committed", runtime=runtime,
                       artifact_key=ref.key, pixel_bytes=state.pixels.nbytes, width=state.width, height=state.height)
            return ref
        except Exception as exc:
            self._emit("ARTIFACT_WRITE_FAILED", "State was not committed; check free space/permissions and preserve its predecessor",
                       runtime=runtime, level="ERROR", exc_info=True)
            raise RetouchingError("ARTIFACT_WRITE_FAILED", f"Cannot commit image state: {exc}") from exc

    def _manifest(self, ref):
        require(isinstance(ref, ArtifactRef) and ref.store_id == self.marker["store_id"], "Artifact belongs to another store")
        data = read_json(self._path("states", ref.key + ".json"), limit=MANIFEST_LIMIT)
        require(fingerprint(data) == ref.manifest_sha256, "Artifact manifest checksum mismatch")
        require(isinstance(data, dict), "Invalid artifact manifest")
        names = "schema_version identity source_id representation width height pixel_revision geometry_revision context pixels exif icc_profile"
        fields(data, names + "".join(" " + name for name in OPTIONAL_METADATA if name in data))
        require(type(data["schema_version"]) is int and data["schema_version"] == 1, "Unsupported artifact schema")
        identity = ArtifactIdentity(**data["identity"])
        require(identity.key == ref.key and data["representation"] == identity.representation, "Artifact identity mismatch")
        return data, identity

    def _load(self, ref, expected_identity):
        import numpy as np
        data, identity = self._manifest(ref)
        require(identity == expected_identity, "Cached state does not match frozen execution inputs")
        require(data["source_id"] == identity.source_id, "State source does not match frozen artifact identity")
        require(integer(data["width"], 1) and integer(data["height"], 1), "Invalid artifact dimensions")
        size = data["width"] * data["height"] * 12
        require(size <= self.max_pixel_bytes, "Pixel dimensions exceed configured byte budget")
        require(isinstance(data["context"], list) and len(data["context"]) <= MAX_CONTEXT, "Invalid context list")
        tiff_pixels.validate_descriptor(data["pixels"], self.max_pixel_bytes)
        path = self._path("blobs", tiff_pixels.filename(data["pixels"]["pixel_sha256"], data["width"], data["height"]))
        array = tiff_pixels.read(path, data["pixels"], data["width"], data["height"], self.max_pixel_bytes)
        # Own immutable bytes; close all TIFF handles before returning to the worker.
        pixels = np.frombuffer(array.tobytes(), dtype="<f4").reshape(array.shape)
        del array
        metadata = SourceMetadata(data["source_id"], self._read_blob(data["exif"], MAX_METADATA_BYTES),
                                  self._read_blob(data["icc_profile"], MAX_METADATA_BYTES),
                                  **{name: self._read_blob(data[name], MAX_METADATA_BYTES)
                                     for name in OPTIONAL_METADATA if name in data})
        state = ImageState(pixels, metadata, tuple(decode_context(c) for c in data["context"]),
                           data["pixel_revision"], data["geometry_revision"], data["representation"])
        validate_state(state, self.max_pixel_bytes)
        return state

    def read(self, ref, expected_identity, *, runtime=None):
        try:
            self._guard()
            state = self._load(ref, expected_identity)
            self._emit("ARTIFACT_VALIDATED", "Restored lossless state with verified identity and checksums", runtime=runtime,
                       artifact_key=ref.key)
            return state
        except Exception as exc:
            self._emit("ARTIFACT_INVALID", "Cannot reuse intermediate state. Check scratch storage or recompute from a valid dependency.",
                       runtime=runtime, level="ERROR", exc_info=True, artifact_key=ref.key if isinstance(ref, ArtifactRef) else None)
            raise RetouchingError("ARTIFACT_INVALID", f"Cannot restore image state: {exc}") from exc

    def discard(self, ref):
        """Caller journals a successor first. Only unlink the named manifest, retaining shared blobs."""
        try:
            self._guard()
            self._manifest(ref)
            self._path("states", ref.key + ".json").unlink()
            self._emit("ARTIFACT_DISCARDED", "Removed retired state manifest; shared blobs retained until collection", artifact_key=ref.key)
        except Exception as exc:
            self._emit("ARTIFACT_CLEANUP_FAILED", "Cannot retire state; retained artifacts need inspection", level="ERROR", exc_info=True)
            raise RetouchingError("ARTIFACT_CLEANUP_FAILED", f"Cannot retire state: {exc}") from exc

    def retain_image_checkpoint(self, ref, *, runtime):
        """Reclaim obsolete files in a store dedicated to one image.

        The caller supplies its only journaled manifest, or None after clearing
        the checkpoint. Blob payloads are validated on read before they can run.
        """
        try:
            self._guard()
            require(self.marker.get("image_id") == runtime.image_id and runtime.image_id is not None
                    and runtime.job_id == self.marker["job_id"], "Cleanup requires an owned image checkpoint store")
            keep = set()
            if ref is not None:
                try:
                    data, _ = self._manifest(ref)
                    tiff_pixels.validate_descriptor(data["pixels"], self.max_pixel_bytes)
                    require(integer(data["width"], 1) and integer(data["height"], 1), "Invalid artifact dimensions")
                    keep.add(tiff_pixels.filename(data["pixels"]["pixel_sha256"], data["width"], data["height"]))
                    for name in ("exif", "icc_profile", *OPTIONAL_METADATA):
                        if name in data:
                            descriptor = data[name]
                            fields(descriptor, "sha256 bytes")
                            require(digest(descriptor["sha256"]), "Invalid metadata digest")
                            keep.add(descriptor["sha256"] + ".bin")
                except Exception as exc:
                    self._emit("ARTIFACT_INVALID", "Cannot retain the journaled intermediate manifest", runtime=runtime,
                               level="ERROR", exc_info=True, artifact_key=ref.key)
                    raise RetouchingError("ARTIFACT_INVALID", f"Cannot restore image state: {exc}") from exc
            candidates = []
            for path in (self.directory / "states").iterdir():
                if ref is not None and path.name == ref.key + ".json":
                    continue
                if (path.suffix == ".json" and digest(path.stem)
                        or re.fullmatch(r"\.[0-9a-f]{64}\.json\.[a-z0-9_]+\.tmp", path.name)):
                    candidates.append(path)
            for path in (self.directory / "blobs").iterdir():
                if path.name not in keep and (path.suffix in (".bin", ".tif") and digest(path.stem)
                                              or re.fullmatch(r"[0-9a-f]{32}\.pending", path.name)):
                    candidates.append(path)
            # Validate the entire deletion set before removing any entries.
            for path in candidates:
                require(not _linked(path) and path.is_file(), "Obsolete artifact is a filesystem alias or non-file")
            self._guard()
            for path in candidates:
                path.unlink()
            if candidates:
                self._emit("ARTIFACT_COLLECTED", "Reclaimed obsolete image checkpoint files", runtime=runtime,
                           removed_files=len(candidates))
        except Exception as exc:
            if getattr(exc, "code", None) == "ARTIFACT_INVALID":
                raise
            self._emit("ARTIFACT_CLEANUP_FAILED", "Cannot reclaim image checkpoints; inspect scratch storage before resuming",
                       runtime=runtime, level="ERROR", exc_info=True)
            raise RetouchingError("ARTIFACT_CLEANUP_FAILED", f"Cannot reclaim image checkpoints: {exc}") from exc

    def collect(self):
        """Explicit, single-writer mark/sweep; malformed manifests stop cleanup before any deletion.

        Only exact owned blob names are eligible. Unknown files and atomic-write
        leftovers are preserved for inspection. Never delete the configured root.
        """
        try:
            self._guard()
            used = set()
            for path in (self.directory / "states").iterdir():
                require(path.suffix == ".json" and digest(path.stem) and not _linked(path),
                        "Unexpected state entry; inspect interrupted writes before collection")
                data = read_json(path, limit=MANIFEST_LIMIT)
                ref = ArtifactRef(self.marker["store_id"], path.stem, fingerprint(data))
                data, identity = self._manifest(ref)
                # Validate all referenced blobs/context before deleting anything.
                self._load(ref, identity)
                used.add(tiff_pixels.filename(data["pixels"]["pixel_sha256"], data["width"], data["height"]))
                used.update(data[name]["sha256"] + ".bin"
                            for name in ("exif", "icc_profile", *OPTIONAL_METADATA) if name in data)
            candidates = []
            for path in (self.directory / "blobs").iterdir():
                if path.suffix in (".bin", ".tif") and digest(path.stem) and path.name not in used:
                    require(not _linked(path) and path.is_file(), "Collection candidate is a filesystem alias or non-file")
                    candidates.append(path)
            removed = 0
            for path in candidates:
                self._guard()
                require(not _linked(path), "Collection candidate became a filesystem alias")
                path.unlink()
                removed += 1
            self._emit("ARTIFACT_COLLECTED", "Reclaimed unreferenced owned blobs", removed_blobs=removed)
            return removed
        except Exception as exc:
            self._emit("ARTIFACT_CLEANUP_FAILED", "Artifact collection stopped; inspect retained manifests and storage ownership",
                       level="ERROR", exc_info=True)
            raise RetouchingError("ARTIFACT_CLEANUP_FAILED", f"Cannot collect artifacts: {exc}") from exc
