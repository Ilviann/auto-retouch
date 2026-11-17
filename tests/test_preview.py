"""Focused preview checks using a generated JPEG and the reference operations."""

from io import StringIO
import hashlib
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.operations.reference import LUMINANCE
from retouching_tool.pipeline.assets import ModelCatalog, ModelFile, ModelManifest
from retouching_tool.pipeline.definition import OperationDescriptor

ROOT = Path(__file__).resolve().parents[1]


class PreviewTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".test-work"
        work.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=work, prefix="preview ")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.log = Diagnostics(root / "logs", console=StringIO())
        self.addCleanup(self.log.close)
        self.source = root / "source"
        self.destination = root / "destination"
        self.source.mkdir()
        self.destination.mkdir()
        self.path = self.source / "photo.jpg"
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (640, 400), (96, 72, 48)).save(self.path, exif=exif)
        self.app = ApplicationService(root, load_config(root), self.log)
        self.app.startup(LaunchOptions((), ROOT / "examples/pipelines/reference.json", None), "directml")
        self.index = self.app.index_sources((self.source,))
        self.item = self.index.images[0]
        self.editor = self.app.pipeline_service
        self.service = self.app.preview_service

    def test_preview_preserves_orientation_and_executes_isolated_or_enabled_stages(self):
        original_bytes = self.path.read_bytes()
        thumbnail = self.service.thumbnail(self.item)
        self.assertEqual((thumbnail.width, thumbnail.height), (100, 160))
        original = self.service.picture(self.index, self.item.image_id, "original")
        self.assertEqual((original.original.width, original.original.height), (400, 640))
        self.assertIsNone(original.result)

        self.editor.set_parameter("exposure-reference", "stops", 1)
        pipeline = self.service.picture(self.index, self.item.image_id, "whole", destination=self.destination)
        self.assertEqual(pipeline.original, pipeline.result)
        self.assertEqual(pipeline.context[0].producer_id, "statistics-after")
        isolated = self.service.picture(self.index, self.item.image_id, "operation",
                                        destination=self.destination, stage_id="exposure-reference")
        self.assertGreater(isolated.result.rgb[0], isolated.original.rgb[0])
        self.assertFalse(self.editor.status.stages[1].stage.enabled)
        self.assertEqual(isolated.context, ())
        self.editor.set_enabled("exposure-reference", True)
        pipeline = self.service.picture(self.index, self.item.image_id, "enabled", destination=self.destination)
        self.assertEqual(pipeline.result, isolated.result)
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(list(self.destination.iterdir()), [])
        completed = [event for event in self.log.records if event["code"] == "STAGE_COMPLETED"]
        self.assertEqual([event["stage_instance_id"] for event in completed
                          if event["preview_request_id"] == "operation"], ["exposure-reference"])

    def test_preview_rejects_invalid_destination_dependencies_and_changed_source(self):
        with self.assertRaisesRegex(RetouchingError, "distinct"):
            self.service.picture(self.index, self.item.image_id, "invalid-folder", destination=self.source)
        self.editor.registry.register(OperationDescriptor("test.consumer", 1, "Context consumer",
                                                          requires=(LUMINANCE,)), None)
        consumer = self.editor.add_operation("test.consumer", 1).stages[-1].stage
        with self.assertRaisesRegex(RetouchingError, "Use Preview pipeline"):
            self.service.picture(self.index, self.item.image_id, "missing-context",
                                 destination=self.destination, stage_id=consumer.id)
        self.path.unlink()
        with self.assertRaisesRegex(RetouchingError, "Scan the sources again"):
            self.service.picture(self.index, self.item.image_id, "deleted")
        errors = {event["preview_request_id"]: event for event in self.log.records
                  if event.get("preview_request_id") and event.get("exception")}
        self.assertEqual(errors["invalid-folder"]["code"], "DESTINATION_IS_SOURCE")
        self.assertEqual(errors["missing-context"]["stage_instance_id"], consumer.id)
        self.assertEqual(errors["deleted"]["image_id"], self.item.image_id)
        self.assertIn("FileNotFoundError", errors["deleted"]["exception"])

    def test_whole_preview_requires_review_when_an_enabled_asset_disappears(self):
        model_path = self.editor.checkpoints / "fixture.bin"
        model_path.parent.mkdir()
        fixture = b"test-authored synthetic asset"
        model_path.write_bytes(fixture)
        self.editor.catalog = ModelCatalog((
            ModelManifest("fixture", "1", (ModelFile(model_path.name, hashlib.sha256(fixture).hexdigest()),),
                          ("directml",), "https://example.test/model", "Local test fixture"),
        ))
        self.editor.registry.register(OperationDescriptor("test.model", 1, "Asset fixture", model_refs=("fixture",)), None)
        added = self.editor.add_operation("test.model", 1, model_ref="fixture").stages[-1]
        self.assertEqual(added.state, "ready")
        model_path.unlink()

        with self.assertRaisesRegex(RetouchingError, "Review the pipeline cards"):
            self.service.picture(self.index, self.item.image_id, "missing-asset", destination=self.destination)
        status = self.editor.status.stages[-1]
        self.assertEqual(status.state, "unavailable")
        self.assertFalse(status.stage.enabled)
        self.assertTrue(any(event["code"] == "PREVIEW_PIPELINE_CHANGED" and event["preview_request_id"] == "missing-asset"
                            for event in self.log.records))
