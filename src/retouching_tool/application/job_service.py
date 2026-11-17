"""Create, run and resume local jobs through headless commands."""

from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from uuid import uuid4

from ..errors import RetouchingError
from ..imaging.jpeg import JpegRuntime, JpegSettings, decode_jpeg, export_jpeg
from ..jobs.execution import ImageDiagnostics, disk_full
from ..jobs.journal import MAX_ATTEMPTS, STAGED_SCHEMA_VERSION, TERMINAL, JournalError, open_journal, runner_lock
from ..jobs.publication import PreparedExport, check_destination, prepare_parent, recover_export
from ..jobs.snapshot import FrozenInput, JobSnapshot, StageSkip, model_manifests, runtime_versions
from ..pipeline.executor import StageRuntime, StageSession
from ..pipeline.validation import validate_pipeline
from ..storage.artifacts import fingerprint
from ..storage.batch_paths import current_file_stamp, file_identity, output_path, resolve_folder, stamp_and_hash
from .batch import BASE_SCRATCH_BYTES, BatchService, SourceIndex, SourceRoot, volume_budgets
from .staged_jobs import StagedRunner, validate_staged_progress


@dataclass(frozen=True)
class JobImageStatus:
    image_id: str
    source: Path
    output: Path
    status: str
    attempts: int
    warnings: int
    code: str | None
    message: str | None
    completed_stages: int | None = None


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    status: str
    images: tuple[JobImageStatus, ...]
    skipped_stages: tuple[StageSkip, ...]
    mode: str = "sequential"
    stage_ids: tuple[str, ...] = ()


class JobControl:
    """Thread-safe requests observed between the scheduler's work units."""

    def __init__(self):
        self.pause = Event()
        self.cancel = Event()


class JobService:
    def __init__(self, root, batch, pipeline, diagnostics):
        self.root = root
        self.batch = batch
        self.pipeline = pipeline
        self.diagnostics = diagnostics

    def create(self, index, destination, *, mode="sequential", settings=JpegSettings(), selected_ids=None) -> str:
        """Validate and persist a frozen job. No photos are decoded or exported here."""
        job_id = uuid4().hex
        context = {"index_id": index.index_id, "destination_path": destination}
        try:
            if not isinstance(settings, JpegSettings):
                raise RetouchingError("JOB_SETTINGS_INVALID", "Provide validated JPEG compression settings.")
            definition = self.pipeline.preflight()
            skipped = tuple(StageSkip(item.stage.id, ", ".join(issue.code for issue in item.issues) or "disabled")
                            for item in self.pipeline.status.stages if not item.stage.enabled)
            plan = self.batch.preflight(index, destination, mode=mode, selected_ids=selected_ids, job_id=job_id)
            if not plan.ready:
                raise RetouchingError("JOB_SPACE_INSUFFICIENT", "Free the disk space reported by preflight before creating this job.")
            inputs = []
            for planned in plan.images:
                image = planned.source
                context.update(image_id=image.image_id, source_path=image.path)
                try:
                    stamp, checksum = stamp_and_hash(image.path, image.stamp)
                except OSError as exc:
                    raise RetouchingError("SOURCE_CHANGED", f"Cannot freeze {image.path}. Check the file and re-index the batch.") from exc
                inputs.append(FrozenInput(image.image_id, image.path, image.relative_path, stamp, checksum,
                                          image.header.width, image.header.height, image.header.header_bytes))
            context.pop("image_id")
            context.pop("source_path")
            snapshot = JobSnapshot(job_id, index.index_id, index.recursive,
                                   tuple((root.path, root.identity) for root in index.roots), plan.destination,
                                   file_identity(plan.destination.stat()), plan.scratch, self.pipeline.checkpoints.resolve(),
                                   self.pipeline.backend, definition, settings, runtime_versions(self.pipeline.backend),
                                   model_manifests(definition, self.pipeline.catalog), skipped, tuple(inputs), plan.mode)
            with open_journal(self.root, job_id, create=True) as journal:
                journal.initialize(snapshot.encode(), (item.image_id for item in inputs))
        except Exception as exc:
            self._emit(job_id, getattr(exc, "code", "JOB_CREATE_FAILED"), str(exc), level="ERROR", exc_info=True, **context)
            raise

        for stage in snapshot.skipped:
            self._emit(job_id, "JOB_STAGE_SKIPPED", "Frozen pipeline skips this stage", stage_instance_id=stage.stage_id,
                       skip_reason=stage.reason)
        self._emit(job_id, "JOB_CREATED", "Saved the job snapshot", mode=snapshot.mode, image_count=len(snapshot.inputs),
                   pipeline_sha256=snapshot.pipeline_sha256, backend=snapshot.backend, versions=snapshot.versions, **context)
        return job_id

    def status(self, job_id) -> JobStatus:
        try:
            with open_journal(self.root, job_id) as journal:
                return self._status(journal, self._load(journal, job_id))
        except Exception as exc:
            self._emit(job_id, getattr(exc, "code", "JOB_STATUS_FAILED"), str(exc), level="ERROR", exc_info=True)
            raise

    def run(self, job_id, *, control: JobControl | None = None) -> JobStatus:
        """Run synchronously. A new call resumes an interrupted or paused job."""
        try:
            with runner_lock(self.root), open_journal(self.root, job_id) as journal:
                job = self._load(journal, job_id)
                if job.mode == "staged":
                    StagedRunner(self, journal, job, control or JobControl()).run()
                elif journal.status() not in TERMINAL:
                    self._run(journal, job, control or JobControl())
                return self._status(journal, job)
        except Exception as exc:
            self._emit(job_id, getattr(exc, "code", "JOB_RUN_FAILED"), str(exc), level="ERROR", exc_info=True)
            raise

    def _load(self, journal, job_id):
        try:
            job = JobSnapshot.parse(journal.snapshot(), job_id, self.pipeline.registry)
            rows = journal.images()
            if tuple(row["image_id"] for row in rows) != tuple(item.image_id for item in job.inputs):
                raise ValueError("Journal image list differs from the frozen inputs")
            if (journal.version == STAGED_SCHEMA_VERSION) != (job.mode == "staged"):
                raise ValueError("Journal schema differs from the saved execution mode")
            if job.mode == "staged":
                validate_staged_progress(job, rows)
            return job
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise JournalError("Cannot validate the saved job. Retain its journal for inspection.") from exc

    @staticmethod
    def _status(journal, job):
        status, rows = journal.accounting()
        images = tuple(JobImageStatus(item.image_id, item.path, job.destination / item.relative_path,
                                      row["status"], row["attempts"], row["warnings"], row["code"], row["message"],
                                      row["next_stage"] if job.mode == "staged" else None)
                       for item, row in zip(job.inputs, rows, strict=True))
        return JobStatus(job.job_id, status, images, job.skipped, job.mode,
                         tuple(stage.id for stage in job.pipeline.stages if stage.enabled))

    def _validate_run(self, job):
        check_destination(job)
        index = SourceIndex(job.index_id, job.recursive, tuple(SourceRoot(*root) for root in job.roots), ())
        _, scratch, _ = BatchService(job.scratch, self.diagnostics).validate_folders(index, job.destination)
        if scratch != job.scratch:
            raise RetouchingError("JOB_FOLDER_CHANGED", "The frozen scratch folder resolves elsewhere. Restore it before resuming.")
        if job.backend != self.pipeline.backend:
            raise RetouchingError("JOB_RUNTIME_CHANGED", "Restore the job's recorded prepared backend or create a new job.")
        current_versions = runtime_versions(job.backend)
        if any(value != current_versions.get(key) for key, value in job.versions.items() if key != "windows"):
            raise RetouchingError("JOB_RUNTIME_CHANGED", "Restore the job's recorded runtime and prepared backend, or create a new job.")
        registry, catalog = self.pipeline.registry, self.pipeline.catalog
        status = validate_pipeline(job.pipeline, registry, catalog, job.checkpoints, job.backend)
        for original, item in zip(job.pipeline.stages, status.stages, strict=True):
            if original.enabled and (item.issues or item.dependency_errors):
                self._emit(job.job_id, "JOB_STAGE_UNAVAILABLE", "Cannot run the frozen enabled stage", level="ERROR",
                           stage_instance_id=item.stage.id, issues=[asdict(issue) for issue in item.issues],
                           dependency_errors=item.dependency_errors)
        if (not status.executable or status.definition != job.pipeline
                or fingerprint(model_manifests(job.pipeline, catalog)) != fingerprint(job.models)):
            raise RetouchingError("JOB_PIPELINE_CHANGED", "Restore the frozen pipeline's dependencies and checkpoint versions before resuming.")

    def _run(self, journal, job, control):
        for item, row in zip(job.inputs, journal.images(), strict=True):
            if row["status"] == "prepared":
                self._recover(journal, job, item, row)

        pending = [(item, row) for item, row in zip(job.inputs, journal.images(), strict=True)
                   if row["status"] in ("queued", "running", "prepared")]
        largest = max((item.working_bytes for item, row in pending if row["attempts"] < MAX_ATTEMPTS), default=0)
        remaining_output = 2 * sum(item.stamp.size for item, row in pending if row["attempts"] < MAX_ATTEMPTS)
        started = False
        for item, row in pending:
            if control.cancel.is_set():
                journal.cancel_remaining()
                break
            if control.pause.is_set():
                journal.set_status("paused")
                break
            attempt = row["attempts"] + 1
            if attempt > MAX_ATTEMPTS:
                message = "Interrupted image reached the replay limit. Create a new job for this input."
                journal.fail(item.image_id, "JOB_ATTEMPTS_EXHAUSTED", message, row["warnings"])
                self._emit(job.job_id, "JOB_ATTEMPTS_EXHAUSTED", message, level="ERROR", image_id=item.image_id)
                continue
            if not started:
                self._validate_run(job)
                self._emit(job.job_id, "JOB_STARTED", "Running the saved sequential job", previous_status=journal.status())
                journal.set_status("running")
                started = True
            self._check_space(job, BASE_SCRATCH_BYTES + largest, remaining_output)
            journal.begin(item.image_id)
            if attempt > 1:
                self._emit(job.job_id, "JOB_IMAGE_REPLAYED", "Replaying an uncommitted image after interruption",
                           image_id=item.image_id, attempt=attempt, source_path=item.path)
            self._process(journal, job, item, attempt)
            remaining_output -= 2 * item.stamp.size
        else:
            failed = any(row["status"] == "failed" for row in journal.images())
            journal.set_status("completed_with_errors" if failed else "completed")

        self._emit_accounting(journal, job)

    def _emit_accounting(self, journal, job):
        result = self._status(journal, job)
        counts = {state: sum(image.status == state for image in result.images)
                  for state in ("queued", "running", "prepared", "completed", "failed", "canceled")}
        self._emit(job.job_id, "JOB_RUN_STOPPED", "Saved current job accounting", job_status=result.status,
                   counts=counts, images_with_warnings=sum(image.warnings > 0 for image in result.images))

    @staticmethod
    def _check_scratch(job):
        try:
            scratch, existing = resolve_folder(job.scratch, allow_missing=True)
        except (OSError, ValueError) as exc:
            raise RetouchingError("JOB_FOLDER_CHANGED", f"Cannot reopen scratch storage at {job.scratch}. Restore access before resuming.") from exc
        if scratch != job.scratch:
            raise RetouchingError("JOB_FOLDER_CHANGED", "The frozen scratch folder resolves elsewhere. Restore it before resuming.")
        return scratch, existing

    def _check_space(self, job, scratch_bytes, output_bytes):
        check_destination(job)
        scratch, existing = self._check_scratch(job)
        volumes = volume_budgets(((job.destination, job.destination, output_bytes), (scratch, existing, scratch_bytes)))
        for volume in volumes:
            if volume.available_bytes < volume.required_bytes:
                paths = ", ".join(map(str, volume.paths))
                raise RetouchingError("JOB_SPACE_INSUFFICIENT", f"Free space on {paths} before resuming. "
                                      f"Need {volume.required_bytes:,} bytes, have {volume.available_bytes:,}.")

    def _recover(self, journal, job, item, row):
        attempt = row["unit_attempts"] if job.mode == "staged" else row["attempts"]
        try:
            receipt = PreparedExport.parse(row["receipt"])
        except (TypeError, ValueError) as exc:
            raise JournalError("Cannot validate a prepared export receipt. Retain the journal for inspection.") from exc
        try:
            output = recover_export(job, item, receipt)
        except (RetouchingError, OSError) as exc:
            self._record_failure(journal, job, item, attempt, exc, row["warnings"], fallback="JOB_EXPORT_RECOVERY_FAILED")
            return
        if output is not None:
            journal.complete(item.image_id)
            self._emit(job.job_id, "JOB_EXPORT_RECOVERED", "Recovered a verified export without rendering again",
                       image_id=item.image_id, attempt=attempt, output_path=output)

    def _process(self, journal, job, item, attempt):
        log = ImageDiagnostics(self.diagnostics)
        runtime = JpegRuntime(log, item.image_id, job.job_id, attempt=attempt)

        def process():
            state = self._decode(item, runtime)
            for stage in job.pipeline.stages:
                if stage.enabled:
                    stage_runtime = StageRuntime(log, job.backend, job.checkpoints, job.job_id, stage,
                                                 image_id=item.image_id, attempt=attempt)
                    with StageSession(self.pipeline.registry, self.pipeline.catalog, stage_runtime) as session:
                        state = session.process(state, image_id=item.image_id, attempt=attempt)
            return state

        try:
            self._publish(journal, job, item, runtime, process)
        except Exception as exc:
            self._record_failure(journal, job, item, attempt, exc, log.warnings, traced=log.traced_error,
                                 stage_id=log.failed_stage)

    def _publish(self, journal, job, item, runtime, load_state):
        def prepare_receipt(pending, destination):
            check_destination(job)
            expected, _ = output_path(job.destination, item.relative_path)
            if destination != expected or pending.parent != expected.parent:
                raise RetouchingError("JOB_FOLDER_CHANGED", "Destination changed during JPEG export. Restore it before resuming.")
            receipt = PreparedExport.capture(pending)
            journal.prepared(item.image_id, receipt.encode(), runtime.diagnostics.warnings)

        output = prepare_parent(job, item)
        export_jpeg(load_state(), output, settings=job.settings, pipeline_sha256=job.pipeline_sha256,
                    runtime=runtime, before_publish=prepare_receipt)
        journal.complete(item.image_id)
        self._emit(job.job_id, "JOB_IMAGE_COMPLETED", "Committed the verified JPEG result", image_id=item.image_id,
                   attempt=runtime.attempt, output_path=output, warnings=runtime.diagnostics.warnings)

    def _decode(self, item, runtime):
        self._check_source(item)
        state = decode_jpeg(item.path, source_id=item.image_id, runtime=runtime, expected_sha256=item.sha256)
        self._check_source(item)
        return state

    @staticmethod
    def _check_source(item):
        try:
            if current_file_stamp(item.path) != item.stamp:
                raise OSError("Source file attributes differ from the frozen input")
        except OSError as exc:
            raise RetouchingError("SOURCE_CHANGED", f"Source changed at {item.path}. Re-index it for a new job.") from exc

    def _record_failure(self, journal, job, item, attempt, exc, warnings, *, traced=False, stage_id=None,
                        fallback="JOB_IMAGE_FAILED"):
        if isinstance(exc, JournalError) or getattr(exc, "code", None) == "JOB_FOLDER_CHANGED":
            raise exc
        if disk_full(exc):
            raise RetouchingError("JOB_DISK_FULL", f"Storage ran out while processing {item.path}. Free space and resume the job.") from exc
        code = getattr(exc, "code", fallback)
        message = str(exc) or type(exc).__name__
        journal.fail(item.image_id, code, message, warnings)
        self._emit(job.job_id, "JOB_IMAGE_FAILED", message, level="ERROR", exc_info=not traced,
                   image_id=item.image_id, attempt=attempt, source_path=item.path, stage_instance_id=stage_id,
                   failure_code=code)

    def _emit(self, job_id, code, message, **details):
        self.diagnostics.emit(code, message, component="jobs", job_id=job_id, **details)
