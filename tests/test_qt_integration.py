"""A focused Widgets integration test; no processing/portrait claims."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import unittest

from PySide6.QtWidgets import QApplication, QPlainTextEdit
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from retouching_tool.application.contracts import StartupSnapshot
from retouching_tool.ui.qt.main_window import MainWindow


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
            self.assertIn("Auto Retouch (by Neyrome)", window.windowTitle())
            self.assertTrue(QFontMetrics(window.font()).inFontUcs4(ord("A")))
        finally:
            window.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
