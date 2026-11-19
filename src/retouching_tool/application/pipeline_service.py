"""Transactional pipeline editing and local readiness, without GUI dependencies."""

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from ..errors import RetouchingError
from ..config import ScalingSettings
from ..json_io import atomic_json, read_json
from ..operations.person_foreground import MODEL as PERSON_FOREGROUND_MODEL
from ..operations.alibaba_skin import BLEMISH_MODEL, MODEL as ALIBABA_MODEL, SMOOTHING_MODEL
from ..operations.face_detection import MODEL as YUNET_MODEL
from ..operations.exposed_skin import MODEL as EXPOSED_SKIN_MODEL
from ..pipeline.assets import ModelCatalog
from ..pipeline.definition import PipelineDefinition, parse_pipeline
from ..pipeline.registry import application_registry
from ..pipeline.validation import validate_pipeline
from .state import WorkspaceState


UNTITLED_PIPELINE = "Untitled pipeline"


def _name_untitled_pipeline(definition, path=None):
    if definition.name in ("Empty foundation pipeline", UNTITLED_PIPELINE):
        name = path.stem.strip()[:200] if path else ""
        return replace(definition, name=name or UNTITLED_PIPELINE)
    return definition


class PipelineService:
    def __init__(self, checkpoints, backend, diagnostics, *, autosave=None, registry=None, catalog=None,
                 scaling=ScalingSettings(), pytorch_backend=None, preset_folder=None):
        self.checkpoints, self.backend, self.diagnostics = checkpoints, backend, diagnostics
        self.autosave = autosave
        self.state = WorkspaceState(autosave) if autosave is not None else None
        self.preset_folder = preset_folder if preset_folder is not None else checkpoints.parent / "pipelines"
        self.preset_path = None
        self.preset_notice = ""
        self.scaling = scaling
        self.pytorch_backend = pytorch_backend
        self.registry = registry if registry is not None else application_registry()
        self.catalog = catalog if catalog is not None else ModelCatalog(
            (PERSON_FOREGROUND_MODEL, ALIBABA_MODEL, YUNET_MODEL, SMOOTHING_MODEL, BLEMISH_MODEL, EXPOSED_SKIN_MODEL))
        self.status = validate_pipeline(PipelineDefinition(UNTITLED_PIPELINE), self.registry,
                                        self.catalog, checkpoints, backend, pytorch_backend)
        self._saved_definition = self.status.definition

    @property
    def has_unsaved_changes(self):
        return self.status.definition != self._saved_definition

    def list_presets(self):
        folder = self.preset_folder
        try:
            folder.mkdir(parents=True, exist_ok=True)
            return sorted((path for path in folder.iterdir() if path.suffix.casefold() == ".json" and path.is_file()),
                          key=lambda path: path.name.casefold())
        except OSError as exc:
            message = f"Cannot list pipeline presets in {folder}. Check the folder in Settings: {exc}"
            self.diagnostics.emit("PRESET_FOLDER_UNAVAILABLE", message, component="pipeline", level="ERROR",
                                  exc_info=True, preset_folder=folder)
            raise RetouchingError("PRESET_FOLDER_UNAVAILABLE", message) from exc

    def stage_backend(self, stage):
        return self.registry.descriptor(stage.operation, stage.operation_version).execution_backend(
            self.backend, self.pytorch_backend)

    def _commit(self, definition, *, persist=True, write_unchanged=True, **state_fields):
        checked = validate_pipeline(definition, self.registry, self.catalog, self.checkpoints, self.backend, self.pytorch_backend)
        if persist and self.autosave is not None and (write_unchanged or checked.definition != self.status.definition):
            self._persist(checked.definition, **state_fields)
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
            self._persist(checked.definition)
        try:
            yield
        except Exception:
            if save_disabled:
                self._persist(previous)
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
        def action():
            definition = _name_untitled_pipeline(parse_pipeline(read_json(path), self.registry), path)
            checked = self._commit(definition, persist=persist, preset=str(path.absolute()))
            self.preset_path, self._saved_definition = path, definition
            self.preset_notice = ""
            return checked
        return self._command(action, preset_path=path)

    def _persist(self, definition, **fields):
        try:
            self.state.update(pipeline=definition.to_json(), **fields)
        except OSError as exc:
            raise RetouchingError("PIPELINE_WRITE_FAILED", f"Cannot save working pipeline to {self.autosave}: {exc}") from exc

    def _remember_preset(self):
        self.preset_notice = ""
        if self.autosave is None:
            return
        try:
            self._persist(self.status.definition, preset=str(self.preset_path.absolute()))
        except RetouchingError as exc:
            self.preset_notice = f"Cannot remember the selected preset for the next launch: {exc}"
            self.diagnostics.emit("PRESET_SELECTION_WRITE_FAILED", self.preset_notice, level="WARNING",
                                  component="pipeline", exc_info=True, preset_path=self.preset_path)

    def restore(self, data=None):
        """Restore working edits independently of the portable preset they came from."""
        data = self.state.read() if data is None else data
        status = self.load_data(data["pipeline"], persist=False)
        try:
            if "preset" in data:
                saved = data["preset"]
                if not isinstance(saved, str) or not Path(saved).is_absolute():
                    raise ValueError(f"Expected an absolute preset path in {self.autosave}")
                path = Path(saved)
                self._saved_definition = _name_untitled_pipeline(parse_pipeline(read_json(path), self.registry), path)
                self.preset_path = path
                self.status = replace(status, definition=_name_untitled_pipeline(status.definition, path))
        except (OSError, ValueError, RetouchingError) as exc:
            self._saved_definition = None
            self.preset_notice = f"Restored the working pipeline. Its saved preset could not be read: {exc}"
            self.diagnostics.emit("PRESET_SELECTION_RESTORE_FAILED", self.preset_notice, level="WARNING",
                                  component="pipeline", exc_info=True, selection_path=self.autosave)
        return self.status

    def load_data(self, data, *, persist=True):
        return self._command(lambda: self._commit(_name_untitled_pipeline(parse_pipeline(data, self.registry)),
                                                  persist=persist))

    def save(self, path: Path):
        definition = _name_untitled_pipeline(self.status.definition, path)
        def write_preset():
            if self.autosave is not None and path.resolve() == self.autosave.resolve():
                raise RetouchingError("PIPELINE_WRITE_FAILED", "Choose a preset filename separate from the desktop state file.")
            self._write(path, definition)
        self._command(write_preset, preset_path=path)
        renamed = definition != self.status.definition
        if renamed:
            self.status = replace(self.status, definition=definition)
        self.preset_path, self._saved_definition = path, definition
        self._remember_preset()

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
