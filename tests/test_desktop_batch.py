"""Drive the desktop batch commands against generated JPEGs and real workers."""

from dataclasses import replace
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, JpegImagePlugin
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QInputDialog, QLabel,
                               QMessageBox, QPushButton)

from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.operations.reference import EXPOSURE, Exposure
from retouching_tool.pipeline.definition import Parameter
from retouching_tool.ui.qt.diagnostics_dialog import DiagnosticsDialog
from retouching_tool.ui.qt.main_window import MainWindow
from retouching_tool.ui.qt.settings_dialog import SettingsDialog

from test_settings import add_fixture_stages
from test_worker import CHECKPOINT_WORKER


ROOT = Path(__file__).resolve().parents[1]


class DesktopBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        if not QFontDatabase.families():
            font = Path(os.environ["WINDIR"]) / "Fonts" / "segoeui.ttf"
            if QFontDatabase.addApplicationFont(str(font)) < 0:
                raise RuntimeError(f"Cannot load test font: {font}")
            cls.qt.setFont(QFont("Segoe UI", 10))

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
        self.addCleanup(self.dispose_window, window)
        return window, window.batch_panel

    def close_window(self, window):
        window.close()
        self.until(lambda: not window.isVisible(), "window did not close")

    def dispose_window(self, window):
        panel = window.batch_panel
        if panel._stage_preview is not None:
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
                panel._stage_preview.close()
                self.until(lambda: panel._stage_preview is None, "stage preview did not close")
        self.close_window(window)
        window.deleteLater()
        self.qt.sendPostedEvents(window, QEvent.Type.DeferredDelete)

    def until(self, predicate, message):
        deadline = monotonic() + 15
        while not predicate():
            self.qt.processEvents()
            self.assertLess(monotonic(), deadline, message)
            sleep(0.01)

    def wait_for_scan(self, panel, expected):
        self.until(lambda: panel.images.count() == expected and panel.scan.isEnabled(),
                   "automatic source scan did not finish")

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

    def test_settings_save_cancel_card_recovery_and_disk_recheck(self):
        window, panel = self.open_window((self.source,))
        app = panel.application
        self.wait_for_scan(panel, 1)
        panel.destination.setText(str(self.destination))
        self.check_space(panel)
        plan = panel._plan
        config = app.config
        add_fixture_stages(app.pipeline_service)
        stage_id = app.pipeline_service.status.stages[0].stage.id

        def open_settings():
            window.findChild(QPushButton, "openSettings").click()
            dialog = self.qt.activeModalWidget()
            self.assertIsInstance(dialog, SettingsDialog)
            return dialog

        dialog = open_settings()
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertIn(str(config.file), labels)
        self.assertIn("directml", labels)
        self.assertIn(f"In use: {config.temporary}", labels)
        dialog.fields["temporary"].setText("discard")
        dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        self.assertFalse(config.file.exists())
        self.assertIs(panel._plan, plan)

        dialog = open_settings()
        self.assertEqual(dialog.fields["temporary"].text(), config.folders.temporary)
        dialog.fields["temporary"].setText("logs")
        save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.click()
        self.until(lambda: "CONFIG_INVALID" in dialog.message.text(), "invalid folders were accepted")
        self.assertFalse(config.file.exists())
        dialog.fields["temporary"].setText(str(self.source))
        dialog.fields["checkpoints"].setText("empty models")
        release = Event()
        save_settings = app.save_settings

        def delayed_save(*args):
            if not release.wait(5):
                raise TimeoutError("Settings test did not release the save")
            return save_settings(*args)

        with patch.object(app, "save_settings", delayed_save):
            save.click()
            try:
                dialog.reject()
                window.close()
                self.assertTrue(dialog.isVisible() and window.isVisible())
            finally:
                release.set()
        self.until(lambda: "SCRATCH_INSIDE_SOURCE" in panel.setup_message.text(),
                   "edited scratch path was not rechecked")
        self.assertFalse(panel.start.isEnabled())
        self.assertEqual(load_config(self.root), app.config)
        card = window.pipeline_page.stage_cards[stage_id]
        self.assertIn("MODEL_MISSING", card.findChild(QLabel, "stageStatus").text())
        self.assertIn("empty models", window.pipeline_page.details.toPlainText())
        self.assertIn("blocked", panel.pipeline_summary.text())

        dialog = open_settings()
        dialog.fields["temporary"].setText("new scratch")
        dialog.fields["checkpoints"].setText(config.folders.checkpoints)
        dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
        self.until(lambda: panel._plan is not None and panel._plan.scratch == self.root / "new scratch",
                   "new scratch volume was not checked")
        recovered, consumer = app.pipeline_service.status.stages
        self.assertEqual((recovered.state, consumer.state), ("skipped", "blocked"))
        self.assertFalse(recovered.stage.enabled)
        self.assertFalse(panel.start.isEnabled())
        self.assertTrue(card.toggle.isEnabled())
        card.toggle.click()
        self.until(panel.start.isEnabled, "explicit enable did not restore the batch")

    def test_settings_while_staged_worker_active_and_resume(self):
        window, panel = self.open_window(tuple(self.originals), pipeline=ROOT / "examples/pipelines/reference.json")
        self.wait_for_scan(panel, 2)
        panel.destination.setText(str(self.destination))
        panel.mode.setCurrentIndex(panel.mode.findData("staged"))
        self.check_space(panel)
        self.start_at_checkpoint(panel, "pause", panel.start)
        window.findChild(QPushButton, "openSettings").click()
        dialog = self.qt.activeModalWidget()
        self.assertIsInstance(dialog, SettingsDialog)
        dialog.fields["temporary"].setText("future scratch")
        dialog.fields["logs"].setText("future logs")
        dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
        self.until(lambda: self.qt.activeModalWidget() is None, "settings did not save during the batch")
        panel.pause.click()
        self.until(panel.resume.isEnabled, "worker did not pause")
        panel.resume.click()
        self.until(lambda: "2 exported" in panel.job_counts.text(), "could not resume with the frozen paths")
        self.assertFalse((self.root / "future scratch").exists())
        records = [json.loads(line) for line in self.log.path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(event.get("worker_run_id") and event.get("job_id") == panel._job_id for event in records))

    def test_sequential_folder_preflight_and_jpeg_export(self):
        corrupt = self.source / "broken.jpg"
        corrupt.write_bytes(b"not a JPEG")
        window, panel = self.open_window()
        try:
            with patch.object(QFileDialog, "getExistingDirectory", return_value=str(self.source)):
                panel.findChild(QPushButton, "chooseSourceFolder").click()
            self.wait_for_scan(panel, 2)
            panel.recursive.setChecked(True)
            self.assertEqual(panel.images.count(), 0)
            self.wait_for_scan(panel, 3)
            self.assertIn("1 unreadable", panel.source_summary.text())

            panel.destination.setText(str(self.nested))
            self.check_space(panel, ready=False)
            self.assertIn("DESTINATION_INSIDE_SOURCE", panel.setup_message.text())
            panel.destination.setText(str(self.destination))
            self.check_space(panel, ready=False)
            self.assertIn("BATCH_INDEX_INCOMPLETE", panel.setup_message.text())
            row = next(row for row in range(3) if panel.images.item(row).text() == corrupt.name)
            panel.images.item(row).setCheckState(Qt.CheckState.Unchecked)
            self.assertIn("1 excluded", panel.source_summary.text())
            self.assertIn("Estimated temporary space", panel.space.text())
            self.check_space(panel)
            self.assertIn("MiB", panel.space.text())
            self.assertIn("required", panel.space.text())
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

    def test_diagnostics_search_copy_and_workspace_links(self):
        with Image.new("RGB", (40, 30), (240, 160, 80)) as image:
            image.save(self.source / "bright.jpg")
        window, panel = self.open_window((self.source,))
        self.wait_for_scan(panel, 2)
        editor = panel.application.pipeline_service
        stage_id = editor.add_operation("core.exposure", 1).stages[-1].stage.id
        editor.set_parameter(stage_id, "stops", 3)
        window.pipeline_page._refresh_cards()
        panel.refresh_pipeline()
        panel.destination.setText(str(self.destination))
        self.check_space(panel)
        panel.start.click()
        self.until(lambda: panel.history.isEnabled() and panel.results.rowCount() == 2,
                   "finished job did not become available")
        job_id = panel._status.job_id
        image_id = next(image.image_id for image in panel._status.images if image.source.name == "bright.jpg")

        def open_diagnostics(button):
            button.click()
            dialog = next(dialog for dialog in window.findChildren(DiagnosticsDialog) if dialog.isVisible())
            self.until(lambda: dialog.pending is None, "diagnostic search did not finish")
            return dialog

        dialog = open_diagnostics(panel.job_diagnostics)
        self.assertEqual(dialog.job_id.text(), job_id)
        self.assertEqual(dialog.severity.currentData(), "WARNING")
        self.assertTrue(all(event.job_id == job_id for event in dialog.events))
        dialog.text.setText("clipped channel samples")
        dialog.stage_id.setText(stage_id)
        dialog.image_id.setText(image_id)
        dialog.findChild(QPushButton, "refreshDiagnostics").click()
        self.until(lambda: dialog.pending is None, "filtered diagnostics did not finish")
        self.assertEqual([event.code for event in dialog.events], ["EXPOSURE_CLIPPED"])
        self.assertIn("clipped_samples", dialog.details.toPlainText())
        self.assertIn("worker_run_id", dialog.details.toPlainText())
        dialog.copy.click()
        self.assertEqual(self.qt.clipboard().text(), dialog.details.toPlainText())

        report = self.root / "job diagnostics.zip"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(report), "ZIP archive (*.zip)")) as save:
            dialog.export.click()
            self.assertIn("job diagnostics", save.call_args.args[1])
        self.until(lambda: dialog.pending is None, "job diagnostics export did not finish")
        self.assertIn("Saved", dialog.feedback.text())
        self.assertIn("README.txt", dialog.feedback.text())
        self.assertEqual([event.code for event in dialog.events], ["EXPOSURE_CLIPPED"])
        with ZipFile(report) as archive:
            events = [json.loads(line) for line in archive.read("events.jsonl").splitlines()]
            self.assertTrue(all(event["job_id"] == job_id for event in events))
            self.assertIn("EXPOSURE_CLIPPED", {event["code"] for event in events})
            self.assertIn("JOB_CREATED", {event["code"] for event in events})
            self.assertNotIn(b"bright.jpg", archive.read("settings.json"))
            self.assertNotIn(b"bright.jpg", archive.read("events.jsonl"))

        with patch.object(QDesktopServices, "openUrl", return_value=True) as opener:
            dialog.open_folder.click()
            self.assertEqual(Path(opener.call_args.args[0].toLocalFile()), self.log.path.parent)
        with patch.object(QDesktopServices, "openUrl", return_value=False):
            dialog.open_folder.click()
            self.assertIn("Check the Logs folder", dialog.feedback.text())
            self.assertEqual(self.log.records[-1]["code"], "DIAGNOSTIC_FOLDER_OPEN_FAILED")
            self.assertEqual(self.log.records[-1]["job_id"], job_id)
        dialog.photo.click()
        self.assertFalse(dialog.isVisible())
        self.assertEqual(panel.images.currentItem().text(), "bright.jpg")

        # A fresh index assigns new image IDs. The same event still links to its saved job row.
        panel.scan.click()
        self.wait_for_scan(panel, 2)
        dialog = open_diagnostics(panel.job_diagnostics)
        row = next(row for row, event in enumerate(dialog.events) if event.code == "EXPOSURE_CLIPPED")
        dialog.table.selectRow(row)
        dialog.photo.click()
        self.assertEqual(Path(panel.results.item(panel.results.currentRow(), 0).text()).name, "bright.jpg")

        dialog = open_diagnostics(window.findChild(QPushButton, "openDiagnostics"))
        self.assertEqual(dialog.job_id.text(), "")
        row = next(row for row, event in enumerate(dialog.events) if event.code == "EXPOSURE_CLIPPED")
        dialog.table.selectRow(row)
        with patch.object(window.pipeline_page.scroll, "ensureWidgetVisible") as reveal:
            dialog.operation.click()
            reveal.assert_called_once_with(window.pipeline_page.stage_cards[stage_id])
        self.assertFalse(dialog.isVisible())

    def test_staged_pause_reopen_resume_and_cancel(self):
        pipeline = ROOT / "examples/pipelines/reference.json"
        window, panel = self.open_window(tuple(self.originals), pipeline=pipeline)
        panel.application.pipeline_service.set_enabled("exposure-reference", True)
        panel.refresh_pipeline()
        self.assertEqual(panel.images.count(), 0)
        self.assertFalse(panel.application.job_service.saved_jobs())
        self.wait_for_scan(panel, 2)
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
        self.wait_for_scan(panel, 2)
        panel.job_details.click()
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
        self.wait_for_scan(panel, 2)
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
        self.wait_for_scan(panel, 2)
        panel.job_details.click()
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
        self.wait_for_scan(panel, 2)
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
        self.wait_for_scan(panel, 2)
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

    def test_thumbnail_workspace_previews_and_live_estimates(self):
        window, panel = self.open_window(tuple(self.originals), pipeline=ROOT / "examples/pipelines/reference.json")
        self.wait_for_scan(panel, 2)
        self.assertTrue(window.pipeline_page.isVisible())
        self.assertTrue(panel.images.isVisible())
        self.assertTrue(panel.destination.isVisible())
        self.assertTrue(panel.export.isVisible())
        self.assertFalse(panel.destination.text())
        self.assertTrue(panel.preview.isEnabled())
        self.assertIn("Estimated temporary space", panel.space.text())
        self.assertIn("MiB", panel.space.text())
        initial = panel.space.text()
        panel.mode.setCurrentIndex(panel.mode.findData("staged"))
        staged = panel.space.text()
        self.assertNotEqual(initial, staged)
        panel.images.item(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertNotEqual(staged, panel.space.text())
        panel.images.setCurrentRow(1)
        item = panel.images.currentItem()
        self.until(lambda: item.icon().cacheKey() != panel.images._placeholder.cacheKey(), "thumbnail not shown")
        self.assertIn("фото.jpg", item.toolTip())

        service = panel.application.preview_service
        picture = service.picture
        release = Event()

        def delayed(*args, **kwargs):
            if not release.wait(10):
                raise TimeoutError("Preview test did not release its request")
            return picture(*args, **kwargs)

        with patch.object(service, "picture", delayed):
            panel.original.click()
            panel.images.setCurrentRow(0)
            release.set()
            self.until(panel.original.isEnabled, "obsolete original request did not finish")
            self.assertIsNone(panel._picture_window)

        panel.original.click()
        self.until(lambda: panel._picture_window is not None, "original window did not open")
        original = panel._picture_window
        self.assertTrue(original.isWindow())
        self.assertTrue(original.windowTitle().startswith("Original"))
        self.assertIsNotNone(original.picture.pixmap())
        self.assertFalse(original.original_toggle.isVisible())
        panel.destination.setText(str(self.source))
        with patch.object(QFileDialog, "getExistingDirectory") as choose:
            panel.preview.click()
            choose.assert_not_called()
        self.until(lambda: panel._picture_window is not None and panel._picture_window is not original,
                   "whole pipeline window did not open")
        whole = panel._picture_window
        self.assertTrue(whole.original_toggle.isVisible())
        self.assertIsNotNone(whole.findChild(QLabel, "previewStatistics"))
        self.assertFalse(whole.picture.pixmap().isNull())
        whole.close()
        self.assertIsNone(panel._picture_window)
        self.assertIsNone(whole.original)
        self.assertIsNone(whole.result)
        self.assertTrue(whole.picture.pixmap().isNull())
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(panel.application.job_service.saved_jobs())

    def test_modal_stage_comparison_uses_selection_and_saves_or_discards_draft(self):
        window, panel = self.open_window(tuple(self.originals), pipeline=ROOT / "examples/pipelines/reference.json")
        self.wait_for_scan(panel, 2)
        exposure = window.pipeline_page.stage_cards["exposure-reference"]
        editor = panel.application.pipeline_service
        data = editor.status.definition.to_json()
        data["stages"].insert(0, dict(data["stages"][1], id="upstream", enabled=True, parameters={"stops": 1}))
        window.pipeline_page._command(editor.load_data, data)
        self.qt.processEvents()
        self.assertFalse(exposure.toggle.isChecked())
        panel.images.clearSelection()
        self.assertFalse(exposure.preview.isEnabled())
        self.assertFalse(exposure.preview_pipeline.isEnabled())
        thumbnail = panel.images.item(1)
        thumbnail.setCheckState(Qt.CheckState.Unchecked)
        panel.images.scrollToItem(thumbnail)
        QTest.mouseClick(panel.images.viewport(), Qt.MouseButton.LeftButton,
                         pos=panel.images.visualItemRect(thumbnail).center())
        self.assertEqual(panel.images.selectedItems(), [thumbnail])
        self.assertFalse(panel.destination.text())
        self.assertTrue(exposure.preview.isEnabled())
        self.assertTrue(exposure.preview_pipeline.isEnabled())
        persisted = editor.autosave.read_bytes()

        def red(picture):
            rendered = picture.pixmap()
            return rendered.toImage().pixelColor(0, 0).red() if not rendered.isNull() else 0

        preview_service = panel.application.preview_service
        start_comparison = preview_service.stage_comparison

        def capture_comparison(*args, **kwargs):
            comparisons.append(start_comparison(*args, **kwargs))
            return comparisons[-1]

        comparisons = []
        with (patch.object(QFileDialog, "getExistingDirectory") as choose,
              patch.object(preview_service, "stage_comparison", side_effect=capture_comparison)):
            exposure.preview.click()
            choose.assert_not_called()
        self.assertFalse(panel.destination.text())
        operation = panel._stage_preview
        self.assertIs(self.qt.activeModalWidget(), operation)
        self.assertEqual(operation.windowModality(), Qt.WindowModality.WindowModal)
        self.assertIn("фото.jpg", operation.windowTitle())
        self.until(lambda: red(operation.after) > 0, "stage preview did not render")
        self.assertIsNotNone(comparisons[0]._baseline)
        source = red(operation.before)
        self.assertEqual(source, red(operation.after))
        operation.findChild(QDoubleSpinBox, "parameter-stops").setValue(1)
        self.until(lambda: red(operation.after) > source, "exposure did not refresh automatically")
        self.assertEqual(editor.status.definition.stages[2].parameter("stops"), 0)
        self.assertEqual(editor.autosave.read_bytes(), persisted)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as prompt:
            self.assertFalse(operation.close())
            prompt.assert_called_once()
            self.assertTrue(operation.isVisible())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            operation.close_button.click()
        self.assertIsNone(panel._stage_preview)
        self.assertIsNone(comparisons[0]._baseline)
        self.assertTrue(operation.before.pixmap().isNull())
        self.assertTrue(operation.after.pixmap().isNull())
        self.assertEqual(editor.autosave.read_bytes(), persisted)

        with patch.object(QFileDialog, "getExistingDirectory") as choose:
            exposure.preview_pipeline.click()
            choose.assert_not_called()
        cumulative = panel._stage_preview
        self.until(lambda: red(cumulative.after) > 0, "stage prefix did not render")
        baseline = red(cumulative.before)
        self.assertGreater(baseline, source)
        self.assertEqual(baseline, red(cumulative.after))
        cumulative.findChild(QDoubleSpinBox, "parameter-stops").setValue(1)
        self.until(lambda: red(cumulative.after) > baseline, "stage prefix did not refresh")
        self.assertEqual(red(cumulative.before), baseline)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save):
            with patch("retouching_tool.application.pipeline_service.atomic_json", side_effect=OSError("fixture write failure")):
                cumulative.close_button.click()
            self.assertTrue(cumulative.isVisible())
            self.assertIn("PIPELINE_WRITE_FAILED", cumulative.feedback.text())
            self.assertEqual(editor.autosave.read_bytes(), persisted)
            failure = next(event for event in self.log.records if event["code"] == "PIPELINE_COMMAND_FAILED")
            self.assertEqual(failure["stage_instance_id"], "exposure-reference")
            self.assertEqual(failure["image_id"], panel._index.images[1].image_id)
            self.assertIn("fixture write failure", failure["exception"])
            cumulative.close_button.click()
        self.assertIsNone(panel._stage_preview)
        self.assertFalse(exposure.toggle.isChecked())
        self.assertEqual(exposure.parameters["stops"].value(), 1)
        payload = json.loads(editor.autosave.read_bytes())
        self.assertEqual(payload["stages"][2]["parameters"], {"stops": 1})
        self.assertEqual(payload["stages"][0]["parameters"], {"stops": 1})
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(panel.application.job_service.saved_jobs())

    def test_stage_preview_applies_manual_settings_explicitly_and_drops_stale_results(self):
        window, panel = self.open_window((self.source,))
        self.wait_for_scan(panel, 1)
        panel.destination.setText(str(self.destination))
        editor = panel.application.pipeline_service
        descriptor = replace(EXPOSURE, operation="test.preview_settings",
                             parameters=EXPOSURE.parameters + (Parameter("manual", "Manual value", 0, 0, 3),))

        def combined_exposure(stage):
            stops = stage.parameter("stops") + stage.parameter("manual")
            return Exposure(replace(stage, parameters=(("stops", stops),)))

        editor.registry.register(descriptor, combined_exposure)
        stage = window.pipeline_page._command(editor.add_operation, descriptor.operation, 1).stages[0].stage
        self.qt.processEvents()
        service = panel.application.preview_service
        picture = service.picture
        started, release = Event(), Event()
        requests = []

        def render(*args, **kwargs):
            parameters = kwargs["parameters"]
            requests.append(parameters)
            if parameters["stops"] == 1:
                started.set()
                release.wait(10)
            return picture(*args, **kwargs)

        with patch.object(service, "picture", side_effect=render):
            window.pipeline_page.stage_cards[stage.id].preview.click()
            dialog = panel._stage_preview
            self.until(lambda: not dialog.after.pixmap().isNull(), "initial preview did not render")
            with patch.object(dialog.after, "show_pixels", wraps=dialog.after.show_pixels) as paint:
                dialog.findChild(QDoubleSpinBox, "parameter-manual").setValue(2)
                self.assertTrue(dialog.apply_preview.isEnabled())
                live = dialog.findChild(QDoubleSpinBox, "parameter-stops")
                live.setValue(1)
                try:
                    self.until(started.is_set, "first live preview did not start")
                    self.assertEqual(requests, [{"stops": 0, "manual": 0}, {"stops": 1, "manual": 0}])
                    live.setValue(2)
                finally:
                    release.set()
                self.until(lambda: len(requests) == 3 and not dialog.feedback.text(), "latest live preview did not render")
                self.assertEqual(requests[-1], {"stops": 2, "manual": 0})
                paint.assert_called_once()
            live_red = dialog.after.pixmap().toImage().pixelColor(0, 0).red()
            dialog.apply_preview.click()
            self.until(lambda: len(requests) == 4 and not dialog.feedback.text(), "manual preview did not render")
            self.assertEqual(requests[-1], {"stops": 2, "manual": 2})
            self.assertGreater(dialog.after.pixmap().toImage().pixelColor(0, 0).red(), live_red)
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
                dialog.close()
