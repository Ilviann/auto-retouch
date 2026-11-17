"""Drive the desktop batch commands against generated JPEGs and real workers."""

from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from time import monotonic, sleep
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, JpegImagePlugin
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QPushButton

from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.ui.qt.main_window import MainWindow

from test_worker import CHECKPOINT_WORKER


ROOT = Path(__file__).resolve().parents[1]


class DesktopBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        work = ROOT / ".test-work"
        work.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=work, prefix="desktop batch ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(strict=True)
        self.log = Diagnostics(self.root / "logs", console=StringIO())
        self.addCleanup(self.log.close)
        self.source = self.root / "вход"
        self.nested = self.source / "nested"
        self.nested.mkdir(parents=True)
        self.destination = self.root / "выход"
        self.destination.mkdir()
        self.originals = {}
        for path in (self.source / "a.jpg", self.nested / "фото.jpg"):
            with Image.new("RGB", (40, 30), (96, 72, 48)) as image:
                exif = Image.Exif()
                exif[271] = "Desktop Test Camera"
                image.save(path, exif=exif)
            self.originals[path] = path.read_bytes()

    def open_window(self, sources=(), *, pipeline=None):
        application = ApplicationService(self.root, load_config(self.root), self.log)
        snapshot = application.startup(LaunchOptions(sources, pipeline, None), "directml")
        window = MainWindow(snapshot, application.pipeline_service, application=application)
        window.show()
        self.addCleanup(self.close_window, window)
        return window, window.batch_panel

    def close_window(self, window):
        window.close()
        self.until(lambda: not window.isVisible(), "window did not close")

    def until(self, predicate, message):
        deadline = monotonic() + 15
        while not predicate():
            self.qt.processEvents()
            self.assertLess(monotonic(), deadline, message)
            sleep(0.01)

    def click_scan(self, panel, expected):
        panel.scan.click()
        self.until(lambda: panel.images.rowCount() == expected, panel.setup_message.text())

    def check_space(self, panel, *, ready=True):
        panel.check_space.click()
        self.until(lambda: panel.check_space.isEnabled(), "disk check did not return")
        self.assertEqual(panel.start.isEnabled(), ready, panel.setup_message.text())

    def start_at_checkpoint(self, panel, action, button):
        worker = panel.application.job_worker
        command = worker._command

        def test_command(job_id, run_id):
            args = command(job_id, run_id)
            offset = args.index("-m")
            return [*args[:offset], "-c", CHECKPOINT_WORKER, *args[offset + 2:], action]

        marker = self.root / "worker-at-checkpoint"
        with patch.object(worker, "_command", test_command):
            button.click()
            self.until(lambda: marker.exists() and panel.pause.isEnabled(), "worker did not reach a checkpoint")
        marker.unlink()

    def test_sequential_folder_preflight_and_jpeg_export(self):
        corrupt = self.source / "broken.jpg"
        corrupt.write_bytes(b"not a JPEG")
        window, panel = self.open_window()
        try:
            with patch.object(QFileDialog, "getExistingDirectory", return_value=str(self.source)):
                panel.findChild(QPushButton, "chooseSourceFolder").click()
            self.click_scan(panel, 2)
            panel.recursive.setChecked(True)
            self.assertEqual(panel.images.rowCount(), 0)
            self.click_scan(panel, 3)
            self.assertIn("1 unreadable", panel.source_summary.text())

            panel.destination.setText(str(self.nested))
            self.check_space(panel, ready=False)
            self.assertIn("DESTINATION_INSIDE_SOURCE", panel.setup_message.text())
            panel.destination.setText(str(self.destination))
            self.check_space(panel, ready=False)
            self.assertIn("BATCH_INDEX_INCOMPLETE", panel.setup_message.text())
            row = next(row for row in range(3) if panel.images.item(row, 2).text() == "Unreadable")
            panel.images.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
            self.assertIn("1 excluded", panel.source_summary.text())
            self.check_space(panel)
            self.assertIn("bytes required", panel.space.text())
            collision = self.destination / "a.jpg"
            collision.write_bytes(b"occupied after preflight")
            panel.start.click()
            self.until(lambda: panel.check_space.isEnabled(), "job creation did not return")
            self.assertIn("OUTPUT_COLLISION", panel.setup_message.text())
            self.assertFalse(panel.start.isEnabled())
            self.assertFalse(panel.application.job_service.saved_jobs())
            self.assertEqual(collision.read_bytes(), b"occupied after preflight")
            collision.unlink()
            self.check_space(panel)
            panel.quality.setValue(81)
            panel.chroma.setCurrentText("4:2:0")
            panel.progressive.setChecked(True)
            panel.start.click()
            self.until(lambda: panel.history.isEnabled() and panel.results.rowCount() == 2,
                       "finished job did not become available")
            self.assertIn("2 exported", panel.job_counts.text())
            self.assertIn("JPEG 81, 4:2:0, progressive", panel.job_metadata.text())
            self.assertEqual(panel.job_progress.value(), 2)
            self.assertFalse((self.destination / corrupt.name).exists())
            for path, original in self.originals.items():
                self.assertEqual(path.read_bytes(), original)
                with Image.open(self.destination / path.relative_to(self.source)) as image:
                    self.assertEqual(image.getexif()[271], "Desktop Test Camera")
                    self.assertEqual(JpegImagePlugin.get_sampling(image), 2)
                    self.assertTrue(image.info["progressive"])
            records = [json.loads(line) for line in self.log.path.read_text(encoding="utf-8").splitlines()]
            error = next(event for event in records if event["code"] == "DESTINATION_INSIDE_SOURCE")
            self.assertIn("index_id", error)
            self.assertIn("Traceback", error["exception"])
        finally:
            self.close_window(window)

    def test_staged_pause_reopen_resume_and_cancel(self):
        pipeline = ROOT / "examples/pipelines/reference.json"
        window, panel = self.open_window(tuple(self.originals), pipeline=pipeline)
        panel.application.pipeline_service.set_enabled("exposure-reference", True)
        panel.refresh_pipeline()
        self.assertEqual(panel.images.rowCount(), 0)
        self.assertFalse(panel.application.job_service.saved_jobs())
        self.click_scan(panel, 2)
        panel.destination.setText(str(self.destination))
        self.check_space(panel)
        panel.mode.setCurrentIndex(panel.mode.findData("staged"))
        self.assertFalse(panel.start.isEnabled())
        self.check_space(panel)
        self.start_at_checkpoint(panel, "pause", panel.start)
        job_id = panel.application.job_service.saved_jobs()[0].job_id
        panel.pause.click()
        self.until(lambda: panel.resume.isEnabled(), "paused job did not become resumable")
        self.assertTrue(panel.job_heading.text().startswith("Paused"))
        self.assertGreater(panel.job_progress.value(), 0)
        self.assertLess(panel.job_progress.value(), panel.job_progress.maximum())
        self.close_window(window)

        window, panel = self.open_window()
        self.assertIn(str(self.nested / "фото.jpg"), panel.source_text.text())
        self.assertTrue(panel.job_heading.text().startswith("No job selected"))
        with patch.object(QInputDialog, "getItem", side_effect=lambda *args, **kwargs: (args[3][0], True)):
            panel.history.click()
            self.until(lambda: panel.resume.isEnabled(), "saved job did not reopen")
        self.assertIn(job_id, panel.job_heading.text())
        self.assertIn("Staged", panel.job_metadata.text())
        self.start_at_checkpoint(panel, "cancel", panel.resume)
        panel.cancel.click()
        self.until(lambda: panel.history.isEnabled(), "canceled job did not finish")
        self.assertTrue(panel.job_heading.text().startswith("Canceled"))
        self.assertIn("2 canceled", panel.job_counts.text())
        self.assertFalse(panel.resume.isEnabled())
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_close_pauses_then_reopened_job_finishes(self):
        window, panel = self.open_window(tuple(self.originals), pipeline=ROOT / "examples/pipelines/reference.json")
        self.click_scan(panel, 2)
        panel.destination.setText(str(self.destination))
        panel.mode.setCurrentIndex(panel.mode.findData("staged"))
        self.check_space(panel)
        self.start_at_checkpoint(panel, "pause", panel.start)
        jobs = panel.application.job_service
        job_id = jobs.saved_jobs()[0].job_id
        self.close_window(window)
        self.assertEqual(jobs.status(job_id).status, "paused")
        self.assertEqual(list(self.destination.iterdir()), [])

        _, panel = self.open_window()
        with patch.object(QInputDialog, "getItem", side_effect=lambda *args, **kwargs: (args[3][0], True)):
            panel.history.click()
            self.until(lambda: panel.resume.isEnabled(), "saved paused job did not reopen")
        panel.resume.click()
        self.until(lambda: panel.history.isEnabled(), "resumed job did not finish")
        self.assertIn("2 exported", panel.job_counts.text())
        self.assertEqual(jobs.status(job_id).status, "completed")

    def test_pipeline_change_during_freeze_leaves_job_queued_for_review(self):
        pipeline = ROOT / "examples/pipelines/reference.json"
        _, panel = self.open_window(tuple(self.originals), pipeline=pipeline)
        self.click_scan(panel, 2)
        panel.destination.setText(str(self.destination))
        self.check_space(panel)
        editor = panel.application.pipeline_service
        preflight = editor.preflight

        def change_during_preflight():
            editor.set_enabled("statistics-before", False)
            return preflight()

        with patch.object(editor, "preflight", change_during_preflight):
            panel.start.click()
            self.until(lambda: panel.resume.isEnabled(), "frozen job did not become available")
        self.assertIn("availability changed", panel.job_message.text())
        self.assertIn("statistics-before (disabled)", panel.job_metadata.text())
        self.assertTrue(panel.job_heading.text().startswith("Queued"))
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_worker_exit_is_visible_and_saved_job_can_start_again(self):
        _, panel = self.open_window(tuple(self.originals))
        self.click_scan(panel, 2)
        panel.destination.setText(str(self.destination))
        self.check_space(panel)
        worker = panel.application.job_worker
        command = worker._command

        def fail_in_subprocess(job_id, run_id):
            args = command(job_id, run_id)
            return [*args[:args.index("-m")], "-c", "raise SystemExit(79)"]

        with patch.object(worker, "_command", fail_in_subprocess):
            panel.start.click()
            self.until(lambda: panel.resume.isEnabled(), "worker exit did not return control")
        self.assertIn("WORKER_EXITED", panel.worker_warning.text())
        self.assertIn("diagnostics are incomplete", panel.worker_warning.text())
        self.assertTrue(panel.job_heading.text().startswith("Queued"))
        panel.refresh.click()
        self.until(lambda: panel.resume.isEnabled(), "saved job did not refresh")
        self.assertIn("WORKER_EXITED", panel.worker_warning.text())
        panel.resume.click()
        self.until(lambda: panel.history.isEnabled(), "restarted job did not finish")
        self.assertIn("2 exported", panel.job_counts.text())
