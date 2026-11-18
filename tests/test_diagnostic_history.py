"""Verify local diagnostic search against retained and memory-only records."""

from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from retouching_tool.config import LoggingSettings
from retouching_tool.diagnostics.history import DiagnosticHistory, DiagnosticQuery, RESULT_LIMIT, _open_log
from retouching_tool.diagnostics.service import Diagnostics, MAX_EVENT_BYTES

ROOT = Path(__file__).resolve().parents[1]


class DiagnosticHistoryTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".test-work"
        work.mkdir(exist_ok=True)
        temporary = TemporaryDirectory(dir=work, prefix="diagnostics ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = LoggingSettings("INFO", 1, 4, 7)
        self.log = Diagnostics(self.root / "original", self.settings, console=StringIO())
        self.addCleanup(self.log.close)
        self.history = DiagnosticHistory(self.log, self.root / "logs")

    def test_search_rotation_relocation_and_bootstrap_with_correlated_traceback(self):
        bootstrap = Diagnostics(self.root / "logs", session_id=self.log.session_id, console=StringIO())
        self.addCleanup(bootstrap.close)
        bootstrap.emit("BOOTSTRAP_EVENT", "Started with the fallback log", level="WARNING")
        bootstrap.close()
        source = self.root / "Вход" / "ПОРТРЕТ.jpg"
        try:
            raise OSError(f"Simulated bad read: {source}")
        except OSError:
            self.log.emit("PORTRAIT_BAD_READ", "Could not read the JPEG", level="ERROR", exc_info=True,
                          job_id="job-1", image_id="photo-1", stage_instance_id="stage-1", source_path=source)
        self.log.emit("PORTRAIT_INFO", "Routine image event", job_id="job-1", image_id="photo-1")
        old_path = self.log.path
        self.log._size = self.settings.max_file_mb * 1_000_000  # Force rotation without a megabyte fixture.
        with _open_log(old_path) as reader:
            self.log.emit("ROTATED_EVENT", "Wrote to the new active file", level="WARNING")
            self.assertIn(b"PORTRAIT_BAD_READ", reader.read())
        self.assertTrue(old_path.with_suffix(".jsonl.1").exists())
        self.assertIsNone(self.log.failure)
        new_directory = self.root / "new"
        with self.log.changing_settings(new_directory, self.settings):
            pass
        self.log.emit("OTHER_JOB", "Could not read the JPEG", level="ERROR", job_id="job-2",
                      image_id="photo-1", stage_instance_id="stage-1", source_path=source)

        query = DiagnosticQuery(str(source).lower(), "WARNING", "job-1", "photo-1", "stage-1")
        result = self.history.search(query)
        self.assertEqual(result.directory, new_directory)
        self.assertEqual([event.code for event in result.events], ["PORTRAIT_BAD_READ"])
        details = result.events[0].details
        self.assertIn("Traceback", details)
        self.assertIn(f"OSError: Simulated bad read: {source}", details)
        self.assertNotIn("PORTRAIT_INFO", [event.code for event in self.history.search(DiagnosticQuery(
            minimum_severity="WARNING", job_id="job-1")).events])
        self.assertEqual({event.code for event in self.history.search(DiagnosticQuery()).events},
                         {"BOOTSTRAP_EVENT", "PORTRAIT_BAD_READ", "PORTRAIT_INFO", "ROTATED_EVENT", "OTHER_JOB"})

    def test_invalid_records_are_skipped_and_read_failure_keeps_memory(self):
        self.log.emit("AVAILABLE_IN_MEMORY", "The process still has this event", level="WARNING", job_id="job-1")
        record = dict(self.log.records[-1], code="AFTER_BAD_RECORD")
        bad = self.log.path.with_suffix(".jsonl.5")
        bad.write_bytes(b"invalid UTF-8 \xff\n" + b"z" * (MAX_EVENT_BYTES * 2) + b"\n"
                        + json.dumps(record).encode("utf-8") + b"\n" + b"{\"unfinished\":")
        (bad.parent / "unrelated.jsonl").write_bytes(b"\xff\n")
        result = self.history.search(DiagnosticQuery(job_id="job-1"))
        self.assertEqual(result.invalid_records, 3)
        self.assertEqual({event.code for event in result.events}, {"AFTER_BAD_RECORD", "AVAILABLE_IN_MEMORY"})
        reported = self.log.records[-1]
        self.assertEqual(reported["code"], "DIAGNOSTIC_RECORD_INVALID")
        self.assertEqual(reported["job_id"], "job-1")
        self.assertEqual(reported["log_path"], str(bad))
        self.assertIn("UnicodeDecodeError", reported["exception"])

        inaccessible = self.root / "log-path-is-a-file"
        inaccessible.write_text("fixture", encoding="utf-8")
        fallback = Diagnostics(inaccessible, console=StringIO())
        self.addCleanup(fallback.close)
        fallback.emit("MEMORY_ONLY", "Still visible despite the storage failure", level="ERROR")
        result = DiagnosticHistory(fallback, self.root / "absent").search(DiagnosticQuery())
        self.assertEqual([event.code for event in result.events], ["MEMORY_ONLY"])
        self.assertIn("LOG_WRITE_FAILED", result.storage_warning)
        self.assertEqual(result.skipped_files, 1)
        self.assertEqual(fallback.records[-1]["code"], "DIAGNOSTIC_LOG_READ_FAILED")
        self.assertIn("Traceback", fallback.records[-1]["exception"])

    def test_newest_matches_are_bounded_and_identical_disk_occurrences_preserved(self):
        self.log.emit("NEWEST", "A repeated event")
        event = self.log.records[-1]
        self.log.receive(event.copy(), json.dumps(event))
        backup = self.log.path.with_suffix(".jsonl.1")
        with backup.open("w", encoding="utf-8") as stream:
            for index in range(RESULT_LIMIT + 5):
                old = dict(event, timestamp="2020-01-01T00:00:00+00:00", code=f"OLDER_{index}")
                stream.write(json.dumps(old) + "\n")
        result = self.history.search(DiagnosticQuery())
        self.assertTrue(result.has_more)
        self.assertEqual(len(result.events), RESULT_LIMIT)
        self.assertEqual([event.code for event in result.events[:2]], ["NEWEST", "NEWEST"])
        self.assertFalse(any(event.code == "NEWEST" for event in result.events[2:]))
