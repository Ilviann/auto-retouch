"""Readiness and dependency checks shared by editor and execution preflight."""

from dataclasses import dataclass, replace

from .assets import AssetIssue
from .definition import PipelineDefinition, StageDefinition


@dataclass(frozen=True)
class StageStatus:
    stage: StageDefinition
    title: str
    state: str
    issues: tuple[AssetIssue, ...] = ()
    dependency_errors: tuple[str, ...] = ()
    model_page: str | None = None


@dataclass(frozen=True)
class PipelineStatus:
    definition: PipelineDefinition
    stages: tuple[StageStatus, ...]

    @property
    def executable(self):
        return not any(s.stage.enabled and (s.issues or s.dependency_errors) for s in self.stages)


def retained(spec, descriptor):
    if spec.key in descriptor.invalidates or descriptor.changes_geometry:
        return False
    if descriptor.changes_pixels:
        return not spec.pixel_sensitive and spec.key in descriptor.preserves
    return True


def validate_pipeline(definition, registry, catalog, checkpoints, backend, pytorch_backend=None):
    available, statuses, stages = {}, [], []
    for original in definition.stages:
        desc = registry.descriptor(original.operation, original.operation_version)
        device = desc.execution_backend(backend, pytorch_backend)
        issues = catalog.check(original.model_ref, checkpoints, device)
        if device not in desc.backends:
            issues += (AssetIssue("OPERATION_BACKEND_UNSUPPORTED", f"{desc.title} does not support the prepared {desc.inference_runtime} device {device}"),)
        stage = replace(original, enabled=False) if issues else original
        errors = []
        if stage.enabled:
            for required in desc.requires:
                if available.get(required.key) != required:
                    errors.append(f"{required.key} version {required.version} requires a compatible enabled producer before {stage.id}; add/enable/reorder its analysis stage")
            if not errors:
                available = {key: value for key, value in available.items() if retained(value, desc)}
                available.update((spec.key, spec) for spec in desc.provides)
        state = "unavailable" if issues else "blocked" if errors else "ready" if stage.enabled else "skipped"
        statuses.append(StageStatus(stage, desc.title, state, issues, tuple(errors), catalog.publisher_url(stage.model_ref)))
        stages.append(stage)
    return PipelineStatus(replace(definition, stages=tuple(stages)), tuple(statuses))
