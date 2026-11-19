"""Headless startup state and pipeline selection. Launch never starts processing."""

from collections import Counter
from dataclasses import asdict
from pathlib import Path

from ..cli import LaunchOptions
from ..config import AppConfig, FolderSettings, LoggingSettings, ScalingSettings, parse_config
from ..diagnostics.history import DiagnosticHistory
from ..diagnostics.reports import DiagnosticReport, write_report
from ..errors import RetouchingError
from ..imaging.export_settings import encode_settings
from ..imaging.source import SOURCE_EXTENSIONS
from ..jobs.journal import validate_job_id
from ..jobs.snapshot import model_manifests, runtime_versions
from ..json_io import atomic_json
from ..storage.batch_paths import probe_writable, resolve_folder
from .batch import BatchService
from .contracts import StartupSnapshot
from .job_service import JobService
from .pipeline_service import PipelineService, UNTITLED_PIPELINE
from .preview import PreviewService
from .state import WorkspaceState
from .worker_service import WorkerService

DEFAULT_PIPELINE = {"schema_version": 1, "name": UNTITLED_PIPELINE, "stages": []}


class ApplicationService:
    def __init__(self, root: Path, config: AppConfig, diagnostics):
        self.root, self.config, self.diagnostics = root, config, diagnostics
        self.batch_service = BatchService(config.temporary, diagnostics)
        self.diagnostic_history = DiagnosticHistory(diagnostics, root / "logs")
        self._source_index = None
        self.state = WorkspaceState(root / "state.json")
        self.window_state_warning = ""
        if config.file == self.state.path:
            raise RetouchingError("CONFIG_INVALID", "state.json is reserved for desktop state. Choose a different config file.")

    def startup(self, options: LaunchOptions, backend: str, *, pytorch_backend=None) -> StartupSnapshot:
        notices = []
        try:
            self.state.migrate(self.diagnostics)
            data = self.state.read()
        except (OSError, RetouchingError) as exc:
            self.diagnostics.emit("STATE_RESTORE_FAILED", "Could not restore desktop state.", component="application",
                                  level="WARNING", exc_info=True, state_path=self.state.path)
            notices.append(f"Could not restore desktop state: {exc}")
            data = {}
        editor = PipelineService(self.config.checkpoints, backend, self.diagnostics,
                                 preset_folder=self.config.pipelines,
                                 autosave=self.state.path, scaling=self.config.scaling, pytorch_backend=pytorch_backend)
        self.pipeline_service = editor
        self.preview_service = PreviewService(self.root, editor, self.diagnostics)
        self.job_service = JobService(self.root, self.batch_service, editor, self.diagnostics)
        self.job_worker = WorkerService(self.root, backend, self.diagnostics, self.job_service.folder_for,
                                        pytorch_backend=pytorch_backend)
        if options.sources:
            sources = options.sources
        else:
            sources = ()
            if "sources" in data:
                try:
                    if (not isinstance(data["sources"], list)
                            or any(not isinstance(p, str) or not Path(p).is_absolute() for p in data["sources"])):
                        raise RetouchingError("SESSION_INVALID", "Invalid restored source selection")
                    sources = tuple(dict.fromkeys(Path(p).resolve() for p in data["sources"]))
                    self._validate_sources(sources)
                except RetouchingError as exc:
                    self.diagnostics.emit("SESSION_RESTORE_FAILED", str(exc), level="WARNING", exc_info=True)
                    notices.append("Previous sources could not be restored; select sources again.")
                    sources = ()
        self._validate_sources(sources)
        if options.pipeline is not None:
            pipeline = editor.load(options.pipeline).definition.to_json()
            origin = str(options.pipeline)
        else:
            if "pipeline" in data:
                try:
                    pipeline = editor.restore(data).definition.to_json()
                    origin = str(self.state.path)
                except RetouchingError as exc:
                    self.diagnostics.emit("PIPELINE_RESTORE_FAILED", str(exc), level="WARNING", exc_info=True)
                    notices.append("Last-used pipeline is invalid or unsupported. Open a preset or add operations to start again.")
                    pipeline, origin = dict(DEFAULT_PIPELINE), "new pipeline"
            else:
                pipeline, origin = dict(DEFAULT_PIPELINE), "new pipeline"
        if self.config.using_defaults:
            notices.append("Default folders were used at launch. Open Settings to customize them.")
        if self.diagnostics.failure:
            notices.append(self.diagnostics.failure)
        # Persist only a successfully selected pipeline, never replace a corrupt autosave on fallback.
        try:
            if options.pipeline is None and origin == str(self.state.path) and pipeline != data["pipeline"]:
                self.state.update(pipeline=pipeline)
            if options.sources:
                self._save_sources(sources)
        except OSError as exc:
            raise RetouchingError("STATE_WRITE_FAILED", f"Cannot save last-used settings in {self.state.path}: {exc}") from exc
        if editor.preset_notice:
            notices.append(editor.preset_notice)
        self.diagnostics.emit("STARTUP_STATE_READY", "Startup settings loaded; no processing started", component="application",
                              source_count=len(sources), pipeline_origin=origin, backend=backend, pytorch_backend=pytorch_backend)
        return StartupSnapshot(sources, pipeline["name"], origin, backend, self.config.checkpoints,
                               self.config.temporary, self.config.logs, tuple(notices), stages=editor.status.stages,
                               pytorch_backend=pytorch_backend)

    def save_settings(self, folders: FolderSettings, logging: LoggingSettings,
                      scaling: ScalingSettings | None = None) -> AppConfig:
        path = self.config.file
        data = {"schema_version": 1, "paths": asdict(folders), "logging": asdict(logging),
                "scaling": asdict(scaling or self.config.scaling)}
        try:
            config = parse_config(path, data)
            for name in ("checkpoints", "temporary", "logs", "pipelines"):
                folder = getattr(config, name)
                try:
                    _, parent = resolve_folder(folder, allow_missing=True)
                    if name == "temporary":
                        probe_writable(parent, needs_subdirectory=True)
                except (OSError, ValueError) as exc:
                    raise RetouchingError("CONFIG_FOLDER_INVALID", f"Choose an accessible {name} folder. "
                                          f"Cannot use {folder}: {exc}") from exc
            with self.pipeline_service.changing_checkpoints(config.checkpoints):
                with self.diagnostics.changing_settings(config.logs, config.logging):
                    atomic_json(path, data)
        except (OSError, RetouchingError) as exc:
            error = exc if isinstance(exc, RetouchingError) else RetouchingError(
                "CONFIG_WRITE_FAILED", f"Cannot save settings. Check permissions and free space for {path} "
                f"and the log folder: {exc}")
            self.diagnostics.emit(error.code, str(error), component="configuration", level="ERROR", exc_info=True,
                                  config_path=path)
            if error is exc:
                raise
            raise error from exc
        self.config = config
        self.pipeline_service.scaling = config.scaling
        self.pipeline_service.preset_folder = config.pipelines
        self.batch_service.scratch = config.temporary
        self.diagnostics.emit("CONFIG_SAVED", "Updated application settings", component="configuration",
                              config_path=path, checkpoints=config.checkpoints, temporary=config.temporary,
                              logs=config.logs, pipelines=config.pipelines)
        return config

    def index_sources(self, sources, *, recursive=False):
        """Index the desktop selection and remember the successfully scanned paths."""
        sources = tuple(sources)
        index = self.batch_service.index(sources, recursive=recursive)
        try:
            self._save_sources(sources)
        except (OSError, RetouchingError) as exc:
            error = RetouchingError("SOURCE_SELECTION_WRITE_FAILED",
                                    "Cannot save the source selection. Check access to local application state.")
            self.diagnostics.emit(error.code, str(error), component="application", level="ERROR", exc_info=True,
                                  index_id=index.index_id, sources=sources)
            raise error from exc
        self._source_index = index
        return index

    def export_diagnostics(self, destination: Path, *, job_id="", cancelled=lambda: False) -> DiagnosticReport | None:
        """Export this application's session or a saved job's available local evidence."""
        try:
            config, editor, index = self.config, self.pipeline_service, self._source_index
            photo_names = {item.path.name for item in index.images} if index is not None else set()
            notes = []
            settings = {"schema_version": 1, "scope": "job" if job_id else "session",
                        "session_id": self.diagnostics.session_id}
            if job_id:
                validate_job_id(job_id)
                settings["job_id"] = job_id
                try:
                    job, status = self.job_service.read_snapshot(job_id)
                except (RetouchingError, OSError, ValueError):
                    message = "The saved job journal is unavailable or unreadable. Frozen settings and results are missing."
                    notes.append(message)
                    self.diagnostics.emit("DIAGNOSTIC_REPORT_JOB_UNAVAILABLE", message, level="WARNING",
                                          component="diagnostics", exc_info=True, job_id=job_id)
                else:
                    photo_names.update(image.source.name for image in status.images)
                    photo_names.update(image.output.name for image in status.images)
                    settings.update(mode=job.mode, recursive=job.recursive, backend=job.backend, pytorch_backend=job.pytorch_backend, versions=job.versions,
                                    pipeline=_report_pipeline(job.pipeline), pipeline_sha256=job.pipeline_sha256,
                                    models=job.models, export=encode_settings(job.settings), status=status.status,
                                    skipped_stages=[asdict(stage) for stage in status.skipped_stages],
                                    destination_path=str(job.destination), temporary=str(job.scratch),
                                    checkpoints=str(job.checkpoints),
                                    counts=dict(Counter(image.status for image in status.images)),
                                    images=[{"image_id": image.image_id, "source_path": str(image.source),
                                             "output_path": str(image.output), "status": image.status,
                                             "attempts": image.attempts, "warnings": image.warnings,
                                             "code": image.code, "message": image.message}
                                            for image in status.images])
            else:
                pipeline = editor.status.definition
                settings.update(backend=editor.backend, pytorch_backend=editor.pytorch_backend,
                                versions=runtime_versions(editor.backend, editor.pytorch_backend),
                                pipeline=_report_pipeline(pipeline), models=model_manifests(pipeline, editor.catalog),
                                config_path=str(config.file), checkpoints=str(config.checkpoints),
                                temporary=str(config.temporary), logs=str(config.logs), logging=asdict(config.logging),
                                scaling=asdict(config.scaling))
                notes.append("Settings describe the current workspace at export time. Saved jobs have separate frozen settings.")
            return write_report(self.diagnostic_history, destination, settings, job_id=job_id,
                                photo_names=photo_names, limitations=notes, cancelled=cancelled)
        except Exception as exc:
            error = exc if isinstance(exc, RetouchingError) else RetouchingError(
                "DIAGNOSTIC_REPORT_FAILED", "Could not save the diagnostic report. Check folder access and free space.")
            self.diagnostics.emit(error.code, str(error), level="ERROR", component="diagnostics", exc_info=True,
                                  job_id=job_id or None, report_path=destination)
            if error is exc:
                raise
            raise error from exc

    def _save_sources(self, sources):
        self.state.update(sources=[str(Path(p).absolute()) for p in sources])

    @staticmethod
    def _validate_sources(sources):
        for source in sources:
            if not source.exists() or (not source.is_dir() and not (source.is_file() and source.suffix.lower() in SOURCE_EXTENSIONS)):
                raise RetouchingError("SOURCE_INVALID", f"Source must be an existing folder or a photo file (JPEG, AVIF, PNG, TIFF, BMP, JPEG XL): {source}")


def _report_pipeline(definition):
    # Display names can contain client names. Keep the ordered settings and stable stage IDs.
    data = definition.to_json()
    del data["name"]
    return data
