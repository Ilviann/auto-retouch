"""Read retained diagnostic events without Qt or opening any processing inputs."""

from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from heapq import heappush, heapreplace
import json
import os
from pathlib import Path

from ..config import LOG_LEVELS
from .service import LOG_FILENAME, MAX_EVENT_BYTES

RESULT_LIMIT = 200


@dataclass(frozen=True, slots=True)
class DiagnosticQuery:
    text: str = ""
    minimum_severity: str = "DEBUG"
    job_id: str = ""
    image_id: str = ""
    stage_id: str = ""


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    timestamp: datetime
    severity: str
    code: str
    message: str
    job_id: str
    image_id: str
    stage_id: str
    operation: str
    operation_version: int | None
    details: str


@dataclass(frozen=True, slots=True)
class DiagnosticRead:
    directory: Path
    skipped_files: int
    invalid_records: int
    storage_warning: str | None


@dataclass(frozen=True, slots=True)
class DiagnosticResults(DiagnosticRead):
    events: tuple[DiagnosticEvent, ...]
    has_more: bool


def _open_log(path):
    if os.name != "nt":
        return path.open("rb")

    # A regular Windows Python file handle blocks renames and retention deletes.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    generic_read, share_read_write_delete, open_existing = 0x80000000, 7, 3
    sequential_scan = 0x08000000
    handle = create(str(path), generic_read, share_read_write_delete, None, open_existing, sequential_scan, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        close(handle)
        raise
    return os.fdopen(fd, "rb")


def _records(stream, remaining, cancelled):
    oversized = False
    while remaining and not cancelled():
        line = stream.readline(min(remaining, MAX_EVENT_BYTES + 2))
        if not line:
            break
        remaining -= len(line)
        if not line.endswith(b"\n") and remaining:
            oversized = True
            continue
        yield None if oversized or not line.endswith(b"\n") else line
        oversized = False


def _event_time(record):
    if (not isinstance(record, dict) or type(record.get("schema_version")) is not int or record["schema_version"] != 1
            or any(not isinstance(record.get(key), str) for key in ("timestamp", "severity", "code", "message"))
            or record["severity"] not in LOG_LEVELS):
        raise ValueError("Unsupported diagnostic event record")
    time = datetime.fromisoformat(record["timestamp"])
    if time.utcoffset() is None:
        raise ValueError("Diagnostic event has no time zone")
    return time.astimezone(timezone.utc)


def _events(path, seen_files, cancelled, invalid_record):
    with _open_log(path) as stream:
        stamp = os.fstat(stream.fileno())
        identity = (stamp.st_dev, stamp.st_ino)
        if stamp.st_ino:
            # Rotation can expose the same file under both its active and backup names.
            if identity in seen_files:
                return
            seen_files.add(identity)
        for line in _records(stream, stamp.st_size, cancelled):
            try:
                if line is None or len(line.rstrip(b"\r\n")) > MAX_EVENT_BYTES:
                    raise ValueError("Oversized or incomplete diagnostic record")
                record = json.loads(line.decode("utf-8"))
                yield _event_time(record), record
            except (ValueError, RecursionError, OverflowError) as exc:
                invalid_record(path, exc)


def _paths(directory, directories, cancelled, failed_read):
    for root in directories:
        if cancelled():
            return
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if cancelled():
                        return
                    if LOG_FILENAME.fullmatch(entry.name):
                        path = Path(entry.path)
                        try:
                            if entry.is_file(follow_symlinks=False):
                                yield path
                        except OSError as exc:
                            failed_read(path, exc)
        except FileNotFoundError as exc:
            # Only the current directory must exist. The bootstrap path is optional.
            if root == directory:
                failed_read(root, exc)
        except OSError as exc:
            failed_read(root, exc)


def _contains(value, needle):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif needle in str(item).casefold():
            return True
    return False


def _context(record, key):
    value = record.get(key)
    return value if isinstance(value, str) else ""


def _display_event(time, record):
    version = record.get("operation_version")
    priority = ("code", "message", "exception", "technical_cause", "stderr_tail")
    fields = {key: record[key] for key in priority if key in record}
    fields.update(record)
    details = "\n".join(f"{key}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}"
                        for key, value in fields.items())
    details = details.encode("utf-8", "backslashreplace").decode("utf-8")
    return DiagnosticEvent(time, record["severity"], record["code"], record["message"], _context(record, "job_id"),
                           _context(record, "image_id"), _context(record, "stage_instance_id"),
                           _context(record, "operation"), version if type(version) is int else None, details)


class DiagnosticHistory:
    def __init__(self, diagnostics, bootstrap_directory):
        self.diagnostics = diagnostics
        self.bootstrap_directory = bootstrap_directory

    def search(self, query: DiagnosticQuery, *, cancelled=lambda: False) -> DiagnosticResults:
        needle = query.text.strip().casefold()
        filters = {key: value.strip() for key, value in (("job_id", query.job_id), ("image_id", query.image_id),
                                                        ("stage_instance_id", query.stage_id)) if value.strip()}
        severities = LOG_LEVELS[LOG_LEVELS.index(query.minimum_severity):]
        latest = []
        order = 0

        def collect(time, record):
            nonlocal order
            if (record["severity"] not in severities or any(record.get(key) != value for key, value in filters.items())
                    or needle and not _contains(record, needle)):
                return
            order += 1
            row = (time, order, record)
            if len(latest) < RESULT_LIMIT:
                heappush(latest, row)
            elif time >= latest[0][0]:
                heapreplace(latest, row)

        read = self.visit(collect, cancelled=cancelled, **filters)
        events = tuple(_display_event(time, record) for time, _, record in sorted(latest, reverse=True))
        return DiagnosticResults(read.directory, read.skipped_files, read.invalid_records, read.storage_warning,
                                 events, order > RESULT_LIMIT)

    def visit(self, collect, *, cancelled, **context) -> DiagnosticRead:
        """Stream validated retained events plus memory-only occurrences to a consumer."""
        directory, previous_directories, recent, storage_warning = self.diagnostics.search_sources()
        invalid = skipped = 0
        # Keep disk occurrences even when two identical events arrive in the same millisecond.
        # Use the memory snapshot to fill in only the occurrences missing from disk.
        def encode(record):
            return json.dumps(record, sort_keys=True)

        memory_remaining = Counter(map(encode, recent))
        recent_times = {record["timestamp"] for record in recent}
        read_failure = record_failure = None

        def failed_read(path, exc):
            nonlocal skipped, read_failure
            skipped += 1
            read_failure = read_failure or (path, exc)

        def invalid_record(path, exc):
            nonlocal invalid, record_failure
            invalid += 1
            record_failure = record_failure or (path, exc)

        seen_files = set()
        roots = dict.fromkeys((directory, *previous_directories, self.bootstrap_directory))
        for path in _paths(directory, roots, cancelled, failed_read):
            with closing(_events(path, seen_files, cancelled, invalid_record)) as records:
                for time, record in _read_events(path, records, failed_read):
                    collect(time, record)
                    if record["timestamp"] in recent_times:
                        identity = encode(record)
                        if memory_remaining[identity] > 0:
                            memory_remaining[identity] -= 1

        for record in recent:
            if cancelled():
                break
            identity = encode(record)
            if memory_remaining[identity] > 0:
                collect(_event_time(record), record)
                memory_remaining[identity] -= 1

        if not cancelled():
            for failure, code, message, count in (
                (read_failure, "DIAGNOSTIC_LOG_READ_FAILED", "Could not read all retained logs. Check folder access and refresh.", skipped),
                (record_failure, "DIAGNOSTIC_RECORD_INVALID", "Skipped invalid records in retained diagnostic logs.", invalid),
            ):
                if failure is not None:
                    path, cause = failure
                    self.diagnostics.emit(code, message, level="WARNING", component="diagnostics",
                                          exc_info=cause, log_path=path, occurrence_count=count, **context)

        return DiagnosticRead(directory, skipped, invalid, storage_warning or self.diagnostics.failure)


def _read_events(path, records, failed_read):
    # Consumer write errors must propagate separately from problems reading logs.
    try:
        yield from records
    except OSError as exc:
        failed_read(path, exc)
