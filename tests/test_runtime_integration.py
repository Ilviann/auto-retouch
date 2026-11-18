"""Integration against the prepared project environment; no installs or downloads."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from retouching_tool.environment import probe_backend, validate_python, validate_ready
from retouching_tool.errors import RetouchingError

ROOT = Path(__file__).resolve().parents[1]


class RuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-work").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-work", prefix="runtime пример ")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_prepared_backend_executes_without_fallback(self):
        result = validate_ready(ROOT)
        expected = {"cpu": "CPUExecutionProvider", "directml": "DmlExecutionProvider", "cuda": "CUDAExecutionProvider"}[result.backend]
        self.assertEqual(result.result, (4.0, 6.0))
        self.assertEqual(result.executed_providers, (expected,))
        self.assertIn(result.pytorch_backend, ("cpu", "cuda"))
        self.assertTrue(result.pytorch_version)


    def test_cmd_help_from_another_working_directory(self):
        result = subprocess.run(["cmd", "/d", "/c", str(ROOT / "run.cmd"), "--help"],
                                cwd=self.root, capture_output=True, text=True, errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--pipeline", result.stdout)
        self.assertNotIn("BACKEND_READY", result.stderr)

    def test_cmd_invalid_explicit_config_exits_with_traceback_at_info(self):
        invalid = self.root / "неверная конфигурация.json"
        invalid.write_text('{"schema_version": 2}', encoding="utf-8")
        result = subprocess.run(["cmd", "/d", "/c", str(ROOT / "run.cmd"), "--config", str(invalid)],
                                cwd=self.root, capture_output=True, text=True, errors="replace")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("CONFIG_INVALID", result.stderr)
        newest = max((ROOT / "logs").glob("auto-retouch-*.jsonl"), key=lambda p: p.stat().st_mtime_ns)
        events = [json.loads(line) for line in newest.read_text(encoding="utf-8").splitlines()]
        failure = next(event for event in events if event["code"] == "CONFIG_INVALID")
        self.assertIn("Traceback", failure["exception"])

    def test_full_composition_opens_and_closes_qt_without_processing(self):
        # Subprocess isolates QApplication lifetime from other GUI tests.
        code = """
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import PySide6.QtWidgets as widgets
from PySide6.QtCore import QTimer
OriginalApplication=widgets.QApplication
class TimedApplication(OriginalApplication):
    def __init__(self, *args):
        super().__init__(*args)
        QTimer.singleShot(100, self.quit)
widgets.QApplication=TimedApplication
from retouching_tool.bootstrap import main
raise SystemExit(main([]))
"""
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=self.root, capture_output=True,
                                text=True, errors="replace", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GUI_STARTED", result.stderr)
        self.assertIn("LAUNCH_EXITED", result.stderr)

    def test_wrong_python_and_unavailable_provider_fail_explicitly(self):
        with patch("retouching_tool.environment.sys.version_info", (3, 13, 0)):
            with self.assertRaises(RetouchingError) as context:
                validate_python()
        self.assertEqual(context.exception.code, "PYTHON_INCOMPATIBLE")
        import onnxruntime as ort
        with patch.object(ort, "get_available_providers", return_value=["CPUExecutionProvider"]):
            with self.assertRaises(RetouchingError) as context:
                probe_backend("directml")
        self.assertEqual(context.exception.code, "BACKEND_UNAVAILABLE")

    def test_missing_environment_cmd_records_bootstrap_failure(self):
        import shutil
        shutil.copy2(ROOT / "run.cmd", self.root / "run.cmd")
        (self.root / "tools").mkdir()
        shutil.copy2(ROOT / "tools" / "bootstrap_error.cmd", self.root / "tools" / "bootstrap_error.cmd")
        result = subprocess.run(["cmd", "/d", "/c", str(self.root / "run.cmd")],
                                cwd=self.root, capture_output=True, text=True, errors="replace")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ENVIRONMENT_MISSING", result.stderr)
        self.assertIn("ENVIRONMENT_MISSING", (self.root / "logs" / "bootstrap-errors.log").read_text())


if __name__ == "__main__":
    unittest.main()
