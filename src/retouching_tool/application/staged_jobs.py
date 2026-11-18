"""Stage passes over the durable frontier of a frozen, toolkit-free batch."""

from dataclasses import asdict, replace
import json

from ..errors import RetouchingError
from ..imaging.jpeg import JpegRuntime
from ..jobs.execution import ImageDiagnostics, disk_full
from ..jobs.journal import MAX_ATTEMPTS, TERMINAL, JournalError
from ..pipeline.executor import StageRuntime, StageSession
from ..storage.artifact_codec import require
from ..storage.artifacts import ArtifactIdentity, ArtifactRef, ArtifactStore, fingerprint
from ..storage.batch_paths import output_path
from .batch import BASE_SCRATCH_BYTES, CHECKPOINT_OVERHEAD_BYTES

ACTIVE = ("queued", "running", "prepared")


def _artifact(row):
    return ArtifactRef(**json.loads(row["artifact"])) if row["artifact"] is not None else None


def validate_staged_progress(job, rows):
    count = sum(stage.enabled for stage in job.pipeline.stages)
    for row in rows:
        cursor, store_id, ref = row["next_stage"], row["store_id"], _artifact(row)
        if store_id is not None:
            ArtifactRef(store_id, "0" * 64, "0" * 64)
        require(cursor <= count and row["unit_attempts"] <= MAX_ATTEMPTS
                and row["artifact_replays"] < MAX_ATTEMPTS, "Invalid stage cursor or replay count")
        require(ref is None or (cursor > 0 and ref.store_id == store_id), "Checkpoint belongs to another image store")
        require(cursor == 0 or row["status"] not in ACTIVE or ref is not None, "Missing staged checkpoint receipt")
        require(row["status"] not in ("prepared", "completed") or cursor == count, "Export preceded a required stage")


class StagedRunner:
    def __init__(self, service, journal, job, control):
        self.service = service
        self.journal = journal
        self.job = job
        self.control = control
        self.stages = tuple(stage for stage in job.pipeline.stages if stage.enabled)
        self.registry = service.pipeline.registry
        self.catalog = service.pipeline.catalog

    def run(self):
        if self.journal.status() in TERMINAL:
            self._clean_finished()
            return
        for item, row in self._rows():
            if row["status"] == "prepared":
                self.service._recover(self.journal, self.job, item, row)
        if self._pending() and not self._stop_requested():
            self.service._validate_run(self.job)
            self._prepare_checkpoints()
            environment = {"backend": self.job.backend, "versions": self.job.versions}
            if self.job.pytorch_backend is not None:
                environment["pytorch_backend"] = self.job.pytorch_backend
            self._environment = fingerprint(environment)
            self._models = fingerprint(self.job.models)
            self._prefixes = self._prefix_digests()
            self.service._emit(self.job.job_id, "JOB_STARTED", "Running the saved staged job", mode="staged",
                               previous_status=self.journal.status())
            self.journal.set_status("running")
            self._run_passes()
        if not self._pending() and self.journal.status() != "canceled":
            self._clean_finished()
            failed = any(row["status"] == "failed" for _, row in self._rows())
            self.journal.set_status("completed_with_errors" if failed else "completed")
        self.service._emit_accounting(self.journal, self.job)

    def _rows(self):
        return zip(self.job.inputs, self.journal.images(), strict=True)

    def _pending(self):
        return [(item, row) for item, row in self._rows() if row["status"] in ACTIVE]

    def _stop_requested(self):
        if self.control.cancel.is_set():
            self.journal.cancel_remaining()
            self._clean_finished()
            return True
        if self.control.pause.is_set():
            self.journal.set_status("paused")
            return True
        return False

    def _runtime(self, item, stage_index, attempt, log):
        if stage_index == len(self.stages):
            return JpegRuntime(log, item.image_id, self.job.job_id, attempt=attempt)
        stage = self.stages[stage_index]
        return StageRuntime(log, self.job.stage_backend(self.registry, stage), self.job.checkpoints, self.job.job_id,
                            stage, image_id=item.image_id, attempt=attempt, resize_approved=stage.id in self.job.resize_approvals)

    def _store(self, item, row):
        store_id = row["store_id"]
        if store_id is None:
            return None
        scratch, _ = self.service._check_scratch(self.job)
        directory = scratch / f"auto-retouch-job-{store_id}"
        try:
            directory.lstat()
        except FileNotFoundError:
            return None
        store = ArtifactStore.open(directory, self.job.job_id, self.service.diagnostics, image_id=item.image_id)
        if store.directory != directory:
            raise RetouchingError("JOB_FOLDER_CHANGED", "Checkpoint path resolves elsewhere. Restore it before resuming.")
        return store

    def _clean_finished(self):
        for item, row in self._rows():
            if row["status"] not in ACTIVE and row["store_id"] is not None:
                self._retire(item, row, self._store(item, row))

    def _retire(self, item, row, store):
        if store is not None:
            stage_index = max(0, row["next_stage"] - 1)
            runtime = self._runtime(item, stage_index, row["unit_attempts"], self.service.diagnostics)
            store.retain_image_checkpoint(None, runtime=runtime)
        if row["store_id"] is not None:
            self.journal.clear_store(item.image_id, row["store_id"])

    def _prepare_checkpoints(self):
        self._clean_finished()
        for item, row in self._pending():
            try:
                output_path(self.job.destination, item.relative_path)
            except (RetouchingError, OSError) as exc:
                self.service._record_failure(self.journal, self.job, item, row["unit_attempts"], exc, row["warnings"])
                self._retire(item, row, self._store(item, row))
                continue
            if row["store_id"] is None:
                continue
            store = self._store(item, row)
            ref = _artifact(row)
            stage_index = max(0, row["next_stage"] - 1)
            runtime = self._runtime(item, stage_index, row["unit_attempts"], self.service.diagnostics)
            if store is None:
                if ref is not None:
                    self._restart_image(item, row, store, runtime, "Checkpoint directory is missing")
                else:
                    self._retire(item, row, store)
                continue
            try:
                store.retain_image_checkpoint(ref, runtime=runtime)
            except RetouchingError as exc:
                if exc.code != "ARTIFACT_INVALID":
                    raise
                self._restart_image(item, row, store, runtime, str(exc))

    def _prefix_digests(self):
        definitions = self.job.pipeline.to_json()["stages"]
        prefix, contracts = [], []
        digests = []
        for definition, stage in zip(definitions, self.job.pipeline.stages, strict=True):
            prefix.append(definition)
            if stage.enabled:
                descriptor = asdict(self.registry.descriptor(stage.operation, stage.operation_version))
                if descriptor["resize_limit"] is None:
                    del descriptor["resize_limit"]
                if self.job.pytorch_backend is None and descriptor["inference_runtime"] == "onnxruntime":
                    # Preserve contract hashes in checkpoints created before per-runtime devices.
                    del descriptor["inference_runtime"]
                contracts.append(descriptor)
                digests.append((fingerprint(prefix), fingerprint(contracts)))
        return digests

    def _identity(self, item, stage_index):
        prefix, contracts = self._prefixes[stage_index]
        return ArtifactIdentity(item.image_id, item.sha256, prefix, self._environment, self._models, contracts, seed=0)

    def _run_passes(self):
        while pending := self._pending():
            if self._stop_requested():
                break
            stage_index = min(row["next_stage"] for _, row in pending)
            candidates = [(item, row) for item, row in pending if row["next_stage"] == stage_index]
            output = 2 * sum(item.stamp.size for item, _ in pending)
            missing = sum(item.working_bytes + CHECKPOINT_OVERHEAD_BYTES + item.header_bytes
                          for item, row in pending if row["next_stage"] == 0 and self.stages)
            replacing = max((item.working_bytes + CHECKPOINT_OVERHEAD_BYTES for item, row in pending
                             if row["next_stage"] < len(self.stages)), default=0)
            self.service._check_space(self.job, BASE_SCRATCH_BYTES + missing + replacing, output)
            if stage_index == len(self.stages):
                self._pass(candidates, stage_index, output)
            else:
                stage = self.stages[stage_index]
                runtime = StageRuntime(self.service.diagnostics, self.job.stage_backend(self.registry, stage),
                                       self.job.checkpoints, self.job.job_id, stage, resize_approved=stage.id in self.job.resize_approvals)
                with StageSession(self.registry, self.catalog, runtime) as session:
                    self._pass(candidates, stage_index, output, session=session)

    def _pass(self, candidates, stage_index, output, *, session=None):
        for item, row in candidates:
            if self.control.pause.is_set() or self.control.cancel.is_set():
                return
            attempt = row["unit_attempts"] + 1
            log = ImageDiagnostics(self.service.diagnostics, row["warnings"])
            runtime = self._runtime(item, stage_index, attempt, log)
            store = self._store(item, row)
            if attempt > MAX_ATTEMPTS:
                message = "Interrupted unit reached the replay limit. Create a new job for this input."
                self.journal.fail(item.image_id, "JOB_ATTEMPTS_EXHAUSTED", message, log.warnings)
                runtime.emit("JOB_ATTEMPTS_EXHAUSTED", message, level="ERROR")
                self._retire(item, row, store)
            else:
                self._unit(item, row, store, runtime, stage_index, output, session)
            if session is None:
                output -= 2 * item.stamp.size

    def _unit(self, item, row, store, runtime, stage_index, output, session):
        log = runtime.diagnostics
        self.service._check_space(self.job, BASE_SCRATCH_BYTES, output)
        self.journal.begin_unit(item.image_id, stage_index)
        if runtime.attempt > 1:
            runtime.emit("JOB_IMAGE_REPLAYED", "Replaying an uncommitted image-stage unit", source_path=item.path)
        try:
            state = self._restore(item, row, store, runtime)
            if session is None:
                self.service._publish(self.journal, self.job, item, runtime, lambda: state)
            else:
                state = session.process(state, image_id=item.image_id, attempt=runtime.attempt,
                                        diagnostics=log)
                scratch_bytes = BASE_SCRATCH_BYTES + state.pixels.nbytes + CHECKPOINT_OVERHEAD_BYTES
                if stage_index == 0:
                    scratch_bytes += item.header_bytes
                self.service._check_space(self.job, scratch_bytes, output)
                if store is None:
                    store = ArtifactStore.create(self.job.scratch, self.job.job_id, self.service.diagnostics,
                                                 image_id=item.image_id)
                    self.journal.attach_store(item.image_id, store.marker["store_id"])
                ref = store.commit(state, self._identity(item, stage_index), runtime=runtime)
                self.journal.commit_stage(item.image_id, stage_index, asdict(ref), log.warnings)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == "ARTIFACT_INVALID":
                self._restart_image(item, row, store, runtime, str(exc))
                return
            storage_full = disk_full(exc)
            if storage_full or isinstance(exc, JournalError) or code in (
                    "ARTIFACT_WRITE_FAILED", "ARTIFACT_STORE_FAILED", "JOB_SPACE_INSUFFICIENT", "JOB_FOLDER_CHANGED"):
                runtime.emit("JOB_UNIT_INTERRUPTED", "Unit interrupted. Resolve the storage or journal error and resume.",
                             level="ERROR", exc_info=not log.traced_error, source_path=item.path)
                if storage_full:
                    raise RetouchingError("JOB_DISK_FULL", "Storage ran out during staged processing. Free space and resume.") from exc
                raise
            stage_id = self.stages[stage_index].id if session is not None else None
            self.service._record_failure(self.journal, self.job, item, runtime.attempt, exc, log.warnings,
                                         traced=log.traced_error, stage_id=stage_id)
            self._retire(item, row, store)
            return
        if session is None:
            self._retire(item, row, store)
        else:
            store.retain_image_checkpoint(ref, runtime=runtime)

    def _restore(self, item, row, store, runtime):
        if row["next_stage"] == 0:
            decode_runtime = JpegRuntime(runtime.diagnostics, item.image_id, self.job.job_id, attempt=runtime.attempt)
            return self.service._decode(item, decode_runtime)
        if store is None:
            raise RetouchingError("ARTIFACT_INVALID", "Checkpoint directory disappeared before restoration")
        return store.read(_artifact(row), self._identity(item, row["next_stage"] - 1), runtime=runtime)

    def _restart_image(self, item, row, store, runtime, reason):
        if row["artifact_replays"] >= MAX_ATTEMPTS - 1:
            message = "Intermediate recovery reached the replay limit. Check scratch storage and create a new job for this input."
            self.journal.fail(item.image_id, "JOB_ARTIFACT_REPLAYS_EXHAUSTED", message, row["warnings"])
            runtime.emit("JOB_ARTIFACT_REPLAYS_EXHAUSTED", message, level="ERROR", cause=reason)
        else:
            self.journal.rewind(item.image_id)
            runtime.emit("JOB_ARTIFACT_REPLAY", "Intermediate unavailable. Rebuilding this image from its frozen source.",
                         level="WARNING", cause=reason, previous_stages=row["next_stage"])
        self._retire(item, row, store)
