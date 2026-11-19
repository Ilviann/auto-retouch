"""Typed inputs and settings reconstructed from versioned job data."""

from dataclasses import asdict, dataclass
from importlib import metadata
import os
from pathlib import Path
import platform
import sys

from .. import __version__
from ..environment import RUNTIMES
from ..imaging.export_settings import ExportSettings, encode_settings, export_relative_path, output_reservation, parse_settings
from ..imaging.jpeg import MAX_PIXELS
from ..imaging.source import SOURCE_EXTENSIONS
from ..pipeline.definition import PipelineDefinition, parse_pipeline
from ..pipeline.image_state import RGB_FLOAT32_BYTES_PER_PIXEL
from ..storage.artifact_codec import digest, fields, integer, require
from ..storage.artifacts import fingerprint
from ..storage.batch_paths import FileStamp


def runtime_versions(backend, pytorch_backend=None):
    return {"app": __version__, "python": sys.version.split()[0], "windows": platform.version(),
            "pillow": metadata.version("Pillow"), "numpy": metadata.version("numpy"),
            "onnxruntime": metadata.version(RUNTIMES[backend]), "representation": "linear-srgb-float32",
            **({"pytorch": metadata.version("torch")} if pytorch_backend is not None else {})}


def model_manifests(pipeline, catalog):
    refs = sorted({stage.model_ref for stage in pipeline.stages if stage.enabled and stage.model_ref is not None})
    return tuple(asdict(catalog.manifest(ref)) for ref in refs)


def _absolute(value):
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "Invalid absolute job path")
    return path


def _identity(data):
    require(isinstance(data, list) and len(data) == 2 and integer(data[0]) and integer(data[1], 1),
            "Invalid file or directory identity")
    return tuple(data)


def parse_stamp(data):
    fields(data, "identity size modified_ns")
    require(integer(data["size"]) and type(data["modified_ns"]) is int, "Invalid saved file attributes")
    return FileStamp(_identity(data["identity"]), data["size"], data["modified_ns"])


@dataclass(frozen=True)
class FrozenInput:
    image_id: str
    path: Path
    relative_path: Path
    stamp: FileStamp
    sha256: str | None  # Skipped photos have no content to verify on resume.
    width: int
    height: int
    header_bytes: int = 0
    replace_stamp: FileStamp | None = None

    @property
    def working_bytes(self):
        return self.width * self.height * RGB_FLOAT32_BYTES_PER_PIXEL

    @classmethod
    def parse(cls, data):
        fields(data, "image_id path relative_path stamp sha256 width height" +
               (" header_bytes" if "header_bytes" in data else "") +
               (" replace_stamp" if "replace_stamp" in data else ""))
        relative = Path(data["relative_path"])
        require(not relative.anchor and relative.parts and not any(part == ".." or os.path.isreserved(part)
                                                                 for part in relative.parts)
                and relative.suffix.lower() in SOURCE_EXTENSIONS, "Invalid job source path")
        require(isinstance(data["image_id"], str) and data["image_id"]
                and (data["sha256"] is None or digest(data["sha256"]))
                and integer(data["width"], 1) and integer(data["height"], 1)
                and data["width"] * data["height"] <= MAX_PIXELS, "Invalid frozen image identity or dimensions")
        stamp = parse_stamp(data["stamp"])
        header_bytes = data.get("header_bytes", 0)
        require(integer(header_bytes) and header_bytes <= stamp.size, "Invalid frozen image header size")
        return cls(data["image_id"], _absolute(data["path"]), relative, stamp,
                   data["sha256"], data["width"], data["height"], header_bytes,
                   parse_stamp(data["replace_stamp"]) if "replace_stamp" in data else None)


@dataclass(frozen=True)
class StageSkip:
    stage_id: str
    reason: str


@dataclass(frozen=True)
class JobSnapshot:
    job_id: str
    index_id: str
    recursive: bool
    roots: tuple[tuple[Path, tuple[int, int]], ...]
    destination: Path
    destination_identity: tuple[int, int]
    scratch: Path
    checkpoints: Path
    backend: str
    pipeline: PipelineDefinition
    settings: ExportSettings
    versions: dict[str, str]
    models: tuple[dict, ...]
    skipped: tuple[StageSkip, ...]
    inputs: tuple[FrozenInput, ...]
    mode: str = "sequential"
    pytorch_backend: str | None = None
    resize_approvals: tuple[str, ...] = ()

    @property
    def pipeline_sha256(self):
        return fingerprint(self.pipeline.to_json())

    def output_relative(self, item):
        return export_relative_path(item.relative_path, self.settings)

    def output_bytes(self, item):
        return output_reservation(self.settings, item.stamp.size, item.working_bytes)

    def stage_backend(self, registry, stage):
        descriptor = registry.descriptor(stage.operation, stage.operation_version)
        return descriptor.execution_backend(self.backend, self.pytorch_backend)

    def encode(self):
        # Stage tuples must use the portable pipeline schema when persisted.
        data = asdict(self)
        data["settings"] = encode_settings(self.settings)
        if self.pytorch_backend is None:
            del data["pytorch_backend"]
        if not self.resize_approvals:
            del data["resize_approvals"]
        data["pipeline"] = self.pipeline.to_json()
        for item in data["inputs"]:
            if item["replace_stamp"] is None:
                del item["replace_stamp"]
            if self.mode == "sequential":
                del item["header_bytes"]
        return {"schema_version": 1, **data}

    @classmethod
    def parse(cls, data, job_id, registry):
        fields(data, "schema_version mode job_id index_id recursive roots destination destination_identity scratch "
                     "checkpoints backend pipeline settings versions models skipped inputs"
                     + (" pytorch_backend" if "pytorch_backend" in data else "")
                     + (" resize_approvals" if "resize_approvals" in data else ""))
        pytorch_backend = data.get("pytorch_backend")
        require(pytorch_backend in (None, "cpu", "cuda"), "Invalid frozen PyTorch device")
        require(type(data["schema_version"]) is int and data["schema_version"] == 1
                and data["mode"] in ("sequential", "staged") and data["job_id"] == job_id
                and data["backend"] in RUNTIMES and type(data["recursive"]) is bool
                and isinstance(data["index_id"], str), "Invalid job snapshot version, mode or identity")
        roots = tuple((_absolute(path), _identity(identity)) for path, identity in data["roots"])
        inputs = tuple(FrozenInput.parse(item) for item in data["inputs"])
        require(data["mode"] == "sequential" or all(item.header_bytes > 0 for item in inputs),
                "Staged jobs require frozen image header sizes")
        require(inputs and roots and len({item.image_id for item in inputs}) == len(inputs)
                and len({item.relative_path for item in inputs}) == len(inputs), "Invalid frozen input list")
        settings = parse_settings(data["settings"])
        require(len({export_relative_path(item.relative_path, settings) for item in inputs}) == len(inputs),
                "Conflicting frozen export paths")
        for item in inputs:
            require(any(root / item.relative_path == item.path for root, _ in roots),
                    "A frozen input path does not belong to its recorded source roots")
        pipeline = parse_pipeline(data["pipeline"], registry, frozen=True)
        approvals = data.get("resize_approvals", [])
        require(isinstance(approvals, list) and all(isinstance(key, str) for key in approvals)
                and len(set(approvals)) == len(approvals) and set(approvals) <= {
                    s.id for s in pipeline.stages if s.enabled and registry.descriptor(
                        s.operation, s.operation_version).resize_limit}, "Invalid stage resize approvals")
        skipped = tuple(StageSkip(**skip) for skip in data["skipped"])
        require(tuple(skip.stage_id for skip in skipped) == tuple(stage.id for stage in pipeline.stages if not stage.enabled),
                "Skipped stages differ from the saved pipeline")
        return cls(job_id, data["index_id"], data["recursive"], roots,
                   _absolute(data["destination"]), _identity(data["destination_identity"]),
                   _absolute(data["scratch"]), _absolute(data["checkpoints"]), data["backend"], pipeline,
                   settings, data["versions"], tuple(data["models"]), skipped, inputs, data["mode"],
                   pytorch_backend, tuple(approvals))
