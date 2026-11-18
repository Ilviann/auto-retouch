"""Write an inspectable, local-only copy of diagnostic evidence."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import blake2s
import json
import os
from pathlib import Path
import re
from secrets import token_bytes
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from ..errors import RetouchingError
from .service import redact

MAX_LOG_BYTES = 64 * 1024 * 1024
MEMBERS = ("README.txt", "settings.json", "events.jsonl")
CORRELATION_FIELDS = frozenset(("session_id", "parent_session_id", "job_id", "index_id", "image_id",
                                "stage_instance_id", "stage_id", "preview_request_id", "worker_run_id", "id"))
# Stop at common traceback/OS-error punctuation. With no delimiter, redact the
# rest of the line along with the path so spaces in a path never expose its tail.
PATH_START = r"(?:\b[a-z]:[\\/]+|\\{2}|(?<!:)//(?=[^/\s]))"
ABSOLUTE_PATH = re.compile(
    rf"""(?<="){PATH_START}[^"\r\n]+|(?<='){PATH_START}[^'\r\n]+(?:'(?=\w)[^'\r\n]+)*|"""
    rf"""{PATH_START}[^"<>|\r\n]*?(?=:\s|["<>|\r\n]|$)""", re.IGNORECASE)
PHOTO_EXTENSION = re.compile(r"\.jpe?g\b", re.IGNORECASE)
PHOTO_NAME = re.compile(r"(?<![\w.])[\w.%~()'’+\-]+(?:[ \t\\/]+[\w.%~()'’+\-]+)*\.jpe?g\b",
                        re.IGNORECASE)
PHOTO_DIRECTORY = re.compile(r"(?<![\w.])(?:[\w.%~()'’+ -]+[\\/])+(?=<photo-[0-9a-f]{16}>)")
PATH_FIELDS = frozenset(("path", "paths", "sources", "checkpoints", "temporary", "logs", "executable", "bootstrap_log"))


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    path: Path
    members: tuple[str, ...]
    event_count: int


class _Redactor:
    def __init__(self, photo_names):
        self.key = token_bytes(32)
        self.photo_names = self._pattern(photo_names)
        self.users = self._pattern((Path.home().name, os.environ.get("USERNAME", "")))

    @staticmethod
    def _pattern(values):
        unique = sorted({value for value in values if value}, key=lambda value: (-len(value), value))
        if not unique:
            return None
        return re.compile(r"(?<![\w.])(?:" + "|".join(map(re.escape, unique)) + r")(?![\w.])", re.IGNORECASE)

    def _alias(self, kind, value):
        normalized = re.sub(r"[\\/]+", "/", value).rstrip("/").casefold()
        if normalized.startswith("/?/unc/"):
            normalized = "/" + normalized.removeprefix("/?/unc/")
        elif normalized.startswith(("/?/", "/./")):
            normalized = normalized[3:]
        digest = blake2s(normalized.encode("utf-8", "surrogatepass"), key=self.key, digest_size=8).hexdigest()
        return f"<{kind}-{digest}>"

    def text(self, value):
        value = redact(value)
        value = ABSOLUTE_PATH.sub(lambda match: self._alias("path", match[0].rstrip()), value)
        if PHOTO_EXTENSION.search(value):
            if self.photo_names:
                value, replaced = self.photo_names.subn(lambda match: self._alias("photo", match[0]), value)
                if replaced:
                    value = PHOTO_DIRECTORY.sub(lambda match: self._alias("path", match[0]) + "/", value)
            value = PHOTO_NAME.sub(lambda match: self._alias("photo", match[0]), value)
        if self.users:
            value = self.users.sub(lambda match: self._alias("user", match[0]), value)
        return value

    def data(self, value, field=""):
        if isinstance(value, dict):
            return {self.text(key): self.data(item, key) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [self.data(item, field) for item in value]
        if isinstance(value, str):
            if field in CORRELATION_FIELDS:
                return value
            if field in PATH_FIELDS or field.endswith(("_path", "_paths")):
                return self._alias("path", value)
            return self.text(value)
        return value


def write_report(history, destination, settings, *, job_id, photo_names, limitations, cancelled):
    """Stream retained events into a temporary ZIP and publish it without replacing files."""
    if destination.suffix.lower() != ".zip":
        raise RetouchingError("DIAGNOSTIC_REPORT_PATH", "Choose a .zip filename for the diagnostic report.")
    if destination.exists():
        raise RetouchingError("DIAGNOSTIC_REPORT_EXISTS", "Choose a new filename. A file already exists at that location.")
    if cancelled():
        return None
    scrub = _Redactor(photo_names)
    session_id = history.diagnostics.session_id
    temporary = None
    count = size = omitted = truncated = 0
    stderr_available = False
    delivery_gap = False
    try:
        with NamedTemporaryFile(dir=destination.parent, prefix=".auto-retouch-report-", suffix=".tmp",
                                delete=False) as stream:
            temporary = Path(stream.name)
            with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
                with archive.open("events.jsonl", "w") as events:
                    def collect(_time, record):
                        nonlocal count, size, omitted, truncated, stderr_available, delivery_gap
                        if job_id:
                            if record.get("job_id") != job_id:
                                return
                        elif session_id not in (record.get("session_id"), record.get("parent_session_id")):
                            return
                        delivery_gap |= record["code"] in ("WORKER_LOG_DROPPED", "WORKER_LOG_INCOMPLETE",
                                                          "WORKER_STDERR_FAILED", "WORKER_EXITED")
                        if not omitted:
                            line = (json.dumps(scrub.data(record), ensure_ascii=True) + "\n").encode("ascii")
                            if size + len(line) <= MAX_LOG_BYTES:
                                events.write(line)
                                size += len(line)
                                count += 1
                                stderr_available |= bool(record.get("stderr_tail"))
                                truncated += int(any(record.get(key) for key in ("truncated", "exception_truncated",
                                                                                "stderr_truncated")))
                                return
                        omitted += 1

                    read = history.visit(collect, cancelled=cancelled, job_id=job_id or None)
                if cancelled():
                    return None
                notes = list(limitations)
                if read.invalid_records or read.skipped_files:
                    notes.append(f"History scan skipped {read.skipped_files} unreadable files or folders and "
                                 f"{read.invalid_records} invalid records.")
                if read.storage_warning:
                    notes.append(scrub.text(read.storage_warning))
                if omitted:
                    notes.append(f"The event size limit omitted {omitted} matching records after the export filled.")
                if not count:
                    notes.append("No matching events remain in the available logs or memory.")
                if truncated:
                    notes.append(f"{truncated} included events have truncated diagnostic details.")
                if delivery_gap:
                    notes.append("Worker diagnostics may be incomplete. See worker exit and delivery events.")
                if not stderr_available:
                    notes.append("No captured worker stderr is available in the included events.")
                scope = f"Saved job {job_id}" if job_id else f"Application session {session_id}"
                report = [scope, f"Created {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
                          f"{count} diagnostic events", "", "Contents", "README.txt  Report scope and coverage",
                          "settings.json  Available runtime, pipeline and settings evidence",
                          "events.jsonl  Available structured events, including captured stderr", "",
                          "Coverage", *notes, "Rotated, expired or undelivered records may be missing.", "",
                          "Review this local copy before sharing. Redaction of free text is best effort."]
                archive.writestr("settings.json", json.dumps(scrub.data(settings), indent=2, ensure_ascii=True) + "\n")
                archive.writestr("README.txt", "\n".join(report) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if cancelled():
            return None
        try:
            temporary.rename(destination)  # Windows rename fails atomically if the target already exists.
        except FileExistsError as exc:
            raise RetouchingError("DIAGNOSTIC_REPORT_EXISTS", "Choose a new filename. A file already exists at that location.") from exc
        temporary = None
        return DiagnosticReport(destination, MEMBERS, count)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
