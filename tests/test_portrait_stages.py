"""Color correction, fixed reference reuse and YuNet region contracts."""

from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from retouching_tool.application.pipeline_service import PipelineService
from retouching_tool.application.preview import DisplayImage, _face_overlay
from retouching_tool.application.staged_jobs import StagedRunner
from retouching_tool.errors import RetouchingError
from retouching_tool.operations import alibaba_skin, face_detection as detector, white_balance as balance
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.definition import PipelineDefinition, StageDefinition, parse_pipeline
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import FACE_REGIONS, FaceRegion, ImageState, SourceMetadata
from retouching_tool.pipeline.registry import OperationRegistry, application_registry
from retouching_tool.storage.artifact_codec import decode_context, encode_context


ROOT = Path(__file__).resolve().parents[1]


class PortraitStageTests(unittest.TestCase):
    def setUp(self):
        self.log = Mock()
        self.registry = application_registry()

    def state(self, pixels):
        return ImageState.from_pixels(np.array(pixels, dtype=np.float32), SourceMetadata("photo", b"exif"))

    def balance(self, state, **settings):
        stage = StageDefinition("wb", balance.DESCRIPTOR.operation, 1, True, tuple(settings.items()))
        runtime = StageRuntime(self.log, "directml", ROOT / "models", "job", stage)
        with StageSession(self.registry, ModelCatalog(), runtime) as session:
            return session.process(state, image_id="photo")

    def test_automatic_cast_and_ambiguous_color(self):
        opener = self.enterContext(patch.object(balance, "open_session"))
        ramp = np.repeat(np.linspace(.05, .65, 60)[:, None, None], 80, axis=1)
        for cast in ((1.18, 1, .92), (.92, 1, 1.18), (1, 1.12, 1)):
            state = self.state(ramp * cast)
            result = self.balance(state)
            gray = state.pixels @ np.array([.2126, .7152, .0722])
            for channel in range(3):
                np.testing.assert_allclose(result.pixels[:, :, channel], gray, rtol=1e-6)
            self.assertIs(result.metadata, state.metadata)
            self.assertEqual(result.pixel_revision, 1)
        for pixels in (np.full((12, 12, 3), [.9, .3, .3]), np.ones((12, 12, 3)),
                       np.full((12, 12, 3), [.27, .25, .23])):
            state = self.state(pixels)
            self.assertIs(self.balance(state).pixels, state.pixels)
            event = next(call for call in reversed(self.log.emit.call_args_list)
                         if call.args[0] == "WHITE_BALANCE_UNCERTAIN")
            self.assertEqual((event.kwargs["image_id"], event.kwargs["level"]), ("photo", "WARNING"))
        opener.assert_not_called()

    def test_sampled_patch_reused_on_a_different_background_and_highlight_protection(self):
        pixels = np.full((25, 25, 3), [.6, .05, .09])
        pixels[:7, :7] = [.45, .25, .18]
        pixels[1, 1] = 1
        settings = balance.sample_neutral(self.state(pixels), 1, 1)
        pixels[7:] = [.12, .24, .58]
        state = self.state(pixels)
        result = self.balance(state, **settings)
        neutral = result.pixels[0, 0]
        self.assertLess(np.ptp(neutral), .00002)
        self.assertAlmostEqual(neutral[0], np.dot(pixels[0, 0], [.2126, .7152, .0722]), places=4)
        np.testing.assert_array_equal(result.pixels[1, 1], state.pixels[1, 1])
        mixed = self.balance(state, **settings, blend=.5)
        np.testing.assert_allclose(mixed.pixels, (state.pixels + result.pixels) / 2, atol=1e-7)
        for sample in (np.ones((3, 3, 3)), np.zeros((3, 3, 3))):
            with self.assertRaisesRegex(RetouchingError, "neutral patch"):
                balance.sample_neutral(self.state(sample), 0, 0)

    def test_fixed_session_reused_across_chunk_boundaries_and_different_sizes(self):
        stage = StageDefinition("wb", balance.DESCRIPTOR.operation, 1, True,
                                (("mode", "Fixed"), ("red_stops", .25), ("blue_stops", -.2)))
        runtime = StageRuntime(self.log, "cpu", ROOT / "models", "job", stage)
        with (patch("retouching_tool.operations.color_curves.CHUNK_PIXELS", 7),
              patch.object(balance, "open_session", wraps=balance.open_session) as opener,
              StageSession(self.registry, ModelCatalog(), runtime) as session):
            operation = session.operation
            for width in (3, 11):
                state = self.state(np.full((2, width, 3), [.35, .24, .16]))
                result = session.process(state, image_id="photo")
                self.assertGreater(result.pixels[0, 0, 0], state.pixels[0, 0, 0])
                np.testing.assert_array_equal(result.pixels, np.broadcast_to(result.pixels[0, 0], state.pixels.shape))
        opener.assert_called_once()
        self.assertEqual(opener.call_args.args[1], "cpu")
        self.assertIsNone(operation.session)

    def test_detection_maps_suppresses_duplicates_ignores_padding_and_round_trips(self):
        stage = StageDefinition("det", detector.DESCRIPTOR.operation, 1, True,
                                (("threshold", .8), ("expand_pixels", 0)), detector.MODEL.ref)
        operation = detector.FaceDetection(stage)
        state = self.state(np.zeros((100, 200, 3)))
        arrays = []
        for stride in detector.STRIDES:
            n = (detector.SIZE // stride)**2
            arrays.extend(np.zeros((1, n, ch), np.float32) for ch in (1, 1, 4))
        side = detector.SIZE // 8
        for row, col, score, offset in ((10, 10, 1., 0), (10, 11, .95, -1), (20, 50, .9, 0),
                                        (12, 30, .7, 0), (60, 70, 1., 0)):
            idx = row * side + col
            arrays[0][0, idx] = arrays[1][0, idx] = score
            arrays[2][0, idx] = [offset, 0, 0, 0]
        operation.runtime = Mock()
        operation.input_name = "input"
        operation.session = Mock()
        operation.session.run.return_value = arrays
        result = operation.process(state)
        self.assertIs(result.pixels, state.pixels)
        entry = result.entry(FACE_REGIONS.key)
        self.assertEqual(decode_context(encode_context(entry)), entry)
        self.assertEqual(entry.value.regions, (FaceRegion(23, 23, 27, 27), FaceRegion(123, 48, 127, 52)))
        display = DisplayImage(200, 100, bytes(200 * 100 * 3))
        overlay = _face_overlay(display, entry.value.regions)
        samples = np.frombuffer(overlay.rgb, np.uint8).reshape(100, 200, 3)
        np.testing.assert_array_equal(samples[23, 23], (0, 224, 160))
        self.assertEqual(samples[0, 0].sum(), 0)
        operation.stage = replace(stage, parameters=(("threshold", .8), ("expand_pixels", 60)))
        expanded = operation.process(state).entry(FACE_REGIONS.key)
        self.assertEqual(expanded.value.regions, (FaceRegion(0, 0, 87, 87), FaceRegion(63, 0, 187, 100)))
        self.assertEqual(decode_context(encode_context(expanded)), expanded)
        self.assertNotEqual(entry.producer_settings_hash, expanded.producer_settings_hash)
        operation.stage = replace(stage, parameters=(("threshold", .8), ("expand_pixels", 200)))
        self.assertEqual(operation.process(state).entry(FACE_REGIONS.key).value.regions,
                         (FaceRegion(0, 0, 200, 100),) * 2)
        arrays[0].fill(0)
        empty = operation.process(state)
        self.assertFalse(empty.entry(FACE_REGIONS.key).value.regions)
        self.assertEqual(operation.runtime.emit.call_args.args[0], "FACE_DETECTION_EMPTY")
        arrays[0][0, 0] = np.nan
        with self.assertRaisesRegex(RetouchingError, "invalid face detection tensors"):
            operation.process(state)
        operation.session.run.side_effect = OSError("simulated device error")
        with self.assertRaises(RetouchingError) as failed:
            operation.process(state)
        self.assertIsInstance(failed.exception.__cause__, OSError)

    def test_installed_yunet_on_directml(self):
        root = ROOT / "models"
        if not (root / detector.MODEL.files[0].relative_path).is_file():
            self.skipTest("Local March 2023 YuNet checkpoint required")
        stage = StageDefinition("face", detector.DESCRIPTOR.operation, 1, True, model_ref=detector.MODEL.ref)
        runtime = StageRuntime(self.log, "directml", root, "job", stage)
        with StageSession(self.registry, ModelCatalog((detector.MODEL,)), runtime) as session:
            state = self.state(np.zeros((64, 128, 3)))
            result = session.process(state, image_id="photo")
            self.assertIs(result.pixels, state.pixels)
            self.assertEqual(result.entry(FACE_REGIONS.key).value.regions, ())
        warning = next(call for call in self.log.emit.call_args_list if call.args[0] == "FACE_DETECTION_EMPTY")
        self.assertEqual((warning.kwargs["image_id"], warning.kwargs["job_id"]), ("photo", "job"))

    def test_face_settings_validate_and_preserve_legacy_checkpoint_identity(self):
        legacy_registry = OperationRegistry()
        for module, added in ((detector, "expand_pixels"), (alibaba_skin, "use_face_rects")):
            desc = module.DESCRIPTOR
            legacy_registry.register(replace(desc, parameters=tuple(p for p in desc.parameters if p.name != added)), None)

        raw = PipelineDefinition("Studio", tuple(StageDefinition(m.MODEL.ref, m.DESCRIPTOR.operation, 1, True,
            model_ref=m.MODEL.ref) for m in (detector, alibaba_skin))).to_json()
        legacy = parse_pipeline(raw, legacy_registry)
        current = parse_pipeline(legacy.to_json(), self.registry, frozen=True)
        self.assertEqual(current.to_json(), legacy.to_json())
        self.assertEqual([s.identity for s in current.stages], [s.identity for s in legacy.stages])

        def digests(pipeline, registry):
            runner = StagedRunner(Mock(), None, Mock(pipeline=pipeline, pytorch_backend="cpu"), None)
            runner.registry = registry
            return runner._prefix_digests()

        self.assertEqual(digests(current, self.registry), digests(legacy, legacy_registry))
        for index, key, bad in ((0, "expand_pixels", (-1, 1.5, True, "2")),
                                (1, "use_face_rects", (0, 1, "false", None))):
            for value in bad:
                data = current.to_json()
                data["stages"][index]["parameters"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(RetouchingError):
                    parse_pipeline(data, self.registry, frozen=True)
        updated = current.to_json()
        updated["stages"][0]["parameters"]["expand_pixels"] = 30
        updated["stages"][1]["parameters"]["use_face_rects"] = False
        parsed = parse_pipeline(updated, self.registry, frozen=True)
        self.assertEqual(parsed.to_json(), updated)
        self.assertNotEqual(digests(current, self.registry), digests(parsed, self.registry))

    def test_detector_missing_wrong_and_recovered_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            editor = PipelineService(folder, "directml", self.log)
            stage, = editor.add_operation(detector.DESCRIPTOR.operation, 1, model_ref=detector.MODEL.ref).stages
            file, = detector.MODEL.files
            self.assertFalse(stage.stage.enabled)
            self.assertEqual(stage.issues[0].path, folder / file.relative_path)
            self.assertEqual(stage.model_page, detector.MODEL.publisher_url)
            (folder / file.relative_path).write_bytes(b"fixture")
            self.assertEqual(editor.recheck().stages[0].issues[0].code, "MODEL_CHECKSUM_INVALID")
            verified = replace(file, sha256=hashlib.sha256(b"fixture").hexdigest())
            editor.catalog = ModelCatalog((replace(detector.MODEL, files=(verified,)),))
            self.assertFalse(editor.recheck().stages[0].stage.enabled)
            enabled, = editor.set_enabled(stage.stage.id, True).stages
            self.assertTrue(enabled.stage.enabled)
