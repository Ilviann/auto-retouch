import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from retouching_tool.application.service import ApplicationService, DEFAULT_PIPELINE
from retouching_tool.cli import LaunchOptions, parse_args
from retouching_tool.config import LoggingSettings, load_config
from retouching_tool.diagnostics.service import Diagnostics, redact
from retouching_tool.environment import lock_identity, read_ready_backend, validate_packages
from retouching_tool.errors import RetouchingError
from retouching_tool.json_io import atomic_json, read_json

ROOT = Path(__file__).resolve().parents[1]



class FoundationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-work").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-work", prefix="пример ")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.log = Diagnostics(self.root / "logs", console=io.StringIO())
        self.addCleanup(self.log.close)

    def write(self, name, data):
        path = self.root / name
        atomic_json(path, data)
        return path

    def service(self):
        return ApplicationService(self.root, load_config(self.root), self.log)

    def options(self, sources=(), pipeline=None, config=None):
        return LaunchOptions(sources, pipeline, config)

    def test_strict_json_and_atomic_failure_preserve_previous(self):
        path = self.write("state.json", {"text": "Русский путь"})
        self.assertEqual(read_json(path)["text"], "Русский путь")
        with patch("retouching_tool.json_io.os.replace", side_effect=OSError("simulated write failure")):
            with self.assertRaises(OSError):
                atomic_json(path, {"new": True})
        self.assertEqual(read_json(path)["text"], "Русский путь")
        self.assertFalse(list(path.parent.glob(".state.json.*.tmp")))
        atomic_json(path, {"quoted brackets": "[" * 2000 + '\\"' + "]" * 2000})
        self.assertIn("quoted brackets", read_json(path))
        for raw in ('{"x":1,"x":2}', '{"x": NaN}', '{broken', '[' * 2000 + ']' * 2000):
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(RetouchingError):
                read_json(path)
        path.write_bytes(b"x" * 20)
        with self.assertRaises(RetouchingError):
            read_json(path, limit=10)

    def test_config_defaults_and_file_relative_paths(self):
        config = load_config(self.root)
        self.assertTrue(config.using_defaults)
        self.assertEqual(config.temporary, self.root / ".work")
        path = self.write("settings/config.json", {"schema_version": 1, "paths": {"checkpoints": "../модели"}})
        config = load_config(self.root, path)
        self.assertEqual(config.checkpoints, self.root / "модели")
        self.assertFalse(config.using_defaults)
        self.assertFalse((self.root / "модели").exists())

    def test_invalid_config_is_explicit(self):
        invalid = [[], {}, {"schema_version": True}, {"schema_version": 2},
                   {"schema_version": 1, "backend": "cpu"},
                   {"schema_version": 1, "paths": {"temporary": ""}},
                   {"schema_version": 1, "paths": {"temporary": "scratch", "logs": "scratch/logs"}},
                   {"schema_version": 1, "logging": {"level": "verbose"}},
                   {"schema_version": 1, "logging": {"max_file_mb": True}},
                   {"schema_version": 1, "logging": {"max_total_mb": 1}}]
        for data in invalid:
            with self.subTest(data=data):
                path = self.write("config.json", data)
                with self.assertRaises(RetouchingError):
                    load_config(self.root)
        with self.assertRaises(RetouchingError):
            load_config(self.root, self.root / "absent.json")

    def test_cli_paths_resolve_against_caller_and_no_backend_flag(self):
        options = parse_args(["--source", ".", "--source", ".", "--pipeline", "пример.json", "--config", "cfg.json"])
        self.assertEqual(options.sources, (Path.cwd(),))
        self.assertEqual(options.pipeline, Path.cwd() / "пример.json")
        self.assertEqual(options.config, Path.cwd() / "cfg.json")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--backend", "cuda"])
        for option in ("--source", "--pipeline", "--config"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args([option, ""])

    def test_explicit_settings_override_restored_without_processing(self):
        old = self.root / "old"
        new = self.root / "новые фото"
        old.mkdir()
        new.mkdir()
        self.write(".state/session.json", {"schema_version": 1, "sources": [str(old)]})
        self.write(".state/last-pipeline.json", {**DEFAULT_PIPELINE, "name": "Old"})
        explicit = self.write("pipeline.json", {**DEFAULT_PIPELINE, "name": "Explicit"})
        snapshot = self.service().startup(self.options((new,), explicit), "cpu")
        self.assertEqual(snapshot.sources, (new,))
        self.assertEqual(snapshot.pipeline_name, "Explicit")
        self.assertFalse(snapshot.processing_available)
        restored = self.service().startup(self.options(), "cpu")
        self.assertEqual(restored.pipeline_name, "Explicit")
        self.assertEqual(restored.sources, (new,))
        with self.assertRaises(Exception):
            snapshot.backend = "cuda"

    def test_explicit_invalid_pipeline_never_falls_back_or_persists(self):
        saved = self.write(".state/last-pipeline.json", DEFAULT_PIPELINE)
        before = saved.read_bytes()
        for data in ({"bad": True}, {**DEFAULT_PIPELINE, "stages": [{"operation": "unknown"}]}):
            explicit = self.write("bad.json", data)
            with self.assertRaises(RetouchingError):
                self.service().startup(self.options(pipeline=explicit), "cpu")
            self.assertEqual(saved.read_bytes(), before)

    def test_invalid_restore_visible_and_original_preserved(self):
        path = self.write(".state/last-pipeline.json", {"bad": True})
        before = path.read_bytes()
        snapshot = self.service().startup(self.options(), "cpu")
        self.assertTrue(any("invalid or unsupported" in notice for notice in snapshot.notices))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.log.records[-2]["code"], "PIPELINE_RESTORE_FAILED")
        self.assertIn("exception", self.log.records[-2])

    def test_explicit_invalid_source_is_error(self):
        invalid = self.root / "source.png"
        invalid.write_text("synthetic non-JPEG fixture")
        with self.assertRaises(RetouchingError):
            self.service().startup(self.options((invalid,)), "cpu")

    def test_diagnostics_context_traceback_and_secret_redaction(self):
        try:
            try:
                raise OSError("simulated root cause")
            except OSError as exc:
                raise ValueError("https://alice:secret@example.test/simple?token=abc") from exc
        except ValueError:
            self.log.emit("SIMULATED_FAILURE", "Failed", level="ERROR", exc_info=True,
                          job_id="j1", image_id="i1", stage_instance_id="s1", attempt=2)
        event = read_json_lines(self.log.path)[0]
        self.assertEqual(event["stage_instance_id"], "s1")
        self.assertEqual(event["severity"], "ERROR")
        self.assertIn("simulated root cause", event["exception"])
        self.assertIn("ValueError", event["exception"])
        self.assertNotIn("alice", event["exception"])
        self.assertNotIn("secret@", event["exception"])
        self.assertNotIn("token=abc", event["exception"])

    def test_log_storage_failure_exposed_and_memory_bounded(self):
        path = self.root / "not-a-directory"
        path.write_text("fixture")
        output = io.StringIO()
        log = Diagnostics(path, console=output)
        self.addCleanup(log.close)
        for i in range(150):
            log.emit("EXAMPLE", str(i))
        self.assertIsNotNone(log.failure)
        self.assertIn("LOG_WRITE_FAILED", output.getvalue())
        self.assertEqual(len(log.records), 100)
        self.assertFalse(log.path.exists())

    def test_simulated_disk_full_is_not_claimed_saved(self):
        class Full:
            def write(self, value):
                raise OSError(28, "simulated disk full")
            def close(self):
                pass
        self.log.stream.close()
        self.log.stream = Full()
        self.log.emit("EXAMPLE", "record")
        self.assertIn("simulated disk full", self.log.failure)
        self.assertEqual(len(self.log.records), 1)

    def test_log_rotation_retention_preserves_unowned_files(self):
        self.log.close()
        owned = self.root / "logs"
        unrelated = owned / "notes.txt"
        unrelated.write_bytes(b"do not remove")
        ancient = owned / ("auto-retouch-" + "a" * 32 + ".jsonl")
        ancient.write_text("old")
        os.utime(ancient, (0, 0))
        log = Diagnostics(owned, LoggingSettings(max_file_mb=1, max_total_mb=2, retention_days=1), console=io.StringIO())
        self.addCleanup(log.close)
        for _ in range(300):
            log.emit("LARGE", "x" * 15000)
        self.assertFalse(ancient.exists())
        self.assertEqual(unrelated.read_bytes(), b"do not remove")
        self.assertLessEqual(sum(p.stat().st_size for p in owned.glob("*.jsonl*")), 2_000_000)
        self.assertTrue(list(owned.glob("*.jsonl.1")) or log._part > 1)


    def fake_environment(self):
        python = self.root / ".venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.touch()
        locks = self.root / "requirements"
        locks.mkdir()
        for name in ("cpu.lock", "shared.lock"):
            (locks / name).write_bytes((ROOT / "requirements" / name).read_bytes())
        from retouching_tool import __version__
        path = self.write(".venv/retouching-backend.json", {"schema_version": 1, "ready": True, "backend": "cpu",
                          "lock_sha256": lock_identity(self.root, "cpu"), "app_version": __version__})
        return path





    def test_diagnostic_record_payload_is_bounded(self):
        self.log.emit("LARGE", "я" * 20000, details="data" * 100000, stage_instance_id="s1")
        self.assertLessEqual(self.log.path.stat().st_size, 65537)
        event = read_json_lines(self.log.path)[0]
        self.assertTrue(event["truncated"])
        self.assertEqual(event["stage_instance_id"], "s1")

    def test_retention_failure_is_visible_but_does_not_stop_persistence(self):
        with patch.object(self.log, "_retain", side_effect=PermissionError("simulated retention denial")):
            self.log.emit("EXAMPLE", "still saved")
        self.assertIn("LOG_RETENTION_FAILED", self.log.failure)
        self.assertIsNotNone(self.log.stream)
        self.assertEqual(read_json_lines(self.log.path)[0]["message"], "still saved")

    def test_backend_metadata_detects_changed_locks(self):
        self.fake_environment()
        self.assertEqual(read_ready_backend(self.root), "cpu")
        (self.root / "requirements" / "shared.lock").write_text("changed")
        with self.assertRaises(RetouchingError):
            read_ready_backend(self.root)

    def test_headless_import_boundary_in_fresh_interpreter(self):
        code = """
import sys
class RejectQt:
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PySide6', 'shiboken6')):
            raise RuntimeError('Qt crossed shared boundary')
sys.meta_path.insert(0, RejectQt())
import retouching_tool.application.service
import retouching_tool.environment
import retouching_tool.config
import retouching_tool.diagnostics.service
from retouching_tool.cli import parse_args
parse_args([])
assert not any(n.startswith(('PySide6','onnxruntime','numpy')) for n in sys.modules)
"""
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_static_qt_import_boundary(self):
        import ast
        for path in (ROOT / "src" / "retouching_tool").rglob("*.py"):
            if "ui" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [v.name for v in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse(any(n.startswith(("PySide6", "shiboken6")) for n in names), str(path))


def read_json_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


if __name__ == "__main__":
    unittest.main()
