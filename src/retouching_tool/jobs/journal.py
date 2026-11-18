"""Versioned SQLite checkpoints for local jobs. One runner owns the workspace."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from ..errors import RetouchingError
from ..storage.batch_paths import current_file_stamp, output_path

MAX_ATTEMPTS = 3
TERMINAL = ("completed", "completed_with_errors", "canceled")
SCHEMA_VERSION = 1
STAGED_SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE job (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    snapshot TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','running','paused','completed','completed_with_errors','canceled'))
) STRICT;
CREATE TABLE image (
    position INTEGER PRIMARY KEY,
    image_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('queued','running','prepared','completed','skipped','failed','canceled')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    warnings INTEGER NOT NULL DEFAULT 0 CHECK (warnings >= 0),
    code TEXT,
    message TEXT,
    receipt TEXT,
    CHECK (status NOT IN ('prepared', 'completed') OR receipt IS NOT NULL)
) STRICT;
"""

STAGED_SCHEMA = """
ALTER TABLE image ADD COLUMN next_stage INTEGER NOT NULL DEFAULT 0 CHECK (next_stage >= 0);
ALTER TABLE image ADD COLUMN unit_attempts INTEGER NOT NULL DEFAULT 0 CHECK (unit_attempts >= 0);
ALTER TABLE image ADD COLUMN artifact_replays INTEGER NOT NULL DEFAULT 0 CHECK (artifact_replays >= 0);
ALTER TABLE image ADD COLUMN store_id TEXT;
ALTER TABLE image ADD COLUMN artifact TEXT;
CREATE UNIQUE INDEX image_store ON image(store_id) WHERE store_id IS NOT NULL;
"""


class JournalError(RetouchingError):
    def __init__(self, message):
        super().__init__("JOB_JOURNAL_FAILED", message)


def _jobs_folder(root: Path, *, create=False) -> Path:
    root = root.resolve(strict=True)
    relative = Path(".state/jobs/job")
    folder = root
    for name in relative.parts[:-1]:
        folder /= name
        if create:
            folder.mkdir(exist_ok=True)
        else:
            folder.lstat()
        # Validate existing components before creating descendants.
        output_path(root, folder.relative_to(root) / "job", allow_existing=True)
    return folder


@contextmanager
def runner_lock(root):
    import msvcrt

    directory = _jobs_folder(root, create=True)
    lock = directory / "runner.lock"
    try:
        with lock.open("xb"):
            pass
    except FileExistsError:
        current_file_stamp(lock)
    with lock.open("r+b") as stream:
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RetouchingError("JOB_RUNNER_BUSY", "Another batch or AI preview is processing. Retry after it stops.") from exc
        # Closing the handle also releases the region on worker termination.
        yield


def validate_job_id(job_id):
    if not isinstance(job_id, str) or re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
        raise JournalError("Invalid job ID. Select a job saved by Auto ReTouch.")


def saved_job_files(root):
    try:
        directory = _jobs_folder(root)
    except FileNotFoundError:
        return ()
    return tuple(path for path in directory.iterdir()
                 if re.fullmatch(r"[0-9a-f]{32}\.sqlite3", path.name))


@contextmanager
def open_journal(root, job_id, *, create=False):
    validate_job_id(job_id)
    connection = None
    try:
        try:
            path = _jobs_folder(root, create=create) / (job_id + ".sqlite3")
            if create:
                with path.open("xb"):
                    pass
            current_file_stamp(path)
            connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous = FULL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if not create and version not in (SCHEMA_VERSION, STAGED_SCHEMA_VERSION):
                raise JournalError("Job uses an incomplete or unsupported SQLite schema. Retain it for inspection.")
        except (OSError, sqlite3.Error) as exc:
            raise JournalError(f"Cannot open local job {job_id}. Check job storage and permissions.") from exc
        yield JobJournal(connection, version)
    finally:
        if connection is not None:
            connection.close()


class JobJournal:
    def __init__(self, connection, version=SCHEMA_VERSION):
        self.connection = connection
        self.version = version

    def initialize(self, snapshot, image_ids, *, skipped_ids=frozenset()):
        encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, default=os.fspath)
        staged = snapshot["mode"] == "staged"
        version = STAGED_SCHEMA_VERSION if staged else SCHEMA_VERSION
        try:
            # Schema and input rows become visible together with the version marker.
            self.connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + (STAGED_SCHEMA if staged else ""))
            with self.connection:
                self.connection.execute("INSERT INTO job VALUES (1, ?, ?, 'queued')",
                                        (encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()))
                self.connection.executemany("INSERT INTO image(position, image_id, status) VALUES (?, ?, ?)",
                                            ((i, key, "skipped" if key in skipped_ids else "queued")
                                             for i, key in enumerate(image_ids)))
                self.connection.execute(f"PRAGMA user_version = {version}")
        except sqlite3.Error as exc:
            raise JournalError("Cannot commit the initial job snapshot. Check job storage and free space.") from exc
        self.version = version

    def snapshot(self):
        row = self._one("SELECT snapshot, sha256 FROM job WHERE id = 1")
        if hashlib.sha256(row["snapshot"].encode("utf-8")).hexdigest() != row["sha256"]:
            raise JournalError("Saved job snapshot failed its checksum. Retain the job database for inspection.")
        return json.loads(row["snapshot"])

    def status(self):
        return self._one("SELECT status FROM job WHERE id = 1")["status"]

    def _one(self, sql):
        rows = self._read(sql)
        if not rows:
            raise JournalError("Job snapshot is missing from its journal.")
        return rows[0]

    def images(self):
        return self._read("SELECT * FROM image ORDER BY position")

    def accounting(self):
        rows = self._read("SELECT job.status AS job_status, image.* FROM job, image WHERE job.id = 1 ORDER BY position")
        if not rows:
            raise JournalError("Job progress is missing from its journal.")
        return rows[0]["job_status"], rows

    def _read(self, sql):
        try:
            return self.connection.execute(sql).fetchall()
        except sqlite3.Error as exc:
            raise JournalError("Cannot read the job journal. Check the database and its storage.") from exc

    def _write(self, sql, parameters=(), *, single=False):
        try:
            with self.connection:
                result = self.connection.execute(sql, parameters)
                if single and result.rowcount != 1:
                    raise JournalError("Job journal is inconsistent with the current image state.")
        except sqlite3.Error as exc:
            raise JournalError("Cannot record job progress. Check job storage and free space before resuming.") from exc

    def set_status(self, status):
        self._write("UPDATE job SET status = ? WHERE id = 1", (status,), single=True)

    def begin(self, image_id):
        self._write("""UPDATE image SET status = 'running', attempts = attempts + 1, warnings = 0, receipt = NULL
                       WHERE image_id = ? AND status IN ('queued', 'running', 'prepared') AND attempts < ?""",
                    (image_id, MAX_ATTEMPTS), single=True)

    def begin_unit(self, image_id, stage):
        self._write("""UPDATE image SET status = 'running', attempts = attempts + 1,
                       unit_attempts = unit_attempts + 1, receipt = NULL
                       WHERE image_id = ? AND next_stage = ? AND unit_attempts < ?
                       AND status IN ('queued','running','prepared')""", (image_id, stage, MAX_ATTEMPTS), single=True)

    def attach_store(self, image_id, store_id):
        self._write("UPDATE image SET store_id = ? WHERE image_id = ? AND store_id IS NULL",
                    (store_id, image_id), single=True)

    def commit_stage(self, image_id, stage, artifact, warnings):
        self._write("""UPDATE image SET next_stage = next_stage + 1, unit_attempts = 0, artifact = ?, warnings = ?
                       WHERE image_id = ? AND next_stage = ? AND status = 'running' AND store_id IS NOT NULL""",
                    (json.dumps(artifact), warnings, image_id, stage), single=True)

    def rewind(self, image_id):
        self._write("""UPDATE image SET status = 'running', next_stage = 0, unit_attempts = 0, warnings = warnings + 1,
                       artifact_replays = artifact_replays + 1, artifact = NULL, receipt = NULL
                       WHERE image_id = ? AND status IN ('running','prepared') AND artifact_replays < ?""",
                    (image_id, MAX_ATTEMPTS - 1), single=True)

    def clear_store(self, image_id, store_id):
        self._write("""UPDATE image SET artifact = NULL, store_id = NULL WHERE image_id = ? AND store_id = ?
                       AND (next_stage = 0 OR status IN ('completed','failed','canceled'))""",
                    (image_id, store_id), single=True)

    def prepared(self, image_id, receipt, warnings):
        self._write("UPDATE image SET status = 'prepared', receipt = ?, warnings = ? WHERE image_id = ? AND status = 'running'",
                    (json.dumps(receipt), warnings, image_id), single=True)

    def complete(self, image_id):
        self._write("UPDATE image SET status = 'completed' WHERE image_id = ? AND status = 'prepared'", (image_id,), single=True)

    def fail(self, image_id, code, message, warnings):
        self._write("""UPDATE image SET status = 'failed', code = ?, message = ?, warnings = ?
                       WHERE image_id = ? AND status IN ('queued','running','prepared')""",
                    (code, message[:4000], warnings, image_id), single=True)

    def cancel_remaining(self):
        try:
            with self.connection:
                self.connection.execute("UPDATE image SET status = 'canceled' WHERE status IN ('queued','running','prepared')")
                self.connection.execute("UPDATE job SET status = 'canceled' WHERE id = 1")
        except sqlite3.Error as exc:
            raise JournalError("Cannot record cancellation. Check job storage before resuming.") from exc
