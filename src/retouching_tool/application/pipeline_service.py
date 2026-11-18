"""Transactional pipeline editing and local readiness, without GUI dependencies."""

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from ..errors import RetouchingError
from ..config import ScalingSettings
from ..json_io import atomic_json, read_json
from ..operations.person_foreground import MODEL as PERSON_FOREGROUND_MODEL
from ..operations.alibaba_skin import MODEL as ALIBABA_MODEL
from ..pipeline.assets import ModelCatalog
from ..pipeline.definition import PipelineDefinition, parse_pipeline
from ..pipeline.registry import application_registry
from ..pipeline.validation import validate_pipeline


class PipelineService:
    def __init__(self, checkpoints, backend, diagnostics, *, autosave=None, registry=None, catalog=None,
                 scaling=ScalingSettings(), pytorch_backend=None):
        self.checkpoints, self.backend, self.diagnostics = checkpoints, backend, diagnostics
        self.autosave = autosave
        self.scaling = scaling
        self.pytorch_backend = pytorch_backend
        self.registry = registry if registry is not None else application_registry()
        self.catalog = catalog if catalog is not None else ModelCatalog((PERSON_FOREGROUND_MODEL, ALIBABA_MODEL))
        self.status = validate_pipeline(PipelineDefinition("Empty foundation pipeline"), self.registry,
                                        self.catalog, checkpoints, backend, pytorch_backend)

    def stage_backend(self, stage):
        return self.registry.descriptor(stage.operation, stage.operation_version).execution_backend(
            self.backend, self.pytorch_backend)

    def _commit(self, definition, *, persist=True, write_unchanged=True):
        checked = validate_pipeline(definition, self.registry, self.catalog, self.checkpoints, self.backend, self.pytorch_backend)
        if persist and self.autosave is not None and (write_unchanged or checked.definition != self.status.definition):
            self._write(self.autosave, checked.definition)
        self._apply_status(checked)
        return checked

    def _apply_status(self, checked):
        previous = {s.stage.id: s for s in self.status.stages}
        self.status = checked
        for stage in checked.stages:
            old = previous.get(stage.stage.id)
            context = dict(stage_instance_id=stage.stage.id, operation=stage.stage.operation,
                           operation_version=stage.stage.operation_version, model_ref=stage.stage.model_ref,
                           component="pipeline", backend=self.stage_backend(stage.stage))
            if stage.issues and (old is None or old.issues != stage.issues):
                self.diagnostics.emit("STAGE_ASSET_UNAVAILABLE", "Stage is off; supply/validate required model files, then recheck and enable explicitly",
                    level="ERROR", issues=[{"code": i.code, "message": i.message, "path": i.path, "technical_cause": i.technical_cause} for i in stage.issues], **context)
            elif old is not None and old.issues and not stage.issues:
                self.diagnostics.emit("STAGE_ASSET_RECOVERED", "Stage assets validated; stage remains off until explicitly enabled", **context)
            if stage.dependency_errors and (old is None or old.dependency_errors != stage.dependency_errors):
                self.diagnostics.emit("STAGE_CONTEXT_BLOCKED", "; ".join(stage.dependency_errors), level="ERROR", **context)

    @contextmanager
    def changing_checkpoints(self, checkpoints):
        """Persist forced disables, then publish readiness when the folder change commits."""
        if checkpoints == self.checkpoints:
            yield
            return
        previous = self.status.definition
        checked = validate_pipeline(previous, self.registry, self.catalog, checkpoints, self.backend, self.pytorch_backend)
        save_disabled = self.autosave is not None and checked.definition != previous
        if save_disabled:
            self._write(self.autosave, checked.definition)
        try:
            yield
        except Exception:
            if save_disabled:
                self._write(self.autosave, previous)
            raise
        self.checkpoints = checkpoints
        self._apply_status(checked)

    def _command(self, action, **context):
        try:
            return action()
        except Exception as exc:
            self.diagnostics.emit("PIPELINE_COMMAND_FAILED", str(exc), component="pipeline", level="ERROR",
                                  exc_info=True, **context)
            raise

    def load(self, path: Path, *, persist=True):
        return self._command(lambda: self._commit(parse_pipeline(read_json(path), self.registry), persist=persist))

    def load_data(self, data, *, persist=True):
        return self._command(lambda: self._commit(parse_pipeline(data, self.registry), persist=persist))

    def save(self, path: Path):
        return self._command(lambda: self._write(path, self.status.definition))

    @staticmethod
    def _write(path, definition):
        try:
            atomic_json(path, definition.to_json())
        except OSError as exc:
            raise RetouchingError("PIPELINE_WRITE_FAILED", f"Cannot save pipeline to {path}; check permissions/free space: {exc}") from exc

    def recheck(self, *, write_unchanged=True):
        return self._command(lambda: self._commit(self.status.definition, write_unchanged=write_unchanged))

    def preflight(self, *, write_unchanged=True):
        status = self.recheck(write_unchanged=write_unchanged)
        if not status.executable:
            error = RetouchingError("PIPELINE_CONTEXT_BLOCKED", "Enabled operations have unresolved context; fix their analysis dependencies or turn them off")
            self.diagnostics.emit(error.code, str(error), component="pipeline", level="ERROR")
            raise error
        # Frozen tuple definitions can be held by jobs independently of later edits.
        return status.definition

    def resolve_scaling(self, definition):
        defaults = self.scaling
        stages = tuple(replace(stage, parameters=tuple(
            (name, getattr(defaults, name) if value == "inherit" else value)
            for name, value in stage.parameters)) for stage in definition.stages)
        return replace(definition, stages=stages)

    def _edit(self, stage_id, transform, *, image_id=None):
        def action():
            if not any(s.id == stage_id for s in self.status.definition.stages):
                raise RetouchingError("STAGE_UNKNOWN", f"No stage instance {stage_id}")
            data = self.status.definition.to_json()
            data["stages"] = [transform(s) if s["id"] == stage_id else s for s in data["stages"]]
            return self._commit(parse_pipeline(data, self.registry))
        return self._command(action, stage_instance_id=stage_id, image_id=image_id)

    def set_enabled(self, stage_id, enabled):
        def change(stage):
            if type(enabled) is not bool:
                raise RetouchingError("STAGE_ENABLE_INVALID", "Enabled must be boolean")
            if enabled:
                descriptor = self.registry.descriptor(stage["operation"], stage["operation_version"])
                device = descriptor.execution_backend(self.backend, self.pytorch_backend)
                issues = self.catalog.check(stage.get("model_ref"), self.checkpoints, device)
                if issues or device not in descriptor.backends:
                    raise RetouchingError("STAGE_UNAVAILABLE", "; ".join(i.message for i in issues) or "Operation backend unsupported")
            return {**stage, "enabled": enabled}
        return self._edit(stage_id, change)

    def set_parameters(self, stage_id, parameters, *, image_id=None):
        return self._edit(stage_id, lambda stage: {**stage, "parameters": parameters}, image_id=image_id)

    def set_parameter(self, stage_id, name, value):
        return self._edit(stage_id, lambda stage: {**stage, "parameters": {**stage["parameters"], name: value}})

    def add_operation(self, operation, version, *, model_ref=None):
        return self.add({
            "id": uuid4().hex,
            "operation": operation,
            "operation_version": version,
            "enabled": True,
            "parameters": {},
            **({"model_ref": model_ref} if model_ref is not None else {}),
        })

    def add(self, stage_data):
        def action():
            data = self.status.definition.to_json()
            data["stages"].append(stage_data)
            return self._commit(parse_pipeline(data, self.registry))
        return self._command(action)

    def remove(self, stage_id):
        def action():
            if stage_id not in {s.id for s in self.status.definition.stages}:
                raise RetouchingError("STAGE_UNKNOWN", f"No stage instance {stage_id}")
            return self._commit(replace(self.status.definition, stages=tuple(s for s in self.status.definition.stages if s.id != stage_id)))
        return self._command(action)

    def reorder(self, stage_ids):
        def action():
            stages = {s.id: s for s in self.status.definition.stages}
            if len(stage_ids) != len(stages) or set(stage_ids) != set(stages):
                raise RetouchingError("STAGE_ORDER_INVALID", "Reorder must contain every stage id exactly once")
            return self._commit(replace(self.status.definition, stages=tuple(stages[key] for key in stage_ids)))
        return self._command(action)
