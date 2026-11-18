"""Immutable, versioned settings. No arrays, model runtimes, or GUI imports."""

from dataclasses import dataclass
import hashlib
import json
import math

from ..errors import RetouchingError


@dataclass(frozen=True)
class Parameter:
    name: str
    label: str
    default: float
    minimum: float
    maximum: float
    preview_on_change: bool = False

    def validate(self, value):
        if type(value) not in (int, float) or not self.minimum <= value <= self.maximum or not math.isfinite(value):
            raise RetouchingError("PARAMETER_INVALID", f"{self.name} must be a finite number from {self.minimum} to {self.maximum}")
        return float(value)


@dataclass(frozen=True)
class ContextSpec:
    key: str
    version: int = 1
    pixel_sensitive: bool = True


@dataclass(frozen=True)
class OperationDescriptor:
    operation: str
    version: int
    title: str
    parameters: tuple[Parameter, ...] = ()
    requires: tuple[ContextSpec, ...] = ()
    optional: tuple[ContextSpec, ...] = ()
    provides: tuple[ContextSpec, ...] = ()
    preserves: tuple[str, ...] = ()
    invalidates: tuple[str, ...] = ()
    changes_pixels: bool = False
    changes_geometry: bool = False
    model_refs: tuple[str, ...] = ()
    backends: tuple[str, ...] = ("cpu", "directml", "cuda")
    representation: str = "linear-srgb-float32"


@dataclass(frozen=True)
class StageDefinition:
    id: str
    operation: str
    operation_version: int
    enabled: bool
    parameters: tuple[tuple[str, float], ...] = ()
    model_ref: str | None = None

    def parameter(self, name: str) -> float:
        return dict(self.parameters)[name]

    @property
    def identity(self):
        encoded = json.dumps({"id": self.id, "operation": self.operation, "version": self.operation_version,
                              "parameters": dict(self.parameters), "model_ref": self.model_ref},
                             sort_keys=True, allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PipelineDefinition:
    name: str
    stages: tuple[StageDefinition, ...] = ()
    schema_version: int = 1

    def to_json(self):
        return {"schema_version": self.schema_version, "name": self.name, "stages": [
            {"id": s.id, "operation": s.operation, "operation_version": s.operation_version,
             "enabled": s.enabled, "parameters": dict(s.parameters),
             **({"model_ref": s.model_ref} if s.model_ref is not None else {})} for s in self.stages]}


def parse_pipeline(data, registry) -> PipelineDefinition:
    def fail(message):
        raise RetouchingError("PIPELINE_INVALID", message)

    if (not isinstance(data, dict) or set(data) != {"schema_version", "name", "stages"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1):
        fail("Expected a version-1 pipeline with schema_version, name, and stages")
    if not isinstance(data["name"], str) or not data["name"].strip() or len(data["name"]) > 200:
        fail("Pipeline name must contain 1–200 characters")
    if not isinstance(data["stages"], list) or len(data["stages"]) > 256:
        fail("Pipeline stages must be an ordered list of at most 256 entries")
    stages, ids = [], set()
    required = {"id", "operation", "operation_version", "enabled", "parameters"}
    for index, raw in enumerate(data["stages"]):
        if not isinstance(raw, dict) or not required <= set(raw) or set(raw) - required - {"model_ref"}:
            fail(f"Stage {index + 1}: expected id, operation, operation_version, enabled, parameters, and optional model_ref")
        if not isinstance(raw["id"], str) or not raw["id"].strip() or len(raw["id"]) > 100 or raw["id"] in ids:
            fail(f"Stage {index + 1}: id must be a unique nonempty string of at most 100 characters")
        if not isinstance(raw["operation"], str) or type(raw["operation_version"]) is not int:
            fail(f"Stage {raw['id']}: operation and integer operation_version are required")
        descriptor = registry.descriptor(raw["operation"], raw["operation_version"])
        if type(raw["enabled"]) is not bool or not isinstance(raw["parameters"], dict):
            fail(f"Stage {raw['id']}: enabled must be boolean and parameters an object")
        known = {p.name for p in descriptor.parameters}
        if set(raw["parameters"]) - known:
            fail(f"Stage {raw['id']}: unknown parameters {set(raw['parameters']) - known}")
        parameters = tuple((p.name, p.validate(raw["parameters"].get(p.name, p.default))) for p in descriptor.parameters)
        model = raw.get("model_ref")
        if (descriptor.model_refs and (not isinstance(model, str) or model not in descriptor.model_refs)
                or not descriptor.model_refs and "model_ref" in raw):
            fail(f"Stage {raw['id']}: model_ref must name one of {descriptor.model_refs}")
        stages.append(StageDefinition(raw["id"], descriptor.operation, descriptor.version, raw["enabled"], parameters, model))
        ids.add(raw["id"])
    return PipelineDefinition(data["name"], tuple(stages))
