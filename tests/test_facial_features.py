"""Facial landmark coordinates, conservative fallbacks, and the shine consumer."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

import numpy as np

from retouching_tool.application.pipeline_service import PipelineService
from retouching_tool.application.staged_jobs import StagedRunner
from retouching_tool.errors import RetouchingError
from retouching_tool.operations import facial_features as features
from retouching_tool.operations import skin_shine
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.definition import PipelineDefinition, StageDefinition, parse_pipeline
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import (
    EXPOSED_SKIN, FACE_REGIONS, FACIAL_FEATURES, ContextEntry, ExposedSkinMask, FaceRegion,
    FaceRegions, ImageState, SourceMetadata,
)
from retouching_tool.pipeline.registry import OperationRegistry, application_registry
from retouching_tool.storage.artifact_codec import decode_context, encode_context
from retouching_tool.storage.artifacts import MANIFEST_LIMIT


class FacialFeatureTests(unittest.TestCase):
    def setUp(self):
        self.stage = StageDefinition("protect", features.DESCRIPTOR.operation, 1, True, model_ref=features.MODEL.ref)
        self.op = features.ProtectFacialFeatures(self.stage)
        self.op.session, self.op.runtime = Mock(), Mock()
        self.points = np.full((478, 3), 128, np.float32)
        for indices, (x, y) in zip(features.CONTOURS, ((95, 110), (162, 110), (95, 98), (162, 98), (128, 160))):
            angle = np.arange(len(indices)) * (2 * np.pi / len(indices))
            self.points[list(indices), :2] = np.column_stack((x + 12 * np.cos(angle), y + 7 * np.sin(angle)))
        self.success = [self.points.reshape(1, 1, 1, 1434), np.ones((1, 1, 1, 1), np.float32)]
        self.uncertain = [self.success[0], -self.success[1]]
        pixels = np.full((96, 256, 3), [.36, .21, .13], np.float32)
        # Include the lip, cheek, uncertain face, undetected facial skin and body skin.
        for x, y in ((62, 60), (62, 69), (150, 60), (203, 60), (235, 60)):
            pixels[y-1:y+2, x-1:x+2] += .21
        self.regions = (FaceRegion(30, 16, 94, 80), FaceRegion(122, 16, 186, 80))
        faces = ContextEntry(FACE_REGIONS, "det", "fixture", 1, 0, 0, 256, 96, FaceRegions(self.regions),
                             source_id="image", producer_settings_hash="a"*64, model_ref="fixture")
        labels = np.full((96, 256), 2, np.uint8)
        labels[:, 220:] = 1
        skin = replace(faces, spec=EXPOSED_SKIN, value=ExposedSkinMask(256, 96, labels.tobytes()))
        self.image = ImageState.from_pixels(pixels, SourceMetadata("image", b"exif"), context=(faces, skin))

    def shine(self, state, **settings):
        stage = StageDefinition("shine", skin_shine.DESCRIPTOR.operation, 1, True, tuple(settings.items()))
        runtime = StageRuntime(Mock(), "cpu", Path("models"), "job", stage)
        with StageSession(application_registry(), ModelCatalog(), runtime) as session:
            return session.process(state, image_id="image")

    def test_landmarks_map_to_oriented_pixels_and_survive_replay(self):
        self.op.session.run.side_effect = [self.success, self.uncertain]
        state = self.op.process(self.image)
        self.assertIs(state.pixels, self.image.pixels)
        self.assertIs(state.metadata, self.image.metadata)
        entry = state.entry(FACIAL_FEATURES.key)
        first, second = entry.value.faces
        self.assertEqual(first.box, (30, 16, 94, 80))
        self.assertEqual(first.contours[-1][:2], (66, 60))
        self.assertEqual(second.contours, ())
        self.assertEqual(self.op.session.run.call_count, 2)
        self.assertTrue(any(c.args[0] == "FACIAL_FEATURES_UNCERTAIN" for c in self.op.runtime.emit.call_args_list))
        data = json.loads(json.dumps(encode_context(entry)))
        replay = decode_context(data)
        self.assertEqual(replay, entry)
        data["width"] = 65
        with self.assertRaises(ValueError):
            decode_context(data)
        data["width"] = state.width
        data["value"]["data"][0]["contours"][0] = "AA=="
        with self.assertRaises(ValueError):
            decode_context(data)
        state = replace(state, context=(*self.image.context, replay))
        protected = self.shine(state).pixels
        unprotected = self.shine(state, protect_features=False).pixels
        np.testing.assert_array_equal(unprotected, self.shine(self.image).pixels)
        for x, y in ((62, 60), (150, 60), (203, 60)):
            np.testing.assert_array_equal(protected[y, x], state.pixels[y, x])
            self.assertLess(unprotected[y, x, 0], protected[y, x, 0])
        for x, y in ((62, 69), (235, 60)):
            self.assertLess(protected[y, x, 0], state.pixels[y, x, 0])
        with self.assertRaisesRegex(RetouchingError, "Regenerate"):
            self.shine(replace(state, context=(*self.image.context, replace(entry, source_id="other"))))

        maximum = replace(entry, value=replace(entry.value, faces=(first,) * 256))
        encoded = json.dumps({"context": [encode_context(maximum)]}, indent=2)
        self.assertLess(len(encoded), MANIFEST_LIMIT // 2)
        self.assertEqual(decode_context(json.loads(encoded)["context"][0]), maximum)

    def test_legacy_shine_checkpoint_identity(self):
        previous = replace(skin_shine.DESCRIPTOR, optional=(), parameters=(skin_shine.DESCRIPTOR.parameters[0],))
        legacy = OperationRegistry()
        legacy.register(previous, None)
        data = PipelineDefinition("Studio", (StageDefinition("shine", previous.operation, 1, True),)).to_json()
        def identity(registry):
            pipeline = parse_pipeline(data, registry, frozen=True)
            runner = StagedRunner(Mock(), None, Mock(pipeline=pipeline, pytorch_backend="cpu"), None)
            runner.registry = registry
            return pipeline.to_json(), runner._prefix_digests()
        self.assertEqual(identity(application_registry()), identity(legacy))

    def test_empty_detections_and_unreliable_or_invalid_predictions(self):
        faces, skin = self.image.context
        empty = self.op.process(replace(self.image, context=(replace(faces, value=FaceRegions(())), skin)))
        self.op.session.run.assert_not_called()
        edited = self.shine(empty).pixels
        np.testing.assert_array_equal(edited[:, :200], empty.pixels[:, :200])
        self.assertLess(edited[60, 235, 0], empty.pixels[60, 235, 0])
        self.op.session.run.return_value = self.success
        self.points[1, 0] = -100  # Mesh belongs outside the selected detection.
        uncertain = self.op.process(self.image).entry(FACIAL_FEATURES.key)
        self.assertTrue(all(not face.contours for face in uncertain.value.faces))
        self.points[1, 0] = np.nan
        with self.assertRaisesRegex(RetouchingError, "invalid landmarks"):
            self.op.process(self.image)
        self.op.session.run.side_effect = OSError("device lost")
        with self.assertRaises(RetouchingError) as failed:
            self.op.process(self.image)
        self.assertIsInstance(failed.exception.__cause__, OSError)

    def test_missing_file_and_dependency_readiness(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            editor = PipelineService(root, "directml", Mock())
            status, = editor.add_operation(features.DESCRIPTOR.operation, 1, model_ref=features.MODEL.ref).stages
            self.assertFalse(status.stage.enabled)
            self.assertEqual(status.model_page, features.PUBLISHER)
            self.assertEqual(status.issues[0].path, root / features.MODEL.files[0].relative_path)
            file = status.issues[0].path
            file.parent.mkdir(parents=True)
            file.write_bytes(b"wrong model")
            self.assertEqual(editor.recheck().stages[0].issues[0].code, "MODEL_CHECKSUM_INVALID")
            verified = replace(features.MODEL.files[0], sha256=hashlib.sha256(file.read_bytes()).hexdigest())
            editor.catalog = ModelCatalog((replace(features.MODEL, files=(verified,)),))
            self.assertEqual(editor.recheck().stages[0].state, "skipped")
            enabled, = editor.set_enabled(status.stage.id, True).stages
            self.assertEqual(enabled.state, "blocked")
            self.assertIn(FACE_REGIONS.key, enabled.dependency_errors[0])
