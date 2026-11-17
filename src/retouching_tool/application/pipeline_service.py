"""Transactional pipeline editing and local readiness, without GUI dependencies."""

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from ..errors import RetouchingError
from ..json_io import atomic_json, read_json
from ..pipeline.assets import ModelCatalog
from ..pipeline.definition import PipelineDefinition, parse_pipeline
from ..pipeline.registry import reference_registry
from ..pipeline.validation import validate_pipeline


class PipelineService:
    def __init__(self, checkpoints, backend, diagnostics, *, autosave=None, registry=None, catalog=None):
        self.checkpoints, self.backend, self.diagnostics = checkpoints, backend, diagnostics
        self.autosave = autosave
        self.registry = registry if registry is not None else reference_registry()
        self.catalog = catalog if catalog is not None else ModelCatalog()
        self.status = validate_pipeline(PipelineDefinition("Empty foundation pipeline"), self.registry,
                                        self.catalog, checkpoints, backend)

    def _commit(self, definition, *, persist=True):
        checked = validate_pipeline(definition, self.registry, self.catalog, self.checkpoints, self.backend)
        if persist and self.autosave is not None:
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
                           component="pipeline", backend=self.backend)
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
        checked = validate_pipeline(previous, self.registry, self.catalog, checkpoints, self.backend)
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

    def _command(self, action):
        try:
            return action()
        except Exception as exc:
            self.diagnostics.emit("PIPELINE_COMMAND_FAILED", str(exc), component="pipeline", level="ERROR", exc_info=True)
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

    def recheck(self):
        return self._command(lambda: self._commit(self.status.definition))

    def preflight(self):
        status = self.recheck()
        if not status.executable:
            error = RetouchingError("PIPELINE_CONTEXT_BLOCKED", "Enabled operations have unresolved context; fix their analysis dependencies or turn them off")
            self.diagnostics.emit(error.code, str(error), component="pipeline", level="ERROR")
            raise error
        # Frozen tuple definitions can be held by jobs independently of later edits.
        return status.definition

    def _edit(self, stage_id, transform):
        def action():
            if not any(s.id == stage_id for s in self.status.definition.stages):
                raise RetouchingError("STAGE_UNKNOWN", f"No stage instance {stage_id}")
            data = self.status.definition.to_json()
            data["stages"] = [transform(s) if s["id"] == stage_id else s for s in data["stages"]]
            return self._commit(parse_pipeline(data, self.registry))
        return self._command(action)

    def set_enabled(self, stage_id, enabled):
        def change(stage):
            if type(enabled) is not bool:
                raise RetouchingError("STAGE_ENABLE_INVALID", "Enabled must be boolean")
            if enabled:
                descriptor = self.registry.descriptor(stage["operation"], stage["operation_version"])
                issues = self.catalog.check(stage.get("model_ref"), self.checkpoints, self.backend)
                if issues or self.backend not in descriptor.backends:
                    raise RetouchingError("STAGE_UNAVAILABLE", "; ".join(i.message for i in issues) or "Operation backend unsupported")
            return {**stage, "enabled": enabled}
        return self._edit(stage_id, change)

    def set_parameters(self, stage_id, parameters):
        return self._edit(stage_id, lambda stage: {**stage, "parameters": parameters})

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
