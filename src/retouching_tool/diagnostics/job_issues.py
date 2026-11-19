"""Temporary text report for the batch currently open in the desktop."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

from ..errors import RetouchingError


class JobIssues:
    def __init__(self, job_id, stream):
        self.job_id, self.stream = job_id, stream
        self.lock = Lock()
        self.count = 0
        self.failure = None

    def record(self, event):
        if event.get("job_id") != self.job_id or event["severity"] not in ("WARNING", "ERROR", "CRITICAL"):
            return
        heading = f"{event['timestamp']}  {event['severity']}  {event['code']}\n{event['message']}\n"
        omit = {"schema_version", "job_id", "timestamp", "severity", "code", "message", "session_id", "process_id"}
        details = [f"{key}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}"
                   for key, value in event.items() if key not in omit]
        with self.lock:
            try:
                self.stream.seek(0, 2)
                self.stream.write((heading + "\n".join(details) + "\n\n").encode("utf-8", "backslashreplace"))
                self.stream.flush()
                self.count += 1
            except OSError as exc:
                self.failure = f"JOB_ISSUE_WRITE_FAILED: Some events could not be saved. {exc}"
                raise

    def export(self, path, images):
        if path.suffix.lower() != ".txt":
            raise RetouchingError("JOB_REPORT_PATH", "Choose a .txt filename for the batch report.")
        with self.lock:
            end, count, failure = self.stream.seek(0, 2), self.count, self.failure
        temporary = None
        try:
            with NamedTemporaryFile(dir=path.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(f"Auto ReTouch batch {self.job_id}\nWarnings and errors recorded so far: {count}\n".encode())
                if failure:
                    output.write((failure + "\n").encode("utf-8", "backslashreplace"))
                output.write(b"\n")
                offset = 0
                while offset < end:
                    with self.lock:
                        self.stream.seek(offset)
                        chunk = self.stream.read(min(64 * 1024, end - offset))
                    if not chunk:
                        raise OSError("The current batch report ended unexpectedly.")
                    output.write(chunk)
                    offset += len(chunk)
                if count and images:
                    output.write(b"Photo IDs in this batch\n")
                    for image in images:
                        output.write(f"{image.image_id}  {image.source}\n".encode("utf-8", "backslashreplace"))
            temporary.rename(path)  # On Windows, preserve any existing destination.
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return count
