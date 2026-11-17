"""Headless startup state and pipeline selection. Launch never starts processing."""

from pathlib import Path

from ..cli import LaunchOptions
from ..config import AppConfig
from ..errors import RetouchingError
from ..json_io import atomic_json, read_json
from .batch import BatchService
from .contracts import StartupSnapshot
from .job_service import JobService
from .pipeline_service import PipelineService
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
            notices.append(f"Using default folders; copy config.example.json to {self.config.file} to customize them.")
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
