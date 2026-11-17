"""Headless startup state and pipeline selection. Launch never starts processing."""

from dataclasses import asdict
from pathlib import Path

from ..cli import LaunchOptions
from ..config import AppConfig, FolderSettings, LoggingSettings, parse_config
from ..errors import RetouchingError
from ..json_io import atomic_json, read_json
from ..storage.batch_paths import probe_writable, resolve_folder
from .batch import BatchService
from .contracts import StartupSnapshot
from .job_service import JobService
from .pipeline_service import PipelineService
from .preview import PreviewService
from .worker_service import WorkerService

DEFAULT_PIPELINE = {"schema_version": 1, "name": "Empty foundation pipeline", "stages": []}


class ApplicationService:
    def __init__(self, root: Path, config: AppConfig, diagnostics):
        self.root, self.config, self.diagnostics = root, config, diagnostics
        self.batch_service = BatchService(config.temporary, diagnostics)

    def startup(self, options: LaunchOptions, backend: str) -> StartupSnapshot:
        notices = []
        state = self.root / ".state"
        editor = PipelineService(self.config.checkpoints, backend, self.diagnostics,
                                 autosave=state / "last-pipeline.json")
        self.pipeline_service = editor
        self.preview_service = PreviewService(self.batch_service, editor, self.diagnostics)
        self.job_service = JobService(self.root, self.batch_service, editor, self.diagnostics)
        self.job_worker = WorkerService(self.root, backend, self.diagnostics)
        if options.sources:
            sources = options.sources
        else:
            session = state / "session.json"
            sources = ()
            if session.exists():
                try:
                    data = read_json(session)
                    if (not isinstance(data, dict) or set(data) != {"schema_version", "sources"}
                            or type(data["schema_version"]) is not int or data["schema_version"] != 1
                            or not isinstance(data["sources"], list)
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
            pipeline = editor.load(options.pipeline, persist=False).definition.to_json()
            origin = str(options.pipeline)
        else:
            previous = state / "last-pipeline.json"
            if previous.exists():
                try:
                    pipeline = editor.load(previous, persist=False).definition.to_json()
                    origin = str(previous)
                except RetouchingError as exc:
                    self.diagnostics.emit("PIPELINE_RESTORE_FAILED", str(exc), level="WARNING", exc_info=True)
                    notices.append("Last-used pipeline is invalid or unsupported; the empty foundation default is shown.")
                    pipeline, origin = dict(DEFAULT_PIPELINE), "foundation default"
            else:
                pipeline, origin = dict(DEFAULT_PIPELINE), "foundation default"
        if self.config.using_defaults:
            notices.append("Default folders were used at launch. Open Settings to customize them.")
        if self.diagnostics.failure:
            notices.append(self.diagnostics.failure)
        # Persist only a successfully selected pipeline, never replace a corrupt autosave on fallback.
        try:
            if options.pipeline is not None or origin == str(state / "last-pipeline.json"):
                atomic_json(state / "last-pipeline.json", pipeline)
            if options.sources:
                self._save_sources(sources)
        except OSError as exc:
            raise RetouchingError("STATE_WRITE_FAILED", f"Cannot save last-used settings in {state}; check permissions/free space: {exc}") from exc
        self.diagnostics.emit("STARTUP_STATE_READY", "Startup settings loaded; no processing started", component="application",
                              source_count=len(sources), pipeline_origin=origin, backend=backend)
        return StartupSnapshot(sources, pipeline["name"], origin, backend, self.config.checkpoints,
                               self.config.temporary, self.config.logs, tuple(notices), stages=editor.status.stages)

    def save_settings(self, folders: FolderSettings, logging: LoggingSettings) -> AppConfig:
        path = self.config.file
        data = {"schema_version": 1, "paths": asdict(folders), "logging": asdict(logging)}
        try:
            config = parse_config(path, data)
            for name in ("checkpoints", "temporary", "logs"):
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
        self.batch_service.scratch = config.temporary
        self.diagnostics.emit("CONFIG_SAVED", "Updated application settings", component="configuration",
                              config_path=path, checkpoints=config.checkpoints, temporary=config.temporary,
                              logs=config.logs)
        return config

    def index_sources(self, sources, *, recursive=False):
        """Index the desktop selection and remember the successfully scanned paths."""
        sources = tuple(sources)
        index = self.batch_service.index(sources, recursive=recursive)
        try:
            self._save_sources(sources)
        except OSError as exc:
            error = RetouchingError("SOURCE_SELECTION_WRITE_FAILED",
                                    "Cannot save the source selection. Check access to local application state.")
            self.diagnostics.emit(error.code, str(error), component="application", level="ERROR", exc_info=True,
                                  index_id=index.index_id, sources=sources)
            raise error from exc
        return index

    def _save_sources(self, sources):
        atomic_json(self.root / ".state" / "session.json",
                    {"schema_version": 1, "sources": [str(Path(p).absolute()) for p in sources]})

    @staticmethod
    def _validate_sources(sources):
        for source in sources:
            if not source.exists() or (not source.is_dir() and not (source.is_file() and source.suffix.lower() in (".jpg", ".jpeg"))):
                raise RetouchingError("SOURCE_INVALID", f"Source must be an existing folder or JPEG file: {source}")
