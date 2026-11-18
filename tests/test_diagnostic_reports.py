"""Exercise report scope, redaction and local publication without Qt."""

from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError

ROOT = Path(__file__).resolve().parents[1]


class DiagnosticReportTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".test-work"
        work.mkdir(exist_ok=True)
        temporary = TemporaryDirectory(dir=work, prefix="diagnostic report ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.log = Diagnostics(self.root / "logs", console=StringIO())
        self.addCleanup(self.log.close)
        self.app = ApplicationService(self.root, load_config(self.root), self.log)
        self.app.startup(LaunchOptions((), None, None), "directml")
        self.report = self.root / "report.zip"

    def read_report(self, result):
        with ZipFile(result.path) as archive:
            self.assertCountEqual(archive.namelist(), result.members)
            self.assertIsNone(archive.testzip())
            readme = archive.read("README.txt").decode("utf-8")
            settings = json.loads(archive.read("settings.json"))
            events = [json.loads(line) for line in archive.read("events.jsonl").splitlines()]
        self.assertEqual(result.event_count, len(events))
        return readme, settings, events

    def test_job_report_uses_frozen_settings_and_redacts_photos_paths_and_stderr(self):
        source = self.root / "Private Studio"
        source.mkdir()
        photo = source / "Портрет O'Neil.jpg"
        with Image.new("RGB", (16, 12)) as image:
            image.save(photo)
        destination = self.root / "exports"
        destination.mkdir()
        models = self.app.config.checkpoints
        models.mkdir()
        checkpoint = models / "fixture.onnx"
        checkpoint.write_bytes(b"DO NOT ARCHIVE CHECKPOINT BYTES")
        index = self.app.index_sources((source,))
        stage = self.app.pipeline_service.add_operation("core.exposure", 1).stages[0].stage
        self.app.pipeline_service.set_parameter(stage.id, "stops", 1)
        job_id = self.app.job_service.create(index, destination)
        self.app.pipeline_service.set_parameter(stage.id, "stops", 2)
        image_id = index.images[0].image_id
        stderr = f'Driver fault reading "{photo.as_posix().upper()}"\nOperator {Path.home().name} on {photo.name}'
        try:
            raise OSError(f"Cannot read {photo}")
        except OSError:
            self.log.emit("WORKER_STDERR", "Worker failed", level="ERROR", exc_info=True, job_id=job_id,
                          source_path=photo, image_id=image_id, stage_instance_id=stage.id, stderr_tail=stderr)
        self.log.emit("UNRELATED", "Different job", job_id="other")
        self.assertIn(str(photo), self.log.records[-2]["exception"])
        photo.unlink()

        readme, settings, events = self.read_report(self.app.export_diagnostics(self.report, job_id=job_id))
        self.assertEqual(settings["pipeline"]["stages"][0]["parameters"]["stops"], 1)
        self.assertEqual(settings["counts"], {"queued": 1})
        self.assertEqual(settings["backend"], "directml")
        self.assertEqual(settings["images"][0]["image_id"], image_id)
        self.assertNotIn("No captured worker stderr", readme)
        self.assertTrue(all(event["job_id"] == job_id for event in events))
        failure = next(event for event in events if event["code"] == "WORKER_STDERR")
        alias = failure["source_path"]
        self.assertEqual(settings["images"][0]["source_path"], alias)
        self.assertIn(alias, failure["exception"])
        self.assertIn(alias, failure["stderr_tail"])
        self.assertIn("OSError", failure["exception"])
        self.assertEqual(failure["stage_instance_id"], stage.id)
        rendered = json.dumps([settings, events], ensure_ascii=False)
        for secret in (photo.name, "Private Studio", Path.home().name, "DO NOT ARCHIVE CHECKPOINT BYTES"):
            self.assertNotIn(secret.casefold(), rendered.casefold())
        self.assertEqual(checkpoint.read_bytes(), b"DO NOT ARCHIVE CHECKPOINT BYTES")

    def test_session_report_includes_related_rotated_records_and_marks_gaps(self):
        prior = Diagnostics(self.root / "logs", console=StringIO())
        self.addCleanup(prior.close)
        prior.emit("DIFFERENT_SESSION", "Exclude this record")
        self.log.emit("RELATED_SETUP", "Include the parent session linkage",
                      parent_session_id=self.log.session_id)
        record = dict(self.log.records[-1], session_id=prior.session_id, code="ROTATED_SETUP")
        old = prior.path.with_suffix(".jsonl.1")
        old.write_text(json.dumps(record) + "\ninvalid record\n", encoding="utf-8")
        path = r"D:\Unindexed Client, Studio\Extra Photo.jpeg"
        network_path = r"\\PrivateShare\Portrait Team\Additional Photo.jpg"
        self.log.emit("GENERIC_PATH", f'Missing "{path}"', source_path=path,
                      url="https://example.org/versions", source_paths=(network_path,),
                      technical_cause=rf"Cannot read '\\?\UNC{network_path[1:]}'")
        readme, settings, events = self.read_report(self.app.export_diagnostics(self.report))
        self.assertIn("1 invalid records", readme)
        self.assertIn("Rotated, expired or undelivered", readme)
        self.assertTrue(settings["config_path"].startswith("<path-"))
        self.assertEqual({event["code"] for event in events},
                         {"STARTUP_STATE_READY", "RELATED_SETUP", "ROTATED_SETUP", "GENERIC_PATH"})
        redacted = next(event for event in events if event["code"] == "GENERIC_PATH")
        self.assertEqual(redacted["message"], f'Missing "{redacted["source_path"]}"')
        self.assertEqual(redacted["url"], "https://example.org/versions")
        self.assertIn(redacted["source_paths"][0], redacted["technical_cause"])
        self.assertNotIn("Unindexed Client", json.dumps(redacted))

    def test_unavailable_job_keeps_events_and_reports_missing_settings(self):
        job_id = "0" * 32
        self.log.emit("WORKER_EXITED", "Prior interruption", level="ERROR", job_id=job_id)
        readme, settings, events = self.read_report(self.app.export_diagnostics(self.report, job_id=job_id))
        self.assertIn("Frozen settings and results are missing", readme)
        self.assertIn("Worker diagnostics may be incomplete", readme)
        self.assertNotIn("pipeline", settings)
        failure = next(event for event in events if event["code"] == "DIAGNOSTIC_REPORT_JOB_UNAVAILABLE")
        self.assertIn("Traceback", failure["exception"])
        self.assertTrue(any(event["code"] == "WORKER_EXITED" for event in events))

    def test_report_marks_events_omitted_after_reaching_the_size_limit(self):
        self.log.emit("LARGE_EVENT", "Simulated bulk diagnostic", technical_cause="detail" * 1000)
        with patch("retouching_tool.diagnostics.reports.MAX_LOG_BYTES", 2048):
            readme, _, events = self.read_report(self.app.export_diagnostics(self.report))
        self.assertIn("size limit omitted 1 matching records", readme)
        self.assertEqual([event["code"] for event in events], ["STARTUP_STATE_READY"])

    def test_failure_and_cancel_leave_no_partial_report_and_keep_existing_files(self):
        with patch("retouching_tool.diagnostics.reports.ZipFile.writestr", side_effect=OSError("Simulated disk full")):
            with self.assertRaisesRegex(RetouchingError, "Check folder access"):
                self.app.export_diagnostics(self.report)
        self.assertFalse(self.report.exists())
        self.assertEqual(list(self.root.glob(".auto-retouch-report-*")), [])
        self.assertEqual(self.log.records[-1]["code"], "DIAGNOSTIC_REPORT_FAILED")
        self.assertIn("Simulated disk full", self.log.records[-1]["exception"])
        self.assertEqual(self.log.records[-1]["report_path"], str(self.report))

        self.report.write_bytes(b"existing contents")
        with self.assertRaises(RetouchingError) as error:
            self.app.export_diagnostics(self.report)
        self.assertEqual(error.exception.code, "DIAGNOSTIC_REPORT_EXISTS")
        self.assertEqual(self.report.read_bytes(), b"existing contents")

        other = self.root / "canceled.zip"
        cancelled = lambda: any(self.root.glob(".auto-retouch-report-*"))
        self.assertIsNone(self.app.export_diagnostics(other, cancelled=cancelled))
        self.assertFalse(other.exists())
        self.assertEqual(list(self.root.glob(".auto-retouch-report-*")), [])
