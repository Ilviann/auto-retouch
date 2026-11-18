"""Internal subprocess entry point for running a saved job in isolation."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import sys
from threading import Event, Thread

from ..application.batch import BatchService
from ..application.job_service import JobControl, JobService
from ..application.pipeline_service import PipelineService
from ..diagnostics.service import EventLogger, redact


class WorkerDiagnostics(EventLogger):
    def __init__(self, stream, session_id, level, job_id, run_id):
        super().__init__(session_id, level)
        self.stream, self.job_id, self.run_id = stream, job_id, run_id
        self._frames = Queue(maxsize=128)
        self._dropped = Counter()
        self._finished = Event()
        self._writer_failed = Event()
        self._result = None
        self._writer = Thread(target=self._write_frames, name="worker-diagnostics", daemon=True)
        self._writer.start()

    def emit(self, code, message, **context):
        context.update(job_id=self.job_id, worker_run_id=self.run_id)
        super().emit(code, message, **context)

    def _store(self, event, encoded):
        severity = event["severity"]
        # Reserve room for warnings and failures when the parent writer is slow.
        allowance = {"DEBUG": 96, "INFO": 112}.get(severity, self._frames.maxsize)
        if self._frames.qsize() < allowance and not self._writer_failed.is_set():
            try:
                self._frames.put_nowait(encoded.encode("utf-8") + b"\n")
                return
            except Full:
                pass
        self._dropped[severity] += 1
        if severity not in ("DEBUG", "INFO"):
            self._emergency(f"Diagnostic fallback {encoded}")

    def _emergency(self, message):
        # Stderr is drained independently by the parent, even if its log disk is slow.
        try:
            print(message, file=sys.stderr, flush=True)
        except (OSError, UnicodeError):
            self._writer_failed.set()

    def _send(self, frame):
        remaining = memoryview(frame)
        while remaining:
            written = self.stream.write(remaining)
            if not written:
                raise OSError("The worker diagnostic pipe stopped accepting data.")
            remaining = remaining[written:]

    def _write_frames(self):
        try:
            with self.stream:
                while not self._finished.is_set() or not self._frames.empty():
                    try:
                        frame = self._frames.get(timeout=0.05)
                    except Empty:
                        continue
                    self._send(frame)
                summary = {"result": self._result, "dropped": dict(self._dropped)}
                self._send(json.dumps(summary, ensure_ascii=False).encode("utf-8") + b"\n")
        except (OSError, UnicodeError) as exc:
            self._writer_failed.set()
            self._emergency(f"Worker diagnostic pipe failed. Queued records may be missing. {redact(str(exc))}")

    def finish(self, error):
        self._result = error
        self._finished.set()
        self._writer.join(timeout=3)
        if self._writer.is_alive():
            self._emergency("Worker diagnostic drain timed out. Queued records may be missing.")


def _listen(control, diagnostics, finished):
    from ctypes import WinDLL, WinError, byref, get_last_error
    from ctypes.wintypes import BOOL, DWORD, HANDLE, LPDWORD, LPVOID
    from msvcrt import get_osfhandle

    # A blocking read on inherited stdin stalled native NumPy module loading on
    # Windows. Peek first so the control thread never waits inside a pipe read.
    peek = WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek.argtypes = (HANDLE, LPVOID, DWORD, LPDWORD, LPDWORD, LPDWORD)
    peek.restype = BOOL
    try:
        fd = sys.stdin.fileno()
        handle = get_osfhandle(fd)
        available = DWORD()
        while not finished.is_set():
            if not peek(handle, None, 0, None, byref(available), None):
                error = get_last_error()
                if error == 109:  # ERROR_BROKEN_PIPE
                    break
                raise WinError(error)
            if not available.value:
                finished.wait(0.05)
                continue
            match os.read(fd, 1):
                case b"P":
                    control.pause.set()
                case b"C":
                    control.cancel.set()
                case b"":
                    break
                case command:
                    raise ValueError(f"Unexpected worker control byte {command!r}")
    except (OSError, ValueError):
        diagnostics.emit("WORKER_CONTROL_FAILED", "Worker control pipe failed. Pausing at the next safe boundary.",
                         level="WARNING", component="worker", exc_info=True)
    # If the parent disappears, save the current unit and release the runner lock.
    if not finished.is_set():
        control.pause.set()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("job_id")
    parser.add_argument("backend")
    parser.add_argument("--pytorch-backend", choices=("cpu", "cuda"))
    parser.add_argument("session_id")
    parser.add_argument("run_id")
    parser.add_argument("level")
    args = parser.parse_args()
    # Reserve the original stdout pipe for framed diagnostics and the run result.
    # Keep native/library output and faulthandler on the independent stderr pipe.
    stream = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    diagnostics = WorkerDiagnostics(stream, args.session_id, args.level, args.job_id, args.run_id)
    control = JobControl()
    finished = Event()
    listener = Thread(target=_listen, args=(control, diagnostics, finished), name="worker-controls", daemon=True)
    listener.start()
    result = None
    running_job = False
    try:
        batch = BatchService(args.root / ".work", diagnostics)
        pipeline = PipelineService(args.root / "models", args.backend, diagnostics, pytorch_backend=args.pytorch_backend)
        jobs = JobService(args.root, batch, pipeline, diagnostics)
        diagnostics.emit("WORKER_READY", "Opened batch worker", component="worker", backend=args.backend,
                         pytorch_backend=args.pytorch_backend, executable=sys.executable, python=sys.version)
        running_job = True
        jobs.run(args.job_id, control=control)
    except Exception as exc:
        result = {"code": getattr(exc, "code", "JOB_RUN_FAILED"),
                  "message": redact(str(exc) or type(exc).__name__)[:4000]}
        # JobService records the traceback for processing failures itself.
        if not running_job:
            diagnostics.emit(result["code"], result["message"], level="ERROR", component="worker", exc_info=True)
    finally:
        finished.set()
        listener.join(timeout=1)
        diagnostics.finish(result)
    return int(result is not None)


if __name__ == "__main__":
    raise SystemExit(main())
