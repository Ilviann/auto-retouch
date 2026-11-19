"""Immutable, versioned settings. No arrays, model runtimes, or GUI imports."""

from dataclasses import dataclass
import hashlib
import json
import math

from ..errors import RetouchingError
from ..config import SCALING_METHODS


@dataclass(frozen=True)
class Parameter:
    name: str
    label: str
    default: float
    minimum: float
    maximum: float
    preview_on_change: bool = False
    active_choice: tuple[str, str] | None = None

    def validate(self, value):
        if type(value) not in (int, float) or not self.minimum <= value <= self.maximum or not math.isfinite(value):
            raise RetouchingError("PARAMETER_INVALID", f"{self.name} must be a finite number from {self.minimum} to {self.maximum}")
        return float(value)


@dataclass(frozen=True)
class IntegerParameter:
    name: str
    label: str
    default: int
    minimum: int
    maximum: int
    preview_on_change: bool = False

    def validate(self, value):
        if type(value) is not int or not self.minimum <= value <= self.maximum:
            raise RetouchingError("PARAMETER_INVALID",
                                  f"{self.label} must be a whole number from {self.minimum} to {self.maximum}.")
        return value


@dataclass(frozen=True)
class BooleanParameter:
    name: str
    label: str
    default: bool
    optional_context_key: str | None = None
    preview_on_change: bool = False

    def validate(self, value):
        if type(value) is not bool:
            raise RetouchingError("PARAMETER_INVALID", f"{self.label} must be true or false.")
        return value


@dataclass(frozen=True)
class ChoiceParameter:
    name: str
    label: str
    default: str
    choices: tuple[str, ...]
    preview_on_change: bool = False

    def validate(self, value):
        if value not in self.choices:
            raise RetouchingError("PARAMETER_INVALID", f"{self.label} must be one of {', '.join(self.choices)}.")
        return value


@dataclass(frozen=True)
class ScalingParameter:
    name: str
    label: str
    default: str = "inherit"
    preview_on_change: bool = False

    def validate(self, value):
        if value not in ("inherit", *SCALING_METHODS):
            raise RetouchingError("PARAMETER_INVALID", f"Unknown {self.name} method. Choose an app setting or a supported filter.")
        return value


SCALING_PARAMETERS = (ScalingParameter("downscale", "Downscaling"), ScalingParameter("upscale", "Upscaling"))
BLEND = Parameter("blend", "Blending", 1.0, 0.0, 1.0, preview_on_change=True)


@dataclass(frozen=True)
class ContextSpec:
    key: str
    version: int = 1
    pixel_sensitive: bool = True  # A producer must measure its final, blended pixels.


@dataclass(frozen=True, kw_only=True)
class MaskTargetParameter(BooleanParameter):
    """Select an upstream mask as the required target, overriding optional regions."""

    mask: ContextSpec


@dataclass(frozen=True)
class ResizeLimit:
    longest_side: int
    bypass_if_zero: str | None = None

    def applies(self, stage):
        return stage.enabled and (self.bypass_if_zero is None or stage.parameter(self.bypass_if_zero) != 0)


@dataclass(frozen=True)
class OperationDescriptor:
    operation: str
    version: int
    title: str
    parameters: tuple[Parameter | IntegerParameter | BooleanParameter | ChoiceParameter | ScalingParameter, ...] = ()
    requires: tuple[ContextSpec, ...] = ()
    optional: tuple[ContextSpec, ...] = ()
    provides: tuple[ContextSpec, ...] = ()
    changes_pixels: bool = False
    changes_geometry: bool = False
    model_refs: tuple[str, ...] = ()
    backends: tuple[str, ...] = ("cpu", "directml", "cuda")
    representation: str = "linear-srgb-float32"
    inference_runtime: str = "onnxruntime"
    resize_limit: ResizeLimit | None = None
    neutral_picker: bool = False

    @property
    def editable_parameters(self):
        return (*self.parameters, BLEND) if self.changes_pixels else self.parameters

    def active_optional_inputs(self, stage):
        if self.selected_mask(stage) is not None:
            return ()
        ignored = {p.optional_context_key for p in self.parameters
                   if isinstance(p, BooleanParameter) and p.optional_context_key and not stage.parameter(p.name)}
        return tuple(spec for spec in self.optional if spec.key not in ignored)

    def selected_mask(self, stage):
        return next((p.mask for p in self.parameters if isinstance(p, MaskTargetParameter)
                     and stage.parameter(p.name)), None)

    def active_required_inputs(self, stage):
        mask = self.selected_mask(stage)
        return (*self.requires, mask) if mask is not None else self.requires

    def execution_backend(self, onnx_backend, pytorch_backend):
        if self.inference_runtime == "onnxruntime":
            return onnx_backend
        if self.inference_runtime == "pytorch":
            return pytorch_backend
        raise ValueError(f"Unknown inference runtime {self.inference_runtime}")


# These settings extend existing v1 stages. Their defaults reproduce the
# original behavior, including the settings and contract hashes in saved jobs.
LEGACY_STAGE_DEFAULTS = {
    ("analysis.face_detection", 1): {"expand_pixels": 0},
    ("retouch.alibaba_skin", 1): {"use_face_rects": True},
    ("retouch.alibaba_skin_tone_smoothing", 1): {"use_exposed_skin": False},
}


@dataclass(frozen=True)
class StageDefinition:
    id: str
    operation: str
    operation_version: int
    enabled: bool
    parameters: tuple[tuple[str, float | int | bool | str], ...] = ()
    model_ref: str | None = None

    def parameter(self, name: str) -> float | int | bool | str:
        return dict(self.parameters)[name]

    def portable_parameters(self):
        # Implicit defaults keep older job and checkpoint identities stable.
        defaults = LEGACY_STAGE_DEFAULTS.get((self.operation, self.operation_version), {})
        return {key: value for key, value in self.parameters
                if not (key in ("downscale", "upscale") and value == "inherit"
                        or key in defaults and type(value) is type(defaults[key]) and value == defaults[key]
                        or key == BLEND.name and value == BLEND.default)}

    @property
    def identity(self):
        encoded = json.dumps({"id": self.id, "operation": self.operation, "version": self.operation_version,
                              "parameters": self.portable_parameters(), "model_ref": self.model_ref},
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
             "enabled": s.enabled, "parameters": s.portable_parameters(),
             **({"model_ref": s.model_ref} if s.model_ref is not None else {})} for s in self.stages]}


def parse_pipeline(data, registry, *, frozen=False) -> PipelineDefinition:
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
        # Presets and working copies omit retired operations. A saved job keeps its exact contract.
        if not frozen and registry.is_retired(raw["operation"], raw["operation_version"]):
            continue
        descriptor = registry.descriptor(raw["operation"], raw["operation_version"])
        if type(raw["enabled"]) is not bool or not isinstance(raw["parameters"], dict):
            fail(f"Stage {raw['id']}: enabled must be boolean and parameters an object")
        known = {p.name for p in descriptor.editable_parameters}
        if set(raw["parameters"]) - known:
            fail(f"Stage {raw['id']}: unknown parameters {set(raw['parameters']) - known}")
        parameters = tuple((p.name, p.validate(raw["parameters"].get(p.name, p.default))) for p in descriptor.editable_parameters)
        model = raw.get("model_ref")
        if (descriptor.model_refs and (not isinstance(model, str) or model not in descriptor.model_refs)
                or not descriptor.model_refs and "model_ref" in raw):
            fail(f"Stage {raw['id']}: model_ref must name one of {descriptor.model_refs}")
        stages.append(StageDefinition(raw["id"], descriptor.operation, descriptor.version, raw["enabled"], parameters, model))
        ids.add(raw["id"])
    return PipelineDefinition(data["name"], tuple(stages))
