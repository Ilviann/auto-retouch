"""A focused Widgets integration test; no processing/portrait claims."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import hashlib
import io
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QCheckBox, QDoubleSpinBox, QLabel, QPlainTextEdit, QPushButton
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from retouching_tool.application.contracts import StartupSnapshot
from retouching_tool.application.pipeline_service import PipelineService
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.pipeline.assets import ModelCatalog, ModelFile, ModelManifest
from retouching_tool.pipeline.definition import ContextSpec, OperationDescriptor, Parameter
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
                                   ("LOG_WRITE_FAILED: simulated persistent warning",))
        window = MainWindow(snapshot)
        try:
            window.show()
            self.app.processEvents()
            details = window.findChild(QPlainTextEdit, "startupDetails").toPlainText()
            self.assertIn("Explicit test", details)
            self.assertIn("Backend: cpu", details)
            self.assertIn("пример с пробелами", details)
            self.assertIn("LOG_WRITE_FAILED", details)
            self.assertIn("Auto ReTouch", window.windowTitle())
            self.assertTrue(QFontMetrics(window.font()).inFontUcs4(ord("A")))
        finally:
            window.close()

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
            descriptor = OperationDescriptor("test.model", 1, "Synthetic analysis fixture", parameters=(Parameter("strength", "Strength", .5, 0, 1),),
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
                toggle = card.findChild(QCheckBox, "stageEnabled")
                self.assertFalse(toggle.isChecked())
                self.assertFalse(toggle.isEnabled())
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
                self.assertEqual(editor.status.stages[1].state, "blocked")
                toggle.click()
                self.app.processEvents()
                self.assertEqual(editor.status.stages[1].state, "ready")
                path.unlink()
                window.findChild(StageCard, "stage-model").findChild(QPushButton, "recheckStage").click()
                self.app.processEvents()
                self.assertFalse(editor.status.definition.stages[0].enabled)
                self.assertFalse(window.findChild(StageCard, "stage-model").findChild(QCheckBox, "stageEnabled").isEnabled())
                log.failure = "LOG_WRITE_FAILED: simulated persistent diagnostics failure"
                window._command(editor.recheck)
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
