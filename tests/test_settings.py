"""Settings persistence, live services and failure recovery in the headless application."""

from dataclasses import replace
from hashlib import sha256
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import FolderSettings, LoggingSettings, load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.jobs.journal import open_journal
from retouching_tool.json_io import atomic_json, read_json
from retouching_tool.operations.reference import LUMINANCE
from retouching_tool.pipeline.assets import ModelCatalog, ModelFile, ModelManifest
from retouching_tool.pipeline.definition import OperationDescriptor

ROOT = Path(__file__).resolve().parents[1]


def add_fixture_stages(editor):
    content = b"settings test fixture"
    path = editor.checkpoints / "fixture.bin"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    editor.catalog = ModelCatalog((ModelManifest("fixture", "1", (ModelFile(path.name, sha256(content).hexdigest()),),
        ("directml",), "https://example.test/model", "Authored for this test"),))
    editor.registry.register(OperationDescriptor("test.model", 1, "Test analysis", provides=(LUMINANCE,),
                                                model_refs=("fixture",)), None)
    editor.registry.register(OperationDescriptor("test.consumer", 1, "Test consumer", requires=(LUMINANCE,)), None)
    editor.add_operation("test.model", 1, model_ref="fixture")
    editor.add_operation("test.consumer", 1)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".test-work"
        work.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=work, prefix="settings ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "local" / "настройки.json"
        atomic_json(self.path, {"schema_version": 1})
        self.config = load_config(self.root, self.path)
        self.log = Diagnostics(self.config.logs, console=StringIO())
        self.addCleanup(self.log.close)
        self.application = ApplicationService(self.root, self.config, self.log)
        self.application.startup(LaunchOptions((), None, self.path), "directml")

    def test_explicit_config_restart_live_logging_and_saved_jobs(self):
        app = self.application
        source = self.root / "source"
        source.mkdir()
        Image.new("RGB", (40, 30)).save(source / "photo.jpg")
        destination = self.root / "exports"
        destination.mkdir()
        index = app.index_sources((source,))
        before = app.job_service.create(index, destination, mode="staged")
        old_log = self.log.path
        self.log.emit("DEBUG_HIDDEN", "Before changing the level", level="DEBUG")
        folders = FolderSettings("../модели", "scratch", "session logs")
        logging = LoggingSettings("DEBUG", 1, 2, 7)
        saved = app.save_settings(folders, logging)
        reopened = ApplicationService(self.root, load_config(self.root, self.path), self.log)
        startup = reopened.startup(LaunchOptions((), None, self.path), "directml")
        self.assertEqual(saved, reopened.config)
        self.assertEqual((startup.checkpoints, startup.temporary, startup.logs),
                         (saved.checkpoints, saved.temporary, saved.logs))
        self.assertEqual(saved.checkpoints, self.root / "модели")
        self.assertEqual(read_json(self.path)["paths"]["temporary"], "scratch")
        self.assertFalse((self.root / "config.json").exists())
        self.assertEqual(self.log.settings, logging)
        self.log.emit("DEBUG_VISIBLE", "Settings took effect", level="DEBUG")
        self.assertNotIn("DEBUG_HIDDEN", old_log.read_text(encoding="utf-8"))
        self.assertNotIn("DEBUG_VISIBLE", old_log.read_text(encoding="utf-8"))
        self.assertEqual(self.log.path.parent, saved.logs)
        self.assertIn("DEBUG_VISIBLE", self.log.path.read_text(encoding="utf-8"))
        self.assertEqual(app.job_worker._command(before, "test")[-1], "DEBUG")

        after = app.job_service.create(index, destination)
        for job_id, config in ((before, self.config), (after, saved)):
            with open_journal(self.root, job_id) as journal:
                job = journal.snapshot()
            self.assertEqual(Path(job["scratch"]), config.temporary)
            self.assertEqual(Path(job["checkpoints"]), config.checkpoints)
        self.assertEqual(app.job_service.run(before).status, "completed")
        self.assertTrue((destination / "photo.jpg").is_file())

    def test_validation_and_io_errors_keep_previous_settings(self):
        app = self.application
        editor = app.pipeline_service
        add_fixture_stages(editor)
        previous_status = editor.status
        pipeline_on_disk = editor.autosave.read_bytes()
        previous_config = self.path.read_bytes()
        previous_log = self.log.path
        candidates = [
            (replace(self.config.folders, temporary="logs"), LoggingSettings(), "CONFIG_INVALID"),
            (replace(self.config.folders, logs=str(editor.checkpoints / "fixture.bin")), LoggingSettings(), "CONFIG_FOLDER_INVALID"),
            (self.config.folders, LoggingSettings(max_file_mb=10, max_total_mb=1), "CONFIG_INVALID"),
        ]
        for folders, logging, code in candidates:
            with self.subTest(code=code, folders=folders), self.assertRaises(RetouchingError) as error:
                app.save_settings(folders, logging)
            self.assertEqual(error.exception.code, code)
        with patch("retouching_tool.application.service.atomic_json", side_effect=PermissionError("simulated denial")):
            with self.assertRaisesRegex(RetouchingError, "simulated denial"):
                app.save_settings(FolderSettings("missing", "new scratch", "new logs"), LoggingSettings("DEBUG"))
        self.assertIs(app.config, self.config)
        self.assertIs(editor.status, previous_status)
        self.assertEqual(editor.checkpoints, self.config.checkpoints)
        self.assertEqual(app.batch_service.scratch, self.config.temporary)
        self.assertEqual(self.path.read_bytes(), previous_config)
        self.assertEqual(editor.autosave.read_bytes(), pipeline_on_disk)
        self.assertEqual(self.log.path, previous_log)
        self.assertEqual(self.log.records[-1]["code"], "CONFIG_WRITE_FAILED")
        self.assertIn("PermissionError", self.log.records[-1]["exception"])
        self.assertEqual(self.log.records[-1]["config_path"], str(self.path))
