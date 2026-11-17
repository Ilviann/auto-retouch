"""Format bounded diagnostic records and persist them through one session writer."""

from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
import traceback
import uuid

from ..config import LoggingSettings

MAX_EVENT_BYTES = 65536

def redact(text: str) -> str:
    # Package-manager errors may include authenticated index URLs.
    text = re.sub(r"(https?://)[^\s/@]+(?::[^\s/@]*)?@", r"\1<credentials>@", text)
    return re.sub(r"(?i)([?&](?:token|key|password|secret)=)[^&\s]+", r"\1<redacted>", text)


class _EventHandler(logging.Handler):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def emit(self, record):
        self.owner._write(record)


class EventLogger:
    """Shared event formatting for local logging and the worker's pipe."""

    def __init__(self, session_id, level):
        self.session_id = session_id
        self._handler = _EventHandler(self)
        self._set_level(level)

    def _set_level(self, level):
        logger = logging.Logger("retouching_tool", level=level)
        logger.addHandler(self._handler)
        self.logger = logger

    def _write(self, record):
        event = {"schema_version": 1, "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                 "severity": record.levelname, "code": record.event_code,
                 "message": redact(record.getMessage())[:4000], "component": record.component,
                 "session_id": self.session_id, "process_id": os.getpid()}
        if os.environ.get("AUTO_RETOUCH_PARENT_SESSION"):
            event["parent_session_id"] = os.environ["AUTO_RETOUCH_PARENT_SESSION"]
        event.update({key: value for key, value in record.context.items() if key not in event})
        if record.exc_info:
            cause = redact("".join(traceback.format_exception(*record.exc_info)))
            event["exception"] = cause[:12000]
            if len(cause) > 12000:
                event["exception_truncated"] = True
        # Bound all data, including subprocess output and technical causes.
        encoded = json.dumps(event, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
            keep = {"schema_version", "timestamp", "severity", "code", "message", "component", "session_id", "process_id",
                    "parent_session_id", "exception", "exception_truncated", "job_id", "image_id", "stage_instance_id",
                    "attempt", "preview_request_id", "worker_run_id"}
            event = {k: v for k, v in event.items() if k in keep}
            event["truncated"] = True
            encoded = json.dumps(event, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
                event = {k: v[:1000] if isinstance(v, str) else v for k, v in event.items()}
                encoded = json.dumps(event, ensure_ascii=False)
        self._store(event, encoded)

    def emit(self, code: str, message: str, *, level="INFO", component="bootstrap", exc_info=False, **context):
        # Context contains structured scalars/identities, never photos or weights.
        safe_context = json.loads(redact(json.dumps(context, default=str, ensure_ascii=False)))
        self.logger.log(getattr(logging, level), message, exc_info=exc_info,
                        extra={"event_code": code, "component": component, "context": safe_context})


class Diagnostics(EventLogger):
    def __init__(self, directory: Path, settings: LoggingSettings | None = None, *, session_id=None, console=None):
        self.settings = settings or LoggingSettings()
        super().__init__(session_id or uuid.uuid4().hex, self.settings.level)
        self.console = console if console is not None else sys.stderr
        self.records = deque(maxlen=100)
        self.failure = None
        self.path = directory / f"auto-retouch-{self.session_id}.jsonl"
        self.stream = None
        self._size = 0
        self._part = 0
        self._open()

    def _fallback(self, exc):
        self.failure = f"LOG_WRITE_FAILED: Diagnostic records could not be saved to {self.path}: {redact(str(exc))}"
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError:
                pass  # Original write failure is already surfaced below.
            self.stream = None
        self._console(self.failure)

    def _console(self, message):
        try:
            self.console.write(message + "\n")
            self.console.flush()
        except (OSError, UnicodeError):
            # Memory fallback remains inspectable by the adapter when stderr also fails.
            self.failure = self.failure or "LOG_CONSOLE_FAILED: Console unavailable; records retained only in bounded memory"

    def _retain(self):
        owned = sorted((p for p in self.path.parent.glob("auto-retouch-*.jsonl*")
                        if re.fullmatch(r"auto-retouch-[0-9a-f]{32}\.jsonl(?:\.[0-9]+)?", p.name)
                        and p.is_file() and not p.is_symlink()), key=lambda p: p.stat().st_mtime)
        cutoff = time.time() - self.settings.retention_days * 86400
        total = sum(p.stat().st_size for p in owned)
        for path in owned:
            if path == self.path:
                continue
            size = path.stat().st_size
            if path.stat().st_mtime < cutoff or total > self.settings.max_total_mb * 1_000_000:
                path.unlink()
                total -= size

    def _open(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._apply_retention()
            self.stream = self.path.open("a", encoding="utf-8")
            self._size = self.path.stat().st_size
        except OSError as exc:
            self._fallback(exc)

    def _apply_retention(self):
        try:
            self._retain()
        except OSError as exc:
            self.failure = f"LOG_RETENTION_FAILED: Cannot enforce diagnostic retention in {self.path.parent}: {redact(str(exc))}"
            self._console(self.failure)

    def receive(self, event, encoded):
        """Persist a validated worker event with its original timestamp and process ID."""
        with self._handler.lock:
            self._store(event, encoded)

    @contextmanager
    def changing_settings(self, directory, settings):
        """Prepare the new writer and switch only after configuration persistence succeeds."""
        path = directory / self.path.name
        stream = None
        try:
            if path != self.path or self.stream is None:
                directory.mkdir(parents=True, exist_ok=True)
                stream = path.open("a", encoding="utf-8")
                size = os.fstat(stream.fileno()).st_size
            yield
            with self._handler.lock:
                if stream is not None:
                    self.failure = None
                    self.close()
                    self.path, self.stream, self._size = path, stream, size
                    stream = None
                if settings.level != self.settings.level:
                    self._set_level(settings.level)
                self.settings = settings
                self._apply_retention()
        finally:
            if stream is not None:
                stream.close()

    def _store(self, event, encoded):
        self.records.append(event)
        self._console(f"{event['severity']} {event['code']}: {event['message']}")
        if self.stream is None:
            return
        try:
            size = len((encoded + "\n").encode("utf-8"))
            if self._size + size > self.settings.max_file_mb * 1_000_000:
                self.stream.close()
                self._part += 1
                self.path.rename(self.path.with_suffix(f".jsonl.{self._part}"))
                self._open()
                if self.stream is None:
                    return
            self.stream.write(encoded + "\n")
            self.stream.flush()
            self._size += size
            self._apply_retention()
        except OSError as exc:
            self._fallback(exc)

    def close(self):
        with self._handler.lock:
            if self.stream is not None:
                try:
                    self.stream.close()
                except OSError as exc:
                    self._fallback(exc)
                finally:
                    self.stream = None
