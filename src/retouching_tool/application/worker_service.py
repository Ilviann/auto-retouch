"""Run saved jobs in a subprocess and collect their output in the background."""

from dataclasses import dataclass
import json
import subprocess
import sys
from threading import Event, Lock, Thread
from uuid import uuid4

from ..diagnostics.service import MAX_EVENT_BYTES, redact
from ..errors import RetouchingError
from ..jobs.journal import validate_job_id

STDERR_LIMIT = 8192


@dataclass(frozen=True)
class WorkerOutcome:
    job_id: str
    run_id: str
    exit_code: int
    error_code: str | None
    message: str | None
    diagnostics_incomplete: bool


class WorkerRun:
    def __init__(self, process, job_id, run_id, diagnostics):
        self.job_id, self.run_id = job_id, run_id
        self._process, self._diagnostics = process, diagnostics
        self._done = Event()
        self._control_lock = Lock()
        self._last_request = None
        self._outcome = None
        Thread(target=self._monitor, name="batch-worker-monitor", daemon=True).start()

    def poll(self) -> WorkerOutcome | None:
        return self._outcome if self._done.is_set() else None

    def wait(self, timeout=None) -> WorkerOutcome:
        if not self._done.wait(timeout):
            raise TimeoutError(f"Batch worker for {self.job_id} is still active.")
        return self._outcome

    def pause(self) -> bool:
        """Request a pause at the next scheduler boundary. Observe the result separately."""
        return self._request(b"P")

    def cancel(self) -> bool:
        """Request cancellation of remaining work. A pending pause can be superseded."""
        return self._request(b"C")

    def _request(self, command):
        with self._control_lock:
            if (self._last_request in (command, b"C") or self._process.stdin.closed
                    or self._process.poll() is not None):
                return False
            try:
                self._process.stdin.write(command)
                self._process.stdin.flush()
            except OSError:
                if self._process.poll() is None:
                    self._emit("WORKER_CONTROL_FAILED", "Could not send the request. Check the worker's current status.",
                               level="WARNING", exc_info=True)
                return False
            self._last_request = command
            return True

    def _emit(self, code, message, **context):
        self._diagnostics.emit(code, message, component="worker", job_id=self.job_id,
                               worker_run_id=self.run_id, launcher_pid=self._process.pid, **context)

    def _read_stderr(self, tail):
        total = 0
        try:
            with self._process.stderr as stream:
                while chunk := stream.read1(4096):
                    total += len(chunk)
                    tail.extend(chunk)
                    del tail[:-STDERR_LIMIT]
        except OSError:
            self._emit("WORKER_STDERR_FAILED", "Could not read all worker stderr.", level="WARNING", exc_info=True)
            return total > STDERR_LIMIT, True
        return total > STDERR_LIMIT, False

    def _decode_frame(self, raw, stream, result_seen):
        if len(raw) > MAX_EVENT_BYTES + 1 or not raw.endswith(b"\n"):
            while not raw.endswith(b"\n") and (raw := stream.readline(MAX_EVENT_BYTES + 2)):
                pass
            raise ValueError("Oversized or incomplete worker frame")
        if result_seen:
            raise ValueError("Unexpected worker frame after the run result")
        encoded = raw.decode("utf-8").removesuffix("\n")
        frame = json.loads(encoded)
        if not isinstance(frame, dict):
            raise ValueError("Expected a worker message object")
        return frame, encoded

    def _validate_event(self, frame):
        if (frame.get("schema_version") != 1 or frame.get("worker_run_id") != self.run_id
                or frame.get("job_id") != self.job_id or frame.get("session_id") != self._diagnostics.session_id
                or any(not isinstance(frame.get(key), str) for key in ("code", "severity", "message"))):
            raise ValueError("Unexpected worker diagnostic event")

    def _monitor(self):
        tail = bytearray()
        stderr_truncated = stderr_failed = False

        def drain_stderr():
            nonlocal stderr_truncated, stderr_failed
            stderr_truncated, stderr_failed = self._read_stderr(tail)

        stderr = Thread(target=drain_stderr, name="batch-worker-stderr", daemon=True)
        stderr.start()
        summary, last_image = None, {}
        incomplete = False
        try:
            with self._process.stdout as stream:
                while raw := stream.readline(MAX_EVENT_BYTES + 2):
                    try:
                        frame, encoded = self._decode_frame(raw, stream, summary is not None)
                        if set(frame) == {"result", "dropped"}:
                            summary = self._read_summary(frame)
                            continue
                        self._validate_event(frame)
                    except (ValueError, RecursionError):
                        if not incomplete:
                            self._emit("WORKER_PROTOCOL_FAILED", "Discarded an invalid worker record. Some diagnostics may be missing.",
                                       level="WARNING", exc_info=True)
                        incomplete = True
                        continue
                    if frame.get("image_id") is not None:
                        last_image = {key: frame[key] for key in ("image_id", "stage_instance_id", "attempt")
                                      if frame.get(key) is not None}
                    self._diagnostics.receive(frame, encoded)
        except OSError:
            self._emit("WORKER_PROTOCOL_FAILED", "Could not read all worker diagnostics.",
                       level="WARNING", exc_info=True)
            incomplete = True
        finally:
            # Let a worker finish its current unit if the diagnostic read stopped early.
            with self._control_lock:
                try:
                    self._process.stdin.close()
                except OSError:
                    self._emit("WORKER_CONTROL_FAILED", "Could not close the worker control pipe cleanly.",
                               level="WARNING", exc_info=True)
                    incomplete = True

        exit_code = self._process.wait()
        stderr.join()
        incomplete |= stderr_truncated or stderr_failed
        stderr_tail = redact(tail.decode("utf-8", errors="replace")).strip()
        if stderr_tail:
            self._emit("WORKER_STDERR", "Captured worker stderr. See technical details.", level="WARNING",
                       stderr_tail=stderr_tail, stderr_truncated=stderr_truncated, **last_image)

        error, dropped = summary or (None, {})
        if dropped:
            incomplete = True
            self._emit("WORKER_LOG_DROPPED", "Worker diagnostics exceeded the delivery buffer. See stderr for any fallback details.",
                       level="WARNING", dropped=dropped, **last_image)
        error_code = message = None
        if error is not None and exit_code == 1:
            error_code, message = error["code"], error["message"]
            self._emit("WORKER_RUN_FAILED", message, level="ERROR", exit_code=exit_code,
                       failure_code=error_code, **last_image)
        elif exit_code:
            incomplete = True
            error_code = "WORKER_EXITED"
            message = "Batch worker exited unexpectedly. Review its diagnostics and resume the saved job."
            self._emit(error_code, message, level="ERROR", exit_code=exit_code, **last_image)
        elif summary is None or error is not None:
            incomplete = True
            self._emit("WORKER_LOG_INCOMPLETE", "The worker did not confirm a clean diagnostic drain. Check the job journal.",
                       level="WARNING", **last_image)
        self._outcome = WorkerOutcome(self.job_id, self.run_id, exit_code, error_code, message,
                                      incomplete or self._diagnostics.failure is not None)
        self._done.set()

    @staticmethod
    def _read_summary(frame):
        result, dropped = frame["result"], frame["dropped"]
        if (result is not None and (not isinstance(result, dict) or set(result) != {"code", "message"}
                                    or any(not isinstance(value, str) for value in result.values()))):
            raise ValueError("Invalid worker exception result")
        if (not isinstance(dropped, dict) or any(type(value) is not int or value <= 0 for value in dropped.values())):
            raise ValueError("Invalid worker diagnostic loss counts")
        return result, dropped


class WorkerService:
    def __init__(self, root, backend, diagnostics, *, pytorch_backend=None):
        self.root, self.backend, self.diagnostics = root, backend, diagnostics
        self.pytorch_backend = pytorch_backend
        self._lock = Lock()
        self._active = None

    def _command(self, job_id, run_id):
        return [sys.executable, "-I", "-B", "-X", "utf8", "-X", "faulthandler",
                "-m", "retouching_tool.jobs.worker_process", str(self.root.resolve()), job_id,
                self.backend, *(["--pytorch-backend", self.pytorch_backend] if self.pytorch_backend else []),
                self.diagnostics.session_id, run_id, self.diagnostics.settings.level]

    def start(self, job_id) -> WorkerRun:
        """Start or resume a saved job. Read progress through the existing job service."""
        with self._lock:
            run_id = uuid4().hex
            try:
                validate_job_id(job_id)
                if self._active is not None and self._active.poll() is None:
                    raise RetouchingError("JOB_RUNNER_BUSY", "Wait for the active batch worker to stop before starting another job.")
                try:
                    process = subprocess.Popen(self._command(job_id, run_id), stdin=subprocess.PIPE,
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               creationflags=subprocess.CREATE_NO_WINDOW)
                except OSError as exc:
                    raise RetouchingError("WORKER_START_FAILED",
                                          "Could not launch the batch worker. Check access to the prepared Python environment.") from exc
            except RetouchingError as exc:
                self.diagnostics.emit(exc.code, str(exc), level="ERROR", component="worker", exc_info=True,
                                      job_id=job_id, worker_run_id=run_id)
                raise
            self._active = WorkerRun(process, job_id, run_id, self.diagnostics)
            return self._active
