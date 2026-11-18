"""A focused Widgets integration test; no processing/portrait claims."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from contextlib import ExitStack
from pathlib import Path
import hashlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QLabel, QPlainTextEdit, QPushButton,
)
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from retouching_tool.application.contracts import StartupSnapshot
from retouching_tool.application.pipeline_service import PipelineService
from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.pipeline.assets import ModelCatalog, ModelFile, ModelManifest
from retouching_tool.pipeline.definition import ContextSpec, OperationDescriptor, Parameter, SCALING_PARAMETERS
from retouching_tool.ui.qt.main_window import MainWindow
from retouching_tool.ui.qt.stage_card import StageCard

ROOT = Path(__file__).resolve().parents[1]


class QtIntegrationTests(unittest.TestCase):
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
            window.close()

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
            window = MainWindow(snapshot, editor, application=application)
            panel = window.pipeline_page

            def click(name, container=panel):
                container.findChild(QPushButton, name).click()
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
                picker = panel.findChild(QComboBox, "operationPicker")
                picker.setCurrentIndex(next(index for index in range(picker.count())
                                            if picker.itemData(index) == ("core.exposure", 1, None)))
                click("addOperation")
                click("addOperation")
                first, second = stages()
                self.assertEqual(first.operation, "core.exposure")
                self.assertNotEqual(first.id, second.id)
                self.assertFalse(card(first).findChild(QPushButton, "moveStageUp").isEnabled())
                self.assertFalse(card(second).findChild(QPushButton, "moveStageDown").isEnabled())
                self.assertIn("Exposure (reference)", window.batch_panel.pipeline_summary.text())
                contract = card(first).findChild(QLabel, "stageDataContract").text()
                self.assertIn("May change pixels", contract)
                self.assertIn("preserves source metadata", contract)
                self.assertIn("invalidates image.luminance", contract)

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

                output = root / "сохранённый профиль.json"
                select_file("savePipeline", output)
                output_bytes = output.read_bytes()
                saved = json.loads(output_bytes)
                self.assertEqual(saved, editor.status.definition.to_json())
                self.assertFalse(saved["stages"][1]["enabled"])
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

                imported = root / "загруженный профиль.json"
                saved["name"] = "Imported profile"
                imported.write_text(json.dumps(saved), encoding="utf-8")
                original = imported.read_bytes()
                select_file("openPipeline", imported)
                self.assertEqual(editor.status.definition.to_json(), saved)
                details = panel.findChild(QPlainTextEdit, "startupDetails")
                self.assertIn(str(imported), details.toPlainText())
                self.assertIn("Imported profile", window.batch_panel.pipeline_summary.text())

                invalid = root / "invalid.json"
                invalid.write_text("{", encoding="utf-8")
                autosave = root / ".state" / "last-pipeline.json"
                before = autosave.read_bytes()
                select_file("openPipeline", invalid)
                self.assertIn("JSON_INVALID", message.text())
                self.assertIn(str(imported), details.toPlainText())
                self.assertEqual(autosave.read_bytes(), before)
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
                window.close()
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
                self.assertIn("produces test.skin v1", card.findChild(QLabel, "stageDataContract").text())
                consumer = window.findChild(StageCard, "stage-consumer")
                self.assertIn("test.skin v1 (required)", consumer.findChild(QLabel, "stageDataContract").text())
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
                    card.findChild(QPushButton, "modelPage").click()
                    open_url.assert_called_once()
                    self.assertEqual(open_url.call_args.args[0].toString(), "https://example.test/model")
                self.app.processEvents()
                self.app.processEvents()
                self.assertTrue(card.isVisible())
                self.assertTrue(window.grab().save(str(artifacts / "pipeline-cards.png")))
                path = root / "models" / "fixture.bin"
                path.parent.mkdir()
                path.write_bytes(content)
                card.findChild(QPushButton, "recheckStage").click()
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
                card.findChild(QPushButton, "moveStageDown").click()
                self.app.processEvents()
                self.assertEqual(editor.status.stages[0].state, "blocked")
                card.findChild(QPushButton, "moveStageUp").click()
                self.app.processEvents()
                self.assertEqual(editor.status.stages[1].state, "ready")
                path.unlink()
                window.findChild(StageCard, "stage-model").findChild(QPushButton, "recheckStage").click()
                self.app.processEvents()
                self.assertFalse(editor.status.definition.stages[0].enabled)
                self.assertFalse(window.findChild(StageCard, "stage-model").findChild(QCheckBox, "stageEnabled").isEnabled())
                log.failure = "LOG_WRITE_FAILED: simulated persistent diagnostics failure"
                window.pipeline_page._command(editor.recheck)
                self.app.processEvents()
                self.assertIn("LOG_WRITE_FAILED", window.findChild(QLabel, "pipelineCommandError").text())
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()
                log.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
