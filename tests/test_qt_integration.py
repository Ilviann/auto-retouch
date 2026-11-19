"""A focused Widgets integration test; no processing/portrait claims."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from contextlib import ExitStack
from concurrent.futures import Future
from pathlib import Path
import hashlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QLabel, QPlainTextEdit, QSlider,
    QMessageBox, QPushButton, QSpinBox,
)
from PySide6.QtGui import QAction, QFont, QFontDatabase, QFontMetrics
from retouching_tool.application.contracts import StartupSnapshot
from retouching_tool.application.pipeline_service import PipelineService
from retouching_tool.application.preview import DisplayImage, PicturePreview
from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.operations.alibaba_skin import BLEMISH, BLEMISH_MODEL, SMOOTHING, SMOOTHING_MODEL
from retouching_tool.operations.face_detection import DESCRIPTOR as DETECTOR, MODEL as DETECTOR_MODEL
from retouching_tool.operations.exposed_skin import DESCRIPTOR as SKIN, MODEL as SKIN_MODEL
from retouching_tool.operations.skin_shine import DESCRIPTOR as SHINE
from retouching_tool.pipeline.assets import ModelCatalog, ModelFile, ModelManifest
from retouching_tool.pipeline.definition import ContextSpec, OperationDescriptor, Parameter, SCALING_PARAMETERS, StageDefinition
from retouching_tool.pipeline.registry import application_registry
from retouching_tool.ui.qt.main_window import MainWindow
from retouching_tool.ui.qt.stage_card import StageCard
from retouching_tool.ui.qt.stage_preview_window import StagePreviewWindow

ROOT = Path(__file__).resolve().parents[1]


class QtIntegrationTests(unittest.TestCase):
    def test_change_preview_modes_follow_each_stages_pixel_contract(self):
        for descriptor in application_registry().descriptors():
            with self.subTest(stage=descriptor.title):
                settings = tuple((p.name, p.default) for p in descriptor.editable_parameters)
                stage = StageDefinition("selected", descriptor.operation, descriptor.version, True, settings)
                dialog = StagePreviewWindow(None, stage, descriptor, "fixture", through_stage=False,
                                            submit=None, save=None)
                dialog.show()
                try:
                    modes = dialog.preview_mode
                    self.assertEqual(modes.isVisible(), descriptor.changes_pixels)
                    self.assertEqual(modes.currentText(), "Stage result")
                    self.assertEqual([modes.itemText(i) for i in range(modes.count())],
                                     ["Stage result", "Show affected pixels", "Pixel difference"])
                finally:
                    dialog.close()

    def test_stage_preview_mode_follows_latest_request_without_changing_settings(self):
        settings = {"strength": .5, "blend": 1.0}
        stage = StageDefinition("shine", SHINE.operation, 1, True, tuple(settings.items()))
        pending = []
        def submit(parameters, approvals, mode):
            future = Future()
            pending.append((parameters, mode, future))
            return future

        dialog = StagePreviewWindow(None, stage, SHINE, "fixture", through_stage=True, submit=submit,
                                    save=lambda _: self.fail("Changing the display modified stage settings"))
        dialog.show()
        dialog.start_preview()
        dialog.preview_mode.setCurrentText("Show affected pixels")
        dialog.preview_mode.setCurrentText("Pixel difference")
        pixel = DisplayImage(1, 1, bytes([50, 40, 30]))
        pending[0][2].set_result(PicturePreview("1", "shine", pixel, pixel))
        dialog._finish_preview()
        self.assertEqual([item[:2] for item in pending], [(settings, "Stage result"), (settings, "Pixel difference")])
        self.assertTrue(dialog.comparison.after.pixmap().isNull())
        colored = DisplayImage(1, 1, bytes([220, 50, 130]))
        pending[1][2].set_result(PicturePreview("2", "shine", pixel, colored, result_caption="Pixel difference"))
        dialog._finish_preview()
        self.assertEqual(dialog.comparison.result_label.text(), "Pixel difference")
        dialog.preview_mode.setCurrentText("Show affected pixels")
        self.assertEqual(pending[2][:2], (settings, "Show affected pixels"))
        pending[2][2].set_result(PicturePreview("3", "shine", pixel, colored, result_caption="Areas in pink"))
        dialog._finish_preview()
        self.assertEqual(dialog.comparison.result_label.text(), "Areas in pink")
        dialog.preview_mode.setCurrentText("Stage result")
        pending[3][2].set_result(PicturePreview("4", "shine", pixel, pixel))
        dialog._finish_preview()
        self.assertEqual(dialog.comparison.result_label.text(), "After this stage")
        with patch.object(QMessageBox, "question") as prompt:
            dialog.close()
            prompt.assert_not_called()

    def close_window(self, window):
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            window.close()

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Qt's offscreen plugin has no Windows font database. Register a local
        # system font for meaningful glyph/raster checks; native Windows uses its
        # normal platform font integration, without bundling this font.
        if not QFontDatabase.families():
            font_file = Path(os.environ["WINDIR"]) / "Fonts" / "segoeui.ttf"
            font_id = QFontDatabase.addApplicationFont(str(font_file))
            if font_id < 0:
                raise RuntimeError(f"Cannot load test font: {font_file}")
            cls.app.setFont(QFont("Segoe UI", 10))

    def test_window_displays_effective_state_and_warning(self):
        path = Path("D:/пример с пробелами")
        snapshot = StartupSnapshot((path,), "Explicit test", "test.json", "cpu", path, path, path,
                                   ("LOG_WRITE_FAILED: simulated persistent warning",), pytorch_backend="cuda")
        window = MainWindow(snapshot)
        try:
            window.show()
            self.app.processEvents()
            details = window.findChild(QPlainTextEdit, "startupDetails").toPlainText()
            self.assertIn("Explicit test", details)
            self.assertIn("ONNX Runtime Backend: cpu", details)
            self.assertIn("PyTorch Backend: cuda", details)
            self.assertIn("пример с пробелами", details)
            self.assertIn("LOG_WRITE_FAILED", details)
            self.assertIn("Auto ReTouch", window.windowTitle())
            self.assertTrue(QFontMetrics(window.font()).inFontUcs4(ord("A")))
        finally:
            self.close_window(window)

    def test_pipeline_editor_actions_json_dialogs_and_restart(self):
        artifacts = ROOT / ".test-work"
        artifacts.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts, prefix="qt editor ") as temporary, ExitStack() as resources:
            root = Path(temporary)
            config = load_config(root)
            log = Diagnostics(root / "logs", console=io.StringIO())
            resources.callback(log.close)
            application = ApplicationService(root, config, log)
            options = LaunchOptions((), None, None)
            snapshot = application.startup(options, "directml")
            editor = application.pipeline_service
            config.pipelines.mkdir(exist_ok=True)
            (config.pipelines / "Studio.json").write_text(json.dumps(editor.status.definition.to_json()))
            window = MainWindow(snapshot, editor, application=application)
            panel = window.pipeline_page

            def click(name, container=panel):
                container.findChild(QAbstractButton, name).click()
                self.app.processEvents()

            def stages():
                return editor.status.definition.stages

            def card(stage):
                return panel.findChild(StageCard, f"stage-{stage.id}")

            def select_file(action, path):
                # Use the real dialog wiring and simulate the OS returning a filename.
                with patch.object(QFileDialog, "exec", return_value=1), patch.object(
                        QFileDialog, "selectedFiles", return_value=[str(path)]):
                    click(action)

            try:
                window.show()
                self.app.processEvents()
                self.assertTrue(window.batch_panel.images.isVisible())
                self.assertTrue(panel.isVisible())
                presets = panel.presets
                self.assertEqual(presets.count(), 1)
                self.assertEqual((presets.currentIndex(), presets.currentText(), presets.placeholderText()), (-1, "", ""))
                self.assertIsNone(panel.findChild(QComboBox, "operationPicker"))
                menu = panel.findChild(QPushButton, "addOperation").menu()
                expected = [(d.operation, d.version, model) for d in editor.registry.descriptors()
                            for model in d.model_refs or (None,)]
                self.assertEqual([action.data() for action in menu.actions()], expected)
                QTimer.singleShot(0, lambda: QTest.keyClick(menu, Qt.Key.Key_Escape))
                click("addOperation")
                self.assertFalse(stages())
                exposure_action = next(a for a in menu.actions() if a.data() == ("core.exposure", 1, None))

                def choose_exposure():
                    menu.setActiveAction(exposure_action)
                    QTest.keyClick(menu, Qt.Key.Key_Return)

                for _ in range(2):
                    QTimer.singleShot(0, choose_exposure)
                    click("addOperation")
                self.app.processEvents()  # Apply the deferred card refresh after the menu action.
                first, second = stages()
                self.assertEqual(first.operation, "core.exposure")
                self.assertNotEqual(first.id, second.id)
                self.assertFalse(card(first).findChild(QAbstractButton, "moveStageUp").isEnabled())
                self.assertFalse(card(second).findChild(QAbstractButton, "moveStageDown").isEnabled())
                self.assertIn("Exposure (reference)", window.batch_panel.pipeline_summary.text())
                self.assertEqual(card(first).toggle.text(), "Exposure (reference)")
                self.assertEqual(card(first).findChild(QLabel, "stageVersion").text(), "v1")
                for label in ("In", "Out"):
                    self.assertEqual(card(first).findChild(QLabel, f"tag-{label}-pixels").text(), "pixels")
                self.assertEqual(card(first).preview_pipeline.text(), "Preview in pipeline")
                slider = card(second).findChild(QSlider, "blendSlider")
                self.assertEqual((slider.minimum(), slider.maximum(), slider.value()), (0, 100, 100))
                slider.setValue(35)
                self.app.processEvents()
                self.assertEqual(stages()[1].parameter("blend"), .35)

                exposure = card(second).findChild(QDoubleSpinBox, "parameter-stops")
                exposure.setValue(1.25)
                exposure.editingFinished.emit()
                click("moveStageUp", card(second))
                self.assertEqual([stage.id for stage in stages()], [second.id, first.id])
                self.assertIs(panel.cards.itemAt(0).widget(), card(second))
                self.assertEqual(stages()[0].parameter("stops"), 1.25)
                click("moveStageDown", card(second))
                self.assertEqual([stage.id for stage in stages()], [first.id, second.id])
                card(second).findChild(QCheckBox, "stageEnabled").click()
                self.app.processEvents()

                output = config.pipelines / "сохранённый профиль.json"
                select_file("savePipeline", output)
                self.assertEqual(presets.currentData(), str(output))
                self.assertTrue(window.batch_panel.pipeline_summary.text().startswith(output.stem + " ·"))
                output_bytes = output.read_bytes()
                saved = json.loads(output_bytes)
                self.assertEqual(saved, editor.status.definition.to_json())
                self.assertFalse(saved["stages"][1]["enabled"])
                working = editor.autosave.read_bytes()
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as confirm:
                    click("removeStage", card(first))
                    confirm.assert_called_once()
                    self.assertIn("stage 1 (Exposure (reference))", confirm.call_args.args[2])
                    self.assertEqual(confirm.call_args.args[-1], QMessageBox.StandardButton.Cancel)
                self.assertEqual(editor.status.definition.to_json(), saved)
                self.assertEqual(editor.autosave.read_bytes(), working)
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                    click("removeStage", card(first))
                self.assertEqual([stage.id for stage in stages()], [second.id])
                self.assertIsNone(card(first))

                message = panel.findChild(QLabel, "pipelineCommandError")
                with patch("retouching_tool.json_io.os.replace", side_effect=PermissionError("simulated save denial")):
                    select_file("savePipeline", output)
                self.assertIn("PIPELINE_WRITE_FAILED", message.text())
                self.assertIn("PermissionError: simulated save denial", log.records[-1]["exception"])
                self.assertEqual(output.read_bytes(), output_bytes)
                wrong_extension = root / "photo.jpg"
                select_file("savePipeline", wrong_extension)
                self.assertIn("ending in .json", message.text())
                self.assertFalse(wrong_extension.exists())

                imported = config.pipelines / "загруженный профиль.JSON"
                saved["name"] = "Imported profile"
                imported.write_text(json.dumps(saved), encoding="utf-8")
                original = imported.read_bytes()
                presets.showPopup()
                presets.hidePopup()
                index = presets.findData(str(imported))
                self.assertGreaterEqual(index, 0)
                working = editor.autosave.read_bytes()
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as confirm:
                    presets.setCurrentIndex(index)
                    presets.activated.emit(index)
                    confirm.assert_called_once()
                self.assertEqual(editor.autosave.read_bytes(), working)
                self.assertEqual(presets.currentData(), str(output))
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
                    presets.setCurrentIndex(index)
                    presets.activated.emit(index)
                self.app.processEvents()
                self.assertEqual(presets.currentData(), str(imported))
                self.assertEqual(editor.status.definition.to_json(), saved)
                details = panel.findChild(QPlainTextEdit, "startupDetails")
                self.assertIn(str(imported), details.toPlainText())
                self.assertIn("Imported profile", window.batch_panel.pipeline_summary.text())

                invalid = root / "invalid.json"
                invalid.write_text("{", encoding="utf-8")
                autosave = root / "state.json"
                before = autosave.read_bytes()
                with patch.object(QMessageBox, "question") as confirm:
                    select_file("openPipeline", invalid)
                    confirm.assert_not_called()
                self.assertIn("JSON_INVALID", message.text())
                self.assertIn(str(imported), details.toPlainText())
                self.assertEqual(autosave.read_bytes(), before)
                self.assertEqual(presets.currentData(), str(imported))
                failure = log.records[-1]
                self.assertEqual(failure["code"], "PIPELINE_COMMAND_FAILED")
                self.assertIn("JSONDecodeError", failure["exception"])

                with patch.object(QFileDialog, "exec", return_value=0):
                    click("openPipeline")
                self.assertEqual(autosave.read_bytes(), before)
                card(first).findChild(QCheckBox, "stageEnabled").click()
                self.app.processEvents()
                self.assertEqual(imported.read_bytes(), original)
                reopened = ApplicationService(root, config, log)
                reopened.startup(options, "directml")
                self.assertEqual(reopened.pipeline_service.status.definition, editor.status.definition)
                self.assertTrue(window.grab().save(str(artifacts / "pipeline-editor.png")))
            finally:
                self.close_window(window)
                window.deleteLater()
                self.app.processEvents()

    def test_model_card_disable_parameters_dependency_and_recovery(self):
        artifacts = ROOT / ".test-work"
        artifacts.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts, prefix="qt pipeline ") as temporary:
            root = Path(temporary)
            log = Diagnostics(root / "logs", console=io.StringIO())
            self.addCleanup(log.close)
            content = b"synthetic model fixture, no inference"
            catalog = ModelCatalog((ModelManifest("fixture", "1", (ModelFile("fixture.bin", hashlib.sha256(content).hexdigest()),),
                ("directml",), "https://example.test/model", "Test-authored synthetic bytes; no production weights"),))
            editor = PipelineService(root / "models", "directml", log, catalog=catalog, autosave=root / "last.json")
            skin = ContextSpec("test.skin", pixel_sensitive=False)
            descriptor = OperationDescriptor("test.model", 1, "Synthetic analysis fixture",
                                              parameters=(Parameter("strength", "Strength", .5, 0, 1), *SCALING_PARAMETERS),
                                              provides=(skin,), model_refs=("fixture",))
            editor.registry.register(descriptor, None)
            editor.registry.register(OperationDescriptor("test.consumer", 1, "Synthetic context consumer", requires=(skin,)), None)
            editor.load_data({"schema_version": 1, "name": "Card contract fixture", "stages": [
                {"id": "model", "operation": "test.model", "operation_version": 1, "enabled": True, "parameters": {"strength": .123456789}, "model_ref": "fixture"},
                {"id": "consumer", "operation": "test.consumer", "operation_version": 1, "enabled": True, "parameters": {}}]})
            snapshot = StartupSnapshot((), editor.status.definition.name, "synthetic test", "directml", root, root, root, (), stages=editor.status.stages)
            window = MainWindow(snapshot, editor)
            try:
                window.resize(980, 940)
                window.show()
                self.app.processEvents()
                card = window.findChild(StageCard, "stage-model")
                window.pipeline_page.set_preview_available(True)
                toggle = card.findChild(QCheckBox, "stageEnabled")
                self.assertFalse(toggle.isChecked())
                self.assertFalse(toggle.isEnabled())
                self.assertFalse(card.preview.isEnabled())
                self.assertFalse(card.preview_pipeline.isEnabled())
                self.assertEqual(card.findChild(QLabel, "tag-Out-test.skin").text(), "test.skin")
                self.assertIsNone(card.findChild(QSlider, "blendSlider"))
                consumer = window.findChild(StageCard, "stage-consumer")
                self.assertIn("Required input", consumer.findChild(QLabel, "tag-In-test.skin").accessibleName())
                self.assertIn("MODEL_MISSING", card.findChild(QLabel, "stageStatus").text())
                control = card.findChild(QDoubleSpinBox, "parameter-strength")
                self.assertTrue(control.isEnabled())
                control.editingFinished.emit()
                self.app.processEvents()
                self.assertEqual(editor.status.definition.stages[0].parameter("strength"), .123456789)
                control.setValue(.7)
                control.editingFinished.emit()
                self.app.processEvents()
                self.assertEqual(editor.status.definition.stages[0].parameter("strength"), .7)
                card = window.findChild(StageCard, "stage-model")
                method = card.findChild(QComboBox, "parameter-downscale")
                self.assertEqual(method.currentText(), "Use app setting")
                method.setCurrentIndex(method.findData("nearest"))
                method.activated.emit(method.currentIndex())
                self.assertEqual(editor.status.definition.stages[0].parameter("downscale"), "nearest")
                # Explicit navigation alone opens a publisher page; rechecks never do.
                with patch("retouching_tool.ui.qt.stage_card.QDesktopServices.openUrl", return_value=True) as open_url:
                    card.findChild(QAction, "modelPage").trigger()
                    open_url.assert_called_once()
                    self.assertEqual(open_url.call_args.args[0].toString(), "https://example.test/model")
                self.app.processEvents()
                self.app.processEvents()
                self.assertTrue(card.isVisible())
                self.assertTrue(window.grab().save(str(artifacts / "pipeline-cards.png")))
                path = root / "models" / "fixture.bin"
                path.parent.mkdir()
                path.write_bytes(content)
                card.findChild(QAction, "recheckStage").trigger()
                self.app.processEvents()
                card = window.findChild(StageCard, "stage-model")
                toggle = card.findChild(QCheckBox, "stageEnabled")
                self.assertTrue(toggle.isEnabled())
                self.assertFalse(toggle.isChecked())
                self.assertTrue(card.preview.isEnabled())
                self.assertTrue(card.preview_pipeline.isEnabled())
                self.assertEqual(editor.status.stages[1].state, "blocked")
                toggle.click()
                self.app.processEvents()
                self.assertEqual(editor.status.stages[1].state, "ready")
                card.findChild(QAbstractButton, "moveStageDown").click()
                self.app.processEvents()
                self.assertEqual(editor.status.stages[0].state, "blocked")
                card.findChild(QAbstractButton, "moveStageUp").click()
                self.app.processEvents()
                self.assertEqual(editor.status.stages[1].state, "ready")
                path.unlink()
                window.findChild(StageCard, "stage-model").findChild(QAction, "recheckStage").trigger()
                self.app.processEvents()
                self.assertFalse(editor.status.definition.stages[0].enabled)
                self.assertFalse(window.findChild(StageCard, "stage-model").findChild(QCheckBox, "stageEnabled").isEnabled())
                log.failure = "LOG_WRITE_FAILED: simulated persistent diagnostics failure"
                window.pipeline_page._command(editor.recheck)
                self.app.processEvents()
                self.assertIn("LOG_WRITE_FAILED", window.findChild(QLabel, "pipelineCommandError").text())
            finally:
                self.close_window(window)
                window.deleteLater()
                self.app.processEvents()
                log.close()
            self.app.processEvents()

    def test_alibaba_inputs_and_model_menu_in_compact_card(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as resources:
            root = Path(temporary)
            log = Diagnostics(root / "logs", console=io.StringIO())
            resources.callback(log.close)
            editor = PipelineService(root / "models", "directml", log, pytorch_backend="cpu")
            stage = editor.add_operation(SMOOTHING.operation, 1, model_ref=SMOOTHING_MODEL.ref).stages[0].stage
            snapshot = StartupSnapshot((), "Pipeline", "test", "directml", root, root, root, ())
            window = MainWindow(snapshot, editor)
            resources.callback(self.close_window, window)
            window.resize(460, 760)
            window.show()
            self.app.processEvents()
            card = window.pipeline_page.stage_cards[stage.id]
            required = card.findChild(QLabel, "tag-In-pixels")
            optional = card.findChild(QLabel, "tag-In-face.regions")
            self.assertIn("solid", required.styleSheet())
            self.assertIn("dashed", optional.styleSheet())
            self.assertEqual(optional.text(), "areas:face · optional")
            checkbox = card.findChild(QCheckBox, "parameter-use_face_rects")
            self.assertTrue(checkbox.isChecked())
            checkbox.click()
            self.app.processEvents()
            self.assertIs(editor.status.stages[0].stage.parameter("use_face_rects"), False)
            self.assertTrue(optional.isHidden())
            checkbox.click()
            self.app.processEvents()
            self.assertFalse(optional.isHidden())
            mask_target = card.findChild(QCheckBox, "parameter-use_exposed_skin")
            mask_tag = card.findChild(QLabel, "tag-In-skin.exposed")
            self.assertTrue(mask_tag.isHidden())
            mask_target.click()
            self.app.processEvents()
            self.assertFalse(mask_tag.isHidden())
            self.assertTrue(optional.isHidden())
            self.assertFalse(checkbox.isEnabled())
            mask_target.click()
            self.app.processEvents()
            self.assertTrue(checkbox.isEnabled())
            mask_stage = window.pipeline_page._command(editor.add_operation, SKIN.operation, 1,
                                                       model_ref=SKIN_MODEL.ref).stages[-1].stage
            self.app.processEvents()
            mask_card = window.pipeline_page.stage_cards[mask_stage.id]
            self.assertFalse(mask_card.toggle.isEnabled())
            self.assertIn(str(editor.checkpoints / SKIN_MODEL.files[0].relative_path), mask_card.status_label.text())
            self.assertEqual(mask_card.model_page, SKIN_MODEL.publisher_url)
            face_stage = window.pipeline_page._command(editor.add_operation, DETECTOR.operation, 1,
                                                       model_ref=DETECTOR_MODEL.ref).stages[-1].stage
            self.app.processEvents()
            expansion = window.pipeline_page.stage_cards[face_stage.id].findChild(QSpinBox, "parameter-expand_pixels")
            self.assertEqual(expansion.value(), 0)
            expansion.setValue(20)
            expansion.editingFinished.emit()
            self.assertEqual(editor.status.stages[-1].stage.parameter("expand_pixels"), 20)
            for item, desc, widget, key, value in (
                    (stage, SMOOTHING, QCheckBox, "use_face_rects", False),
                    (editor.status.stages[-1].stage, DETECTOR, QSpinBox, "expand_pixels", 40)):
                dialog = StagePreviewWindow(window, item, desc, "fixture", through_stage=True, submit=None, save=None)
                control = dialog.findChild(widget, f"parameter-{key}")
                if isinstance(control, QCheckBox):
                    control.click()
                else:
                    control.setValue(value)
                self.assertEqual(dialog._draft[key], value)
                self.assertTrue(dialog.apply_preview.isEnabled())
                dialog.deleteLater()
            self.assertEqual(card.findChild(QLabel, "tag-Out-pixels").text(), "pixels")
            self.assertIn(str(editor.checkpoints / SMOOTHING_MODEL.files[0].relative_path), card.status_label.text())
            self.assertNotIn(BLEMISH_MODEL.files[0].relative_path.split("/")[-1], card.status_label.text())
            self.assertEqual([a.text() for a in card.settings.menu().actions()],
                             ["Open model page in browser", "Check required model files"])
            self.assertLessEqual(card.minimumSizeHint().width(), 420)
            cleanup = window.pipeline_page._command(editor.add_operation, BLEMISH.operation, 1,
                                                    model_ref=BLEMISH_MODEL.ref).stages[-1].stage
            self.app.processEvents()
            cleanup_card = window.pipeline_page.stage_cards[cleanup.id]
            self.assertEqual(cleanup_card.toggle.text(), "Blemish cleanup (Alibaba)")
            self.assertIn(str(editor.checkpoints / BLEMISH_MODEL.files[0].relative_path), cleanup_card.status_label.text())


if __name__ == "__main__":
    unittest.main()
