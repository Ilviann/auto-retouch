"""Opt-in setup checks. Run only for setup improvements or bug fixes.

Ordinary app checks use test_*.py and never run preparation or switch backends.
"""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.environment import lock_identity
from retouching_tool.errors import RetouchingError
from retouching_tool.json_io import atomic_json

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_env", ROOT / "tools" / "prepare_env.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

class SetupChecks(unittest.TestCase):
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


    def test_check_only_has_no_prompt_installs_or_metadata_writes(self):
        path = self.fake_environment()
        before = path.read_bytes(), path.stat().st_mtime_ns
        calls = []
        def runner(command, log, **kwargs):
            calls.append(command)
        setup.prepare(self.root, self.log, check=True, input_fn=lambda _: self.fail("check prompted"), runner=runner)
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        self.assertTrue(any("check" in c for c in calls))
        self.assertFalse(any("install" in c or "uninstall" in c for c in calls))


    def test_damaged_environment_is_not_recreated(self):
        environment = self.root / ".venv"
        environment.mkdir()
        sentinel = environment / "keep.txt"
        sentinel.write_text("user-owned environment fixture")
        with self.assertRaises(RetouchingError) as context:
            setup.prepare(self.root, self.log, input_fn=lambda _: self.fail("damaged environment prompted"))
        self.assertEqual(context.exception.code, "ENVIRONMENT_INVALID")
        self.assertEqual(sentinel.read_text(), "user-owned environment fixture")


    def test_failed_same_backend_update_invalidates_ready_metadata(self):
        path = self.fake_environment()
        def runner(command, log, **kwargs):
            if "install" in command:
                raise RetouchingError("SIMULATED_INSTALL_FAILURE", "injected package failure")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RetouchingError):
            setup.prepare(self.root, self.log, input_fn=lambda _: "3", runner=runner)
        self.assertFalse(path.exists())


    def test_missing_environment_check_does_not_create_one(self):
        with self.assertRaises(RetouchingError):
            setup.prepare(self.root, self.log, check=True)
        self.assertFalse((self.root / ".venv").exists())


    def test_setup_menu_cuda_default_and_invalid_input_bounded(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.select_backend(lambda _: ""), "cuda")
            self.assertEqual(setup.select_backend(lambda _: "2"), "directml")
            with self.assertRaises(RetouchingError):
                setup.select_backend(lambda _: "invalid")


    def test_check_only_preserves_all_environment_files(self):
        def snapshot():
            return {str(p.relative_to(ROOT / ".venv")): (p.stat().st_size, p.stat().st_mtime_ns)
                    for p in (ROOT / ".venv").rglob("*") if p.is_file()}
        before = snapshot()
        result = subprocess.run(["cmd", "/d", "/c", str(ROOT / "prepare_env.cmd"), "--check"],
                                cwd=self.root, capture_output=True, text=True, errors="replace")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(snapshot(), before)
        self.assertNotIn("Choice [1]", result.stdout)


if __name__ == "__main__":
    unittest.main()
