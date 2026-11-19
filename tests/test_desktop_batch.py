"""Drive the desktop batch commands against generated JPEGs and real workers."""

from dataclasses import replace
from io import StringIO
import json
import os
from pathlib import Path
from random import Random
import tempfile
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, JpegImagePlugin
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QInputDialog, QLabel,
                               QMessageBox, QProgressBar, QPushButton, QSlider, QToolButton)

from retouching_tool.application.service import ApplicationService
from retouching_tool.application.batch import BASE_SCRATCH_BYTES
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

    def double_click_thumbnail(self, panel):
        grid = panel.images
        position = grid.visualItemRect(grid.currentItem()).center()
        QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=position)
        QTest.mouseDClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=position)

    def check_comparison_views(self, window, focus_widget):
        view = window.comparison
        self.assertEqual(view.mode.currentText(), "Side by side")
        self.assertTrue(view.before.isVisible() and view.after.isVisible())
        split_width = view.before.width()
        focus_widget.setFocus()
        QTest.keyClick(focus_widget, Qt.Key.Key_Space)
        self.qt.processEvents()
        self.assertTrue(view.before.isVisible())
        self.assertFalse(view.after.isVisible())
        self.assertGreater(view.before.width(), split_width)
        QTest.keyClick(focus_widget, Qt.Key.Key_Space)
        self.qt.processEvents()
        self.assertFalse(view.before.isVisible())
        self.assertTrue(view.after.isVisible())
        self.assertGreater(view.after.width(), split_width)
        view.mode.setCurrentIndex(0)
        self.assertTrue(view.before.isVisible() and view.after.isVisible())

    def wait_for_space(self, panel, *, ready=True):
        self.until(lambda: panel._space_check_after is None and panel._space_check is None, "disk check did not return")
        self.assertEqual(panel.start.isEnabled(), ready, panel.space.text())

    def choose_existing(self, label, path):
        execute = QMessageBox.exec

        def respond(dialog):
            self.assertIn(str(path), dialog.detailedText())
            self.assertEqual(dialog.defaultButton().text(), "Stop processing")
            self.assertIs(dialog.escapeButton(), dialog.defaultButton())
            button = next(button for button in dialog.buttons() if button.text() == label)
            QTimer.singleShot(0, button.click)
            return execute(dialog)

        return patch.object(QMessageBox, "exec", respond)

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
        self.wait_for_space(panel)
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
        dialog.fields["pipelines"].setText("discard presets")
        dialog.scaling["downscale"].setValue("nearest")
        dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        self.assertFalse(config.file.exists())
        self.assertIs(panel._plan, plan)

        dialog = open_settings()
        self.assertEqual(dialog.fields["temporary"].text(), config.folders.temporary)
        self.assertEqual(dialog.fields["pipelines"].text(), "pipelines")
        preset_folder = self.root / "studio presets"
        preset_folder.mkdir()
        (preset_folder / "Studio.json").write_text(json.dumps(app.pipeline_service.status.definition.to_json()))
        dialog.fields["pipelines"].setText(preset_folder.name)
        self.assertEqual(dialog.scaling["downscale"].value(), "bilinear")
        dialog.scaling["upscale"].setValue("nearest")
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
        self.until(lambda: "SCRATCH_INSIDE_SOURCE" in panel.space.text(),
                   "edited scratch path was not rechecked")
        self.assertFalse(panel.start.isEnabled())
        self.assertEqual(load_config(self.root), app.config)
        presets = window.pipeline_page.presets
        self.assertEqual([presets.itemText(i) for i in range(presets.count())], ["Studio"])
        self.assertEqual(presets.currentIndex(), -1)
        self.assertEqual(app.config.scaling.upscale, "nearest")
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

    def test_processing_modal_pause_edit_settings_and_continue_saved_job(self):
        window, panel = self.open_window(tuple(self.originals), pipeline=ROOT / "examples/pipelines/reference.json")
        self.wait_for_scan(panel, 2)
        panel.destination.setText(str(self.destination))
        panel.mode.setCurrentIndex(panel.mode.findData("staged"))
        self.wait_for_space(panel)
        self.start_at_checkpoint(panel, "pause", panel.start)
        self.assertIs(self.qt.activeModalWidget(), panel.monitor)
        self.assertFalse(panel.monitor.close_button.isEnabled())
        self.assertFalse(panel.resume.isEnabled())
        panel.pause.click()
        self.until(panel.resume.isEnabled, "worker did not pause")
        self.assertTrue(panel.cancel.isEnabled())
        panel.monitor.close_button.click()
        window.findChild(QPushButton, "openSettings").click()
        dialog = self.qt.activeModalWidget()
        self.assertIsInstance(dialog, SettingsDialog)
        dialog.fields["temporary"].setText("future scratch")
        dialog.fields["logs"].setText("future logs")
        dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
        self.until(lambda: self.qt.activeModalWidget() is None, "settings did not save")
        with patch.object(QInputDialog, "getItem", side_effect=lambda *args, **kwargs: (args[3][0], True)):
            panel.history.click()
            self.until(panel.monitor.isVisible, "saved processing dialog did not reopen")
        panel.resume.click()
        self.until(lambda: "2 exported" in panel.job_counts.text(), "could not resume with the frozen paths")
        self.assertFalse((self.root / "future scratch").exists())
        records = [json.loads(line) for line in self.log.path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(event.get("worker_run_id") and event.get("job_id") == panel._job_id for event in records))

    def test_bulk_photo_checks_preserve_preview_selection_and_recheck_space_once(self):
        _, panel = self.open_window(tuple(self.originals))
        self.assertFalse(panel.check_all.isEnabled() or panel.uncheck_all.isEnabled())
        self.wait_for_scan(panel, 2)
        panel.destination.setText(str(self.destination))
        self.wait_for_space(panel)
        panel.images.setCurrentRow(1)
        current = panel.images.currentItem()
        self.assertFalse(panel.check_all.isEnabled())
        self.assertTrue(panel.uncheck_all.isEnabled())
        with patch.object(panel.application.batch_service, "estimate", wraps=panel.application.batch_service.estimate) as estimate:
            panel.uncheck_all.click()
            estimate.assert_called_once()
            self.assertIn("0 included", panel.source_summary.text())
            self.assertFalse(panel.start.isEnabled() or panel.uncheck_all.isEnabled())
            self.assertEqual(panel.space.text(), "")
            panel.images.item(0).setCheckState(Qt.CheckState.Checked)
            self.assertTrue(panel.check_all.isEnabled() and panel.uncheck_all.isEnabled())
            estimate.reset_mock()
            panel.check_all.click()
            estimate.assert_called_once()
        self.wait_for_space(panel)
        self.assertIn("2 included", panel.source_summary.text())
        self.assertIs(panel.images.currentItem(), current)
        self.assertFalse(panel.check_all.isEnabled())

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
            self.wait_for_space(panel, ready=False)
            self.assertIn("DESTINATION_INSIDE_SOURCE", panel.space.text())
            panel.destination.setText(str(self.destination))
            self.wait_for_space(panel, ready=False)
            self.assertIn("BATCH_INDEX_INCOMPLETE", panel.space.text())
            row = next(row for row in range(3) if panel.images.item(row).text() == corrupt.name)
            panel.images.item(row).setCheckState(Qt.CheckState.Unchecked)
            self.assertIn("1 excluded", panel.source_summary.text())
            self.assertIn("Estimated temporary space", panel.space.text())
            self.wait_for_space(panel)
            self.assertIn("MiB", panel.space.text())
            self.assertIn("required", panel.space.text())
            collision = self.destination / "a.jpg"
            collision.write_bytes(b"occupied after preflight")
            with self.choose_existing("Stop processing", collision):
                panel.start.click()
                self.until(lambda: panel.scan.isEnabled(), "job creation did not return")
            self.assertIn("stopped", panel.setup_message.text())
            self.assertTrue(panel.start.isEnabled())
            self.assertFalse(panel.application.job_service.saved_jobs())
            self.assertEqual(collision.read_bytes(), b"occupied after preflight")
            collision.unlink()
            with patch.object(QFileDialog, "getExistingDirectory", return_value=str(self.destination)):
                panel.findChild(QPushButton, "chooseDestination").click()
            self.wait_for_space(panel)
            panel.quality.setValue(81)
            panel.chroma.setCurrentText("4:2:0")
            panel.progressive.setChecked(True)
            panel.start.click()
            self.until(lambda: panel.history.isEnabled() and panel.results.rowCount() == 2,
                       "finished job did not become available")
            self.assertIn("2 exported", panel.job_counts.text())
            self.assertIn("JPEG 81, 4:2:0, progressive", panel.job_metadata.text())
            self.assertEqual(panel.job_progress.value(), 2)
            self.assertRegex(panel.job_timing.text(), r"Elapsed: \d+:\d\d:\d\d .*Estimated remaining: 0:00:00$")
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

    def test_format_selection_rechecks_destination_and_starts_staged_png_worker(self):
        _, panel = self.open_window((self.source,))
        self.wait_for_scan(panel, 1)
        panel.destination.setText(str(self.destination))
        self.wait_for_space(panel)
        panel.format.setCurrentText("JPEG XL")
        self.assertTrue(panel.jxl_effort.isVisible())
        panel.jxl_lossless.setChecked(True)
        self.assertFalse(panel.jxl_quality.isEnabled())
        panel.format.setCurrentText("TIFF")
        self.assertTrue(panel.tiff_compression.isVisible())
        self.assertFalse(panel.jxl_effort.isVisible())
        panel.format.setCurrentText("BMP")
        self.assertTrue(panel.bmp_dpi.isVisible())
        self.assertTrue(any("EXIF" in label.text() and label.isVisible() for label in panel.export.findChildren(QLabel)))
        collision = self.destination / "a.png"
        collision.touch()
        panel.format.setCurrentText("PNG")
        self.assertFalse(panel.start.isEnabled())
        self.wait_for_space(panel)
        self.assertNotIn("OUTPUT_COLLISION", panel.space.text())
        panel.mode.setCurrentIndex(1)
        panel.png_compression.setValue(2)
        self.wait_for_space(panel)
        with self.choose_existing("Overwrite existing files", collision):
            panel.start.click()
            self.until(lambda: panel.history.isEnabled() and panel.results.rowCount() == 1, "PNG worker did not finish")
        self.assertIn("1 exported", panel.job_counts.text())
        self.assertIn("PNG lossless, compression 2", panel.job_metadata.text())
        with Image.open(collision) as output:
            self.assertEqual(output.format, "PNG")
            self.assertEqual(output.getexif()[271], "Desktop Test Camera")

    def test_skip_existing_files_runs_only_the_remaining_photos(self):
        _, panel = self.open_window((self.source,))
        self.wait_for_scan(panel, 1)
        panel.recursive.setChecked(True)
        self.wait_for_scan(panel, 2)
        collision = self.destination / "a.jpg"
        collision.write_bytes(b"previous export")
        remaining = panel._index.images[1]
        free = BASE_SCRATCH_BYTES + remaining.working_bytes + 2 * remaining.stamp.size
        with patch("retouching_tool.application.batch.shutil.disk_usage", return_value=SimpleNamespace(free=free)):
            panel.destination.setText(str(self.destination))
            self.wait_for_space(panel)
            self.assertFalse(panel._plan.ready)
            with self.choose_existing("Skip existing files", collision):
                panel.start.click()
                self.until(lambda: panel.history.isEnabled() and "1 exported" in panel.job_counts.text(),
                           "remaining photo was not exported")
        self.assertIn("1 skipped", panel.job_counts.text())
        self.assertEqual(collision.read_bytes(), b"previous export")
        self.assertEqual(panel.results.item(0, 2).text(), "0")
        self.assertEqual(panel.job_progress.value(), panel.job_progress.maximum())
        self.assertTrue((self.destination / "nested" / "фото.jpg").is_file())

    def test_destination_picker_starts_in_source_until_destination_is_chosen(self):
        _, panel = self.open_window()
        browse = panel.findChild(QPushButton, "chooseDestination")
        with patch.object(QFileDialog, "getExistingDirectory", return_value="") as picker:
            browse.click()
            picker.assert_called_once_with(panel, "Choose destination folder", "")

        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(self.source)):
            panel.findChild(QPushButton, "chooseSourceFolder").click()
        self.wait_for_scan(panel, 1)
        with patch.object(QFileDialog, "getExistingDirectory", return_value="") as picker:
            browse.click()
            picker.assert_called_once_with(panel, "Choose destination folder", str(self.source))
            self.assertEqual(panel.destination.text(), "")

        files = [str(self.nested / "фото.jpg"), str(self.source / "a.jpg")]
        with patch.object(QFileDialog, "getOpenFileNames", return_value=(files, "")):
            panel.findChild(QPushButton, "chooseSourceFiles").click()
        self.wait_for_scan(panel, 2)
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(self.destination)) as picker:
            browse.click()
            picker.assert_called_once_with(panel, "Choose destination folder", str(self.nested))
            browse.click()
            picker.assert_called_with(panel, "Choose destination folder", str(self.destination))

    def test_auto_preflight_after_folder_selection_rescan_and_destination_edit(self):
        _, panel = self.open_window()
        self.assertIsNone(panel.findChild(QPushButton, "checkBatchSpace"))
        self.assertEqual(panel.start.text(), "Start processing")
        self.assertEqual(panel.scan.text(), "Rescan source folder")
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(self.destination)):
            panel.findChild(QPushButton, "chooseDestination").click()
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(self.source)):
            panel.findChild(QPushButton, "chooseSourceFolder").click()
        self.until(panel.start.isEnabled, "selecting the source did not validate disk space")

        collision = self.destination / "a.jpg"
        collision.touch()
        panel.scan.click()
        self.assertFalse(panel.start.isEnabled())
        self.until(panel.start.isEnabled, "existing files blocked the rescan")
        collision.unlink()

        service = panel.application.batch_service
        preflight = service.preflight
        started, release = Event(), Event()

        def delayed(index, destination, **options):
            if destination == self.destination:
                started.set()
                if not release.wait(5):
                    raise TimeoutError("Disk check test did not release the request")
            return preflight(index, destination, **options)

        with patch.object(service, "preflight", delayed):
            panel.scan.click()
            self.until(started.is_set, "new rescan did not check the destination")
            try:
                panel.destination.setText(str(self.source))
            finally:
                release.set()
            self.wait_for_space(panel, ready=False)
            self.assertIsNone(panel._plan)
            self.assertNotIn("required ·", panel.space.text())
        panel.destination.setText(str(self.destination))
        self.until(panel.start.isEnabled, "valid destination did not restore start")

    def test_confirmed_break_of_paused_job(self):
        _, panel = self.open_window(tuple(self.originals), pipeline=ROOT / "examples/pipelines/reference.json")
        self.wait_for_scan(panel, 2)
        panel.destination.setText(str(self.destination))
        panel.mode.setCurrentIndex(panel.mode.findData("staged"))
        self.wait_for_space(panel)
        self.start_at_checkpoint(panel, "pause", panel.start)
        panel.pause.click()
        self.until(panel.resume.isEnabled, "Continue did not become available")
        self.assertIs(self.qt.activeModalWidget(), panel.monitor)
        self.assertEqual(panel.resume.text(), "Continue")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            panel.cancel.click()
        self.assertTrue(panel.resume.isEnabled())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            panel.cancel.click()
        self.until(lambda: panel.job_heading.text().startswith("Canceled"), "paused job was not canceled")
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(panel.resume.isEnabled())
        panel.monitor.close_button.click()
        self.assertIsNone(self.qt.activeModalWidget())

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
        self.wait_for_space(panel)
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
        with patch.object(QInputDialog, "getItem", side_effect=lambda *args, **kwargs: (args[3][0], True)):
            panel.history.click()
            self.until(panel.monitor.isVisible, "saved processing dialog did not reopen")
        dialog = open_diagnostics(panel.job_diagnostics)
        row = next(row for row, event in enumerate(dialog.events) if event.code == "EXPOSURE_CLIPPED")
        dialog.table.selectRow(row)
        dialog.photo.click()
        self.assertEqual(Path(panel.results.item(panel.results.currentRow(), 0).text()).name, "bright.jpg")
        panel.monitor.close_button.click()

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
        self.wait_for_space(panel)
        panel.mode.setCurrentIndex(panel.mode.findData("staged"))
        self.assertFalse(panel.start.isEnabled())
        self.wait_for_space(panel)
        worker = panel.application.job_worker
        launch = worker.start
        with patch("retouching_tool.ui.qt.batch_panel.monotonic", return_value=100.0) as clock:
            def start(job_id):
                run = launch(job_id)
                clock.return_value = 112.0
                return run

            with patch.object(worker, "start", start):
                self.start_at_checkpoint(panel, "pause", panel.start)
            self.until(lambda: panel.job_progress.value() == 1, "stage progress did not reach the monitor")
            # Three enabled stages plus export across two photos, one unit committed.
            self.assertIn("Elapsed: 0:00:12", panel.job_timing.text())
            self.assertIn("Estimated remaining: 0:01:24", panel.job_timing.text())
            clock.return_value += 1
            self.until(lambda: "0:01:23" in panel.job_timing.text(), "ETA did not count down between checkpoints")
            panel.pause.click()
            self.until(lambda: panel.resume.isEnabled(), "paused job did not become resumable")
            clock.return_value += 3600
            panel._tick()
            self.assertIn("Elapsed: 0:00:13", panel.job_timing.text())
            self.assertIn("Estimated remaining: —", panel.job_timing.text())
        job_id = panel.application.job_service.saved_jobs()[0].job_id
        self.assertTrue(panel.job_heading.text().startswith("Paused"))
        self.assertGreater(panel.job_progress.value(), 0)
        self.assertLess(panel.job_progress.value(), panel.job_progress.maximum())
        self.close_window(window)

        window, panel = self.open_window()
        self.assertIn(str(self.nested / "фото.jpg"), panel.source_text.text())
        self.assertTrue(panel.job_heading.text().startswith("No job selected"))
        self.wait_for_scan(panel, 2)
        with patch.object(QInputDialog, "getItem", side_effect=lambda *args, **kwargs: (args[3][0], True)):
            panel.history.click()
            self.until(lambda: panel.resume.isEnabled(), "saved job did not reopen")
        self.assertIn(job_id, panel.job_heading.text())
        self.assertIn("Staged", panel.job_metadata.text())
        self.assertIn("Elapsed: —", panel.job_timing.text())
        self.start_at_checkpoint(panel, "cancel", panel.resume)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as confirm:
            QTest.keyClick(panel.monitor, Qt.Key.Key_Escape)
            confirm.assert_called_once()
            self.assertEqual(confirm.call_args.args[-1], QMessageBox.StandardButton.No)
        self.assertTrue(panel.monitor.isVisible())
        self.assertTrue(panel.pause.isEnabled())
        self.assertIsNone(panel._request_sent)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            panel.cancel.click()
        self.until(lambda: panel.history.isEnabled(), "canceled job did not finish")
        self.assertTrue(panel.job_heading.text().startswith("Canceled"))
        self.assertIn("2 canceled", panel.job_counts.text())
        self.assertIn("Estimated remaining: —", panel.job_timing.text())
        self.assertFalse(panel.resume.isEnabled())
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_close_pauses_then_reopened_job_finishes(self):
        window, panel = self.open_window(tuple(self.originals), pipeline=ROOT / "examples/pipelines/reference.json")
        self.wait_for_scan(panel, 2)
        panel.destination.setText(str(self.destination))
        panel.mode.setCurrentIndex(panel.mode.findData("staged"))
        self.wait_for_space(panel)
        self.start_at_checkpoint(panel, "pause", panel.start)
        jobs = panel.application.job_service
        job_id = jobs.saved_jobs()[0].job_id
        self.close_window(window)
        self.assertEqual(jobs.status(job_id).status, "paused")
        self.assertEqual(list(self.destination.iterdir()), [])

        _, panel = self.open_window()
        self.wait_for_scan(panel, 2)
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
        self.wait_for_space(panel)
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
        self.wait_for_space(panel)
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
        self.assertIsNone(panel.findChild(QPushButton, "viewOriginal"))
        self.assertIsNone(panel.findChild(QPushButton, "previewPipeline"))
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
            self.double_click_thumbnail(panel)
            self.assertIsNotNone(panel._task)
            panel.images.setCurrentRow(0)
            release.set()
            self.until(lambda: panel._task is None, "obsolete original request did not finish")
            self.assertIsNone(panel._picture_window)

        QTest.keyClick(panel.images, Qt.Key.Key_Return)
        self.until(lambda: panel._picture_window is not None, "original window did not open")
        original = panel._picture_window
        self.assertTrue(original.isWindow())
        self.assertIs(self.qt.activeModalWidget(), original)
        self.assertTrue(original.windowTitle().startswith("Original"))
        view = original.comparison
        self.assertFalse(view.before.pixmap().isNull())
        self.assertFalse(view.controls.isVisible())
        self.assertFalse(view.after.isVisible())
        opened_filename = panel.images.currentItem().text()
        panel.images.setCurrentRow(1)
        with patch.object(QFileDialog, "getExistingDirectory") as choose:
            original.run_pipeline.click()
            self.assertIs(self.qt.activeModalWidget(), original.render_dialog)
            self.assertIs(original.render_dialog.parentWidget(), original)
            bar = original.render_dialog.findChild(QProgressBar)
            self.assertEqual((bar.minimum(), bar.maximum()), (0, 0))
            self.assertFalse(original.run_pipeline.isEnabled())
            self.until(view.controls.isVisible, "pipeline result did not render")
            choose.assert_not_called()
        self.assertIs(self.qt.activeModalWidget(), original)
        self.assertTrue(original.windowTitle().endswith(opened_filename))
        self.assertTrue(original.statistics.isVisible())
        self.assertFalse(view.after.pixmap().isNull())
        self.check_comparison_views(original, original.run_pipeline)
        release.clear()
        with patch.object(service, "picture", delayed):
            original.run_pipeline.click()
            self.assertIs(self.qt.activeModalWidget(), original.render_dialog)
            original.render_dialog.close_preview.click()
            self.assertIsNone(self.qt.activeModalWidget())
            self.assertTrue(view.before.pixmap().isNull())
            self.assertTrue(view.after.pixmap().isNull())
            release.set()
            self.until(lambda: panel._task is None, "closed preview did not finish")
        self.assertIsNone(self.qt.activeModalWidget())
        self.assertIsNone(panel._picture_window)
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(panel.application.job_service.saved_jobs())

    def test_preview_zoom_pan_and_space_preserve_displayed_detail(self):
        path = self.source / "detail.jpg"
        gradient = Image.linear_gradient("L")
        Image.merge("RGB", (gradient.rotate(90), gradient, gradient)).resize((1200, 800)).save(path)
        window, panel = self.open_window((path,), pipeline=ROOT / "examples/pipelines/reference.json")
        self.wait_for_scan(panel, 1)
        self.double_click_thumbnail(panel)
        self.until(lambda: panel._picture_window is not None, "source did not open")
        photo = panel._picture_window
        view = photo.comparison

        def samples(pane, *offsets):
            self.qt.processEvents()
            image = pane.grab().toImage()
            middle = image.rect().center()
            return [image.pixelColor(middle + offset * image.devicePixelRatio()).getRgb()[:2] for offset in offsets]

        def check_samples(actual, expected):
            for measured, reference in zip(actual, expected, strict=True):
                for value, target in zip(measured, reference, strict=True):
                    self.assertAlmostEqual(value, target, delta=2)

        def wheel(pane, offset, delta):
            position = pane.rect().center() + offset
            event = QWheelEvent(position, pane.mapToGlobal(position), QPoint(), QPoint(0, delta),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.ScrollUpdate, False)
            self.qt.sendEvent(pane, event)

        origin, anchor = QPoint(), QPoint(120, 40)
        offsets = (origin, QPoint(90, 60), QPoint(-80, -40))
        at_fit = samples(view.before, *offsets)
        under_cursor = samples(view.before, anchor)
        wheel(view.before, anchor, 480)
        check_samples(samples(view.before, anchor), under_cursor)
        zoomed = samples(view.before, *offsets)
        self.assertLess(abs(zoomed[1][0] - zoomed[0][0]), abs(at_fit[1][0] - at_fit[0][0]))
        self.assertGreater(abs(zoomed[0][0] - at_fit[0][0]), 6)

        photo.run_pipeline.click()
        self.until(view.controls.isVisible, "pipeline result did not render")
        check_samples(samples(view.before, *offsets), zoomed)
        check_samples(samples(view.after, *offsets), zoomed)
        middle = view.after.rect().center()
        drag = QPoint(70, 50)
        QTest.mousePress(view.after, Qt.MouseButton.LeftButton, pos=middle)
        QTest.mouseMove(view.after, middle + drag)
        QTest.mouseRelease(view.after, Qt.MouseButton.LeftButton, pos=middle + drag)
        check_samples(samples(view.after, drag), [zoomed[0]])
        panned = samples(view.after, *offsets)
        check_samples(samples(view.before, *offsets), panned)
        QTest.keyClick(photo, Qt.Key.Key_Space)
        check_samples(samples(view.before, *offsets), panned)
        QTest.keyClick(photo, Qt.Key.Key_Space)
        check_samples(samples(view.after, *offsets), panned)
        buttons = {button.text(): button for button in view.zoom_controls.findChildren(QToolButton)}
        previous_scale = view.scale()
        buttons["+"].click()
        self.assertGreater(view.scale(), previous_scale)
        buttons["−"].click()
        check_samples(samples(view.after, *offsets), panned)
        buttons["Fit"].click()
        check_samples(samples(view.after, origin), [at_fit[0]])
        self.assertAlmostEqual(view.scale(), min(view.after.width() / 1200, view.after.height() / 800, 1))
        view.mode.setCurrentIndex(view.SIDE_BY_SIDE)
        buttons["Fit"].click()
        fitted_pair = samples(view.after, *offsets)
        QTest.keyClick(photo, Qt.Key.Key_Space)
        check_samples(samples(view.before, *offsets), fitted_pair)
        photo.close()

        window.pipeline_page.stage_cards["exposure-reference"].preview_pipeline.click()
        dialog = panel._stage_preview
        view = dialog.comparison
        self.until(lambda: not view.after.pixmap().isNull(), "stage comparison did not render")
        wheel(view.after, anchor, 480)
        stage_detail = samples(view.before, *offsets)
        dialog.findChild(QDoubleSpinBox, "parameter-stops").setValue(1)
        self.until(lambda: samples(view.after, origin)[0][0] > stage_detail[0][0] + 5, "stage did not refresh")
        check_samples(samples(view.before, *offsets), stage_detail)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            dialog.close()

    def test_preview_100_percent_shows_native_pixels_after_pan_and_space(self):
        path = self.source / "grain.jpg"
        size = (2179, 521)
        Image.frombytes("L", size, Random(37).randbytes(size[0] * size[1])).save(path, quality=95)
        with Image.open(path) as jpeg:
            source = jpeg.convert("RGB")
        window, panel = self.open_window((path,), pipeline=ROOT / "examples/pipelines/reference.json")
        self.wait_for_scan(panel, 1)
        editor = panel.application.pipeline_service
        editor.set_parameter("exposure-reference", "stops", 1)
        editor.set_enabled("exposure-reference", True)
        index = panel._index
        result = panel.application.preview_service.picture(index, index.images[0].image_id, "expected", process=True).result
        expected_result = Image.frombytes("RGB", size, result.rgb)
        self.double_click_thumbnail(panel)
        self.until(lambda: panel._picture_window is not None, "source did not open")
        photo = panel._picture_window

        def actual_size(view):
            button = next(b for b in view.findChildren(QToolButton) if b.text() == "100%")
            center = view.center
            button.click()
            self.assertEqual(view.center, center)
            self.assertAlmostEqual(view.scale() * view.devicePixelRatioF(), 1)

        def check_pixels(pane, expected):
            self.qt.processEvents()
            screen = pane.grab().toImage()
            dpr = screen.devicePixelRatio()
            x, y = int(pane.width() * dpr / 2), int(pane.height() * dpr / 2)
            center = pane.comparison.center
            u, v = int(center.x() * expected.width), int(center.y() * expected.height)
            self.assertEqual([screen.pixelColor(x + dx, y + dy).red() for dy in range(3) for dx in range(9)],
                             [expected.getpixel((u + dx, v + dy))[0] for dy in range(3) for dx in range(9)])

        view = photo.comparison
        actual_size(view)
        check_pixels(view.before, source)
        middle = view.before.rect().center()
        QTest.mousePress(view.before, Qt.MouseButton.LeftButton, pos=middle)
        QTest.mouseMove(view.before, middle + QPoint(93, 26))
        QTest.mouseRelease(view.before, Qt.MouseButton.LeftButton, pos=middle + QPoint(93, 26))
        center = view.center
        self.assertLess(center.x(), .5)
        photo.run_pipeline.click()
        self.until(view.controls.isVisible, "pipeline result did not render")
        check_pixels(view.before, source)
        check_pixels(view.after, expected_result)
        QTest.keyClick(photo, Qt.Key.Key_Space)
        check_pixels(view.before, source)
        QTest.keyClick(photo, Qt.Key.Key_Space)
        check_pixels(view.after, expected_result)
        self.assertEqual(view.center, center)
        photo.close()
        self.assertTrue(view.before._native.isNull() and view.after._native.isNull())

        card = window.pipeline_page.stage_cards["exposure-reference"]
        for action in (card.preview, card.preview_pipeline):
            action.click()
            dialog = panel._stage_preview
            view = dialog.comparison
            self.until(view.zoom_controls.isEnabled, "stage comparison did not render")
            actual_size(view)
            check_pixels(view.before, source)
            check_pixels(view.after, expected_result)
            dialog.close()

    def test_neutral_picker_saves_fixed_white_balance_and_missing_detector_card(self):
        from retouching_tool.operations.face_detection import DESCRIPTOR, MODEL

        window, panel = self.open_window(tuple(self.originals))
        self.wait_for_scan(panel, 2)
        editor = panel.application.pipeline_service
        page = window.pipeline_page
        page._command(editor.add_operation, "core.white_balance", 1)
        wb = editor.status.stages[-1].stage
        self.until(lambda: wb.id in page.stage_cards, "white balance card did not appear")
        panel.images.setCurrentRow(0)
        page.stage_cards[wb.id].preview.click()
        dialog = panel._stage_preview
        self.until(dialog.pick_neutral.isEnabled, "no white balance input")
        self.assertIn("Insufficient consistent neutrals", dialog.feedback.text())
        dialog.comparison.actual_size()
        self.qt.processEvents()
        pane = dialog.comparison.before
        dialog.pick_neutral.click()
        QTest.mouseClick(pane, Qt.MouseButton.LeftButton, pos=pane.rect().center())
        self.until(lambda: dialog._submitted["mode"] == "Fixed" and dialog._future is None,
                   "the sampled correction did not render")
        self.assertLess(dialog._draft["red_stops"], 0)
        self.assertGreater(dialog._draft["blue_stops"], 0)
        self.assertEqual(editor.status.stages[-1].stage.parameter("mode"), "Automatic")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save):
            dialog.close_button.click()
        self.assertEqual(editor.status.stages[-1].stage.parameter("mode"), "Fixed")
        # This test workspace has no model files. Verify the actual detector card and model link.
        stage = page._command(editor.add_operation, DESCRIPTOR.operation, 1, model_ref=MODEL.ref).stages[-1].stage
        self.until(lambda: stage.id in page.stage_cards, "face detector card did not appear")
        card = page.stage_cards[stage.id]
        self.assertFalse(card.toggle.isEnabled())
        self.assertIn(MODEL.files[0].relative_path, card.status_label.text())
        self.assertEqual(card.model_page, MODEL.publisher_url)

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
        self.until(lambda: red(operation.comparison.after) > 0, "stage preview did not render")
        self.assertIsNotNone(comparisons[0]._baseline)
        source = red(operation.comparison.before)
        self.assertEqual(source, red(operation.comparison.after))
        self.check_comparison_views(operation, operation.findChild(QDoubleSpinBox, "parameter-stops"))
        operation.findChild(QDoubleSpinBox, "parameter-stops").setValue(1)
        self.until(lambda: red(operation.comparison.after) > source, "exposure did not refresh automatically")
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
        self.assertTrue(operation.comparison.before.pixmap().isNull())
        self.assertTrue(operation.comparison.after.pixmap().isNull())
        self.assertEqual(editor.autosave.read_bytes(), persisted)

        with patch.object(QFileDialog, "getExistingDirectory") as choose:
            exposure.preview_pipeline.click()
            choose.assert_not_called()
        cumulative = panel._stage_preview
        self.until(lambda: red(cumulative.comparison.after) > 0, "stage prefix did not render")
        baseline = red(cumulative.comparison.before)
        self.check_comparison_views(cumulative, cumulative.close_button)
        self.assertGreater(baseline, source)
        self.assertEqual(baseline, red(cumulative.comparison.after))
        cumulative.findChild(QDoubleSpinBox, "parameter-stops").setValue(1)
        self.until(lambda: red(cumulative.comparison.after) > baseline, "stage prefix did not refresh")
        self.assertEqual(red(cumulative.comparison.before), baseline)
        full_effect = red(cumulative.comparison.after)
        cumulative.findChild(QSlider, "blendSlider").setValue(50)
        self.until(lambda: baseline < red(cumulative.comparison.after) < full_effect, "blended preview did not refresh")
        self.assertEqual(red(cumulative.comparison.before), baseline)
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
        self.assertEqual(payload["stages"][2]["parameters"], {"stops": 1, "blend": .5})
        self.assertEqual(payload["stages"][0]["parameters"], {"stops": 1})
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(panel.application.job_service.saved_jobs())

    def test_resize_consent_is_local_to_each_preview_and_job(self):
        from retouching_tool.errors import ResizeApprovalRequired

        window, panel = self.open_window((self.source,))
        self.wait_for_scan(panel, 1)
        editor = panel.application.pipeline_service
        descriptor = replace(EXPOSURE, operation="test.manual_resize",
                             parameters=(replace(EXPOSURE.parameters[0], preview_on_change=False),))
        editor.registry.register(descriptor, Exposure)
        stage = window.pipeline_page._command(editor.add_operation, descriptor.operation, 1).stages[0].stage
        self.qt.processEvents()
        preview = panel.application.preview_service
        picture = preview.picture
        approvals = []

        def render(*args, **kwargs):
            if not kwargs.get("process") and kwargs.get("stage_id") is None:
                return picture(*args, **kwargs)
            approvals.append(kwargs["resize_approvals"])
            if stage.id not in approvals[-1]:
                raise ResizeApprovalRequired(stage.id, "Approve the test resize?")
            return picture(*args, **kwargs)

        def approve_resize(parent, *args):
            self.assertIs(self.qt.activeModalWidget(), parent)
            self.assertFalse(parent.render_dialog.isVisible())
            return QMessageBox.StandardButton.Yes

        with patch.object(preview, "picture", side_effect=render), patch.object(
                QMessageBox, "question", side_effect=approve_resize):
            window.pipeline_page.stage_cards[stage.id].preview.click()
            dialog = panel._stage_preview
            self.assertIs(self.qt.activeModalWidget(), dialog.render_dialog)
            self.until(lambda: not dialog.comparison.after.pixmap().isNull(), "stage resize was not retried")
            dialog.close()
            self.double_click_thumbnail(panel)
            self.until(lambda: panel._picture_window is not None, "photo preview did not open")
            photo = panel._picture_window
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
                photo.run_pipeline.click()
                self.until(photo.run_pipeline.isEnabled, "declined resize kept the preview busy")
                self.assertIs(self.qt.activeModalWidget(), photo)
                self.assertIn("Approve the test resize?", photo.feedback.text())
                self.assertFalse(photo.comparison.after.isVisible())
            with patch.object(photo.render_dialog, "open", wraps=photo.render_dialog.open) as show_progress:
                photo.run_pipeline.click()
                self.until(lambda: photo.comparison.after.isVisible(), "pipeline resize was not retried")
                self.assertEqual(show_progress.call_count, 2)
            self.assertIs(self.qt.activeModalWidget(), photo)
            photo.close()
        self.assertEqual(approvals, [(), (stage.id,), (), (), (stage.id,)])

        panel.destination.setText(str(self.destination))
        self.wait_for_space(panel)
        with patch.object(panel.application.job_service, "create", side_effect=ResizeApprovalRequired(stage.id, "Resize?")), patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as prompt:
            panel.start.click()
            self.until(lambda: panel._task is None, "resize request did not finish")
            prompt.assert_called_once()
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
            self.assertIs(self.qt.activeModalWidget(), dialog.render_dialog)
            modal_bar = dialog.render_dialog.findChild(QProgressBar)
            self.assertEqual((modal_bar.minimum(), modal_bar.maximum()), (0, 0))
            view = dialog.comparison
            self.until(lambda: not view.after.pixmap().isNull(), "initial preview did not render")
            live = dialog.findChild(QDoubleSpinBox, "parameter-stops")

            def layout_rects():
                return [widget.geometry() for widget in
                        (view.before, view.after, live, dialog.live_progress, dialog.apply_preview, dialog.close_button)]

            idle_rects = layout_rects()
            self.assertIs(self.qt.activeModalWidget(), dialog)
            self.assertTrue(dialog.live_progress.isVisible())
            self.assertGreater(dialog.live_progress.maximum(), 0)
            with patch.object(view.after, "show_pixels", wraps=view.after.show_pixels) as paint:
                dialog.findChild(QDoubleSpinBox, "parameter-manual").setValue(2)
                self.assertTrue(dialog.apply_preview.isEnabled())
                live.setValue(1)
                try:
                    self.until(started.is_set, "first live preview did not start")
                    self.assertTrue(dialog.live_progress.isVisible())
                    self.assertEqual(dialog.live_progress.maximum(), 0)
                    self.assertIs(self.qt.activeModalWidget(), dialog)
                    self.assertEqual(layout_rects(), idle_rects)
                    self.assertEqual(requests, [{"stops": 0, "manual": 0, "blend": 1}, {"stops": 1, "manual": 0, "blend": 1}])
                    live.setValue(2)
                finally:
                    release.set()
                self.until(lambda: len(requests) == 3 and dialog._future is None, "latest live preview did not render")
                self.assertEqual(requests[-1], {"stops": 2, "manual": 0, "blend": 1})
                paint.assert_called_once()
            live_red = view.after.pixmap().toImage().pixelColor(0, 0).red()
            dialog.apply_preview.click()
            self.assertIs(self.qt.activeModalWidget(), dialog.render_dialog)
            self.assertEqual(layout_rects(), idle_rects)
            self.until(lambda: len(requests) == 4 and dialog._future is None, "manual preview did not render")
            self.assertIs(self.qt.activeModalWidget(), dialog)
            self.assertTrue(dialog.live_progress.isVisible())
            self.assertGreater(dialog.live_progress.maximum(), 0)
            self.assertEqual(layout_rects(), idle_rects)
            self.assertEqual(requests[-1], {"stops": 2, "manual": 2, "blend": 1})
            self.assertGreater(view.after.pixmap().toImage().pixelColor(0, 0).red(), live_red)
            dialog.findChild(QDoubleSpinBox, "parameter-manual").setValue(1)
            dialog.apply_preview.click()
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard) as prompt:
                dialog.render_dialog.close_preview.click()
                self.assertFalse(dialog.render_dialog.close_preview.isEnabled())
                self.assertTrue(dialog.render_dialog.isVisible())
                prompt.assert_not_called()
                self.until(lambda: panel._stage_preview is None, "stage preview did not finish closing")
                prompt.assert_called_once()
