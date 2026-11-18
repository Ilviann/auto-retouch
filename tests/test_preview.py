"""Focused preview checks using a generated JPEG and the reference operations."""

from io import StringIO
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.operations.reference import LUMINANCE, ReferenceOperation
from retouching_tool.pipeline.assets import ModelCatalog, ModelFile, ModelManifest
from retouching_tool.pipeline.definition import OperationDescriptor

ROOT = Path(__file__).resolve().parents[1]


class ContextConsumer(ReferenceOperation):
    def process(self, state):
        return state


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
        self.source.mkdir()
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
        with patch.object(self.app.batch_service, "preflight") as preflight:
            pipeline = self.service.picture(self.index, self.item.image_id, "whole", process=True)
            self.assertEqual(pipeline.original, pipeline.result)
            self.assertEqual(pipeline.context[0].producer_id, "statistics-after")
            isolated = self.service.picture(self.index, self.item.image_id, "operation", stage_id="exposure-reference")
            self.assertGreater(isolated.result.rgb[0], isolated.original.rgb[0])
            self.assertFalse(self.editor.status.stages[1].stage.enabled)
            self.assertEqual(isolated.context, ())
            self.editor.set_enabled("exposure-reference", True)
            saved_at = self.editor.autosave.stat().st_mtime_ns
            pipeline = self.service.picture(self.index, self.item.image_id, "enabled", process=True)
            preflight.assert_not_called()
        self.assertEqual(pipeline.result, isolated.result)
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(list(self.source.parent.rglob("*.jpg")), [self.path])
        self.assertFalse(self.app.config.temporary.exists())
        self.assertEqual(self.editor.autosave.stat().st_mtime_ns, saved_at)
        completed = [event for event in self.log.records if event["code"] == "STAGE_COMPLETED"]
        self.assertEqual([event["stage_instance_id"] for event in completed
                          if event["preview_request_id"] == "operation"], ["exposure-reference"])

    def test_preview_rejects_missing_dependencies_and_changed_source(self):
        self.editor.registry.register(OperationDescriptor("test.consumer", 1, "Context consumer",
                                                          requires=(LUMINANCE,)), ContextConsumer)
        consumer = self.editor.add_operation("test.consumer", 1).stages[-1].stage
        with self.assertRaisesRegex(RetouchingError, "Use Preview pipeline"):
            self.service.picture(self.index, self.item.image_id, "missing-context", stage_id=consumer.id)
        comparison = self.service.picture(self.index, self.item.image_id, "with-context",
                                          stage_id=consumer.id, through_stage=True)
        self.assertEqual(comparison.original, comparison.result)
        self.assertEqual(comparison.context[0].producer_id, "statistics-after")
        self.path.unlink()
        with self.assertRaisesRegex(RetouchingError, "Scan the sources again"):
            self.service.picture(self.index, self.item.image_id, "deleted")
        errors = {event["preview_request_id"]: event for event in self.log.records
                  if event.get("preview_request_id") and event.get("exception")}
        self.assertEqual(errors["missing-context"]["stage_instance_id"], consumer.id)
        self.assertEqual(errors["deleted"]["image_id"], self.item.image_id)
        self.assertIn("FileNotFoundError", errors["deleted"]["exception"])

    def test_stage_comparison_uses_enabled_prefix_and_immutable_draft(self):
        data = self.editor.status.definition.to_json()
        selected = data["stages"][1]
        data["stages"] = [
            dict(selected, id="upstream", enabled=True, parameters={"stops": 1}),
            dict(selected, id="upstream-disabled", parameters={"stops": 3}),
            selected,
            dict(selected, id="downstream", enabled=True, parameters={"stops": 3}),
        ]
        definition = self.editor.load_data(data).definition
        persisted = self.editor.autosave.read_bytes()
        saved_at = self.editor.autosave.stat().st_mtime_ns
        self.assertFalse(selected["enabled"])
        comparison = self.service.stage_comparison(self.index, self.item.image_id, selected["id"], through_stage=True)
        before = self.service.picture(self.index, self.item.image_id, "isolated-upstream",
                                      stage_id="upstream").result
        first = comparison.render("first-draft", {"stops": 0.5})
        second = comparison.render("second-draft", {"stops": 1})
        self.assertEqual(before, first.original)
        self.assertEqual(first.original, second.original)
        self.assertLess(before.rgb[0], first.result.rgb[0])
        self.assertLess(first.result.rgb[0], second.result.rgb[0])
        completed = [event for event in self.log.records if event["code"] == "STAGE_COMPLETED"]
        for request, expected in (("first-draft", ["upstream", selected["id"]]), ("second-draft", [selected["id"]])):
            self.assertEqual([event["stage_instance_id"] for event in completed
                              if event["preview_request_id"] == request], expected)
        with self.assertRaisesRegex(RetouchingError, "finite number"):
            comparison.render("invalid-draft", {"stops": 10})
        self.assertEqual(self.editor.status.definition, definition)
        self.assertEqual(self.editor.autosave.read_bytes(), persisted)
        self.assertEqual(self.editor.autosave.stat().st_mtime_ns, saved_at)
        self.assertFalse(self.app.config.temporary.exists())

        self.path.unlink()
        with self.assertRaisesRegex(RetouchingError, "Scan the sources again"):
            comparison.render("stale-baseline", {"stops": 2})

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
            self.service.picture(self.index, self.item.image_id, "missing-asset", process=True)
        status = self.editor.status.stages[-1]
        self.assertEqual(status.state, "unavailable")
        self.assertFalse(status.stage.enabled)
        self.assertTrue(any(event["code"] == "PREVIEW_PIPELINE_CHANGED" and event["preview_request_id"] == "missing-asset"
                            for event in self.log.records))
