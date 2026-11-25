"""Check the Gaussian pair through the registered, headless stage lifecycle."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np

from retouching_tool.errors import RetouchingError
from retouching_tool.operations.frequency_filters import HIGH_PASS, LOW_PASS, TILE_HEIGHT, TILE_WIDTH
from retouching_tool.operations.reference import STATISTICS
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.definition import StageDefinition
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ImageState, SourceMetadata
from retouching_tool.pipeline.registry import application_registry


class FrequencyFilterTests(unittest.TestCase):
    def setUp(self):
        self.registry = application_registry()
        self.log = Mock()
        self.metadata = SourceMetadata("photo", exif=b"capture")

    def process(self, state, descriptor, *, backend="cpu", **parameters):
        stage = StageDefinition("filter", descriptor.operation, 1, True, tuple(parameters.items()))
        device = descriptor.execution_backend(backend, "cpu")
        runtime = StageRuntime(self.log, device, Path("models"), "job", stage)
        with StageSession(self.registry, ModelCatalog(), runtime) as session:
            return session.process(state, image_id="photo")

    def test_impulse_across_tile_boundaries_matches_gaussian_in_each_channel(self):
        y0, x0 = TILE_HEIGHT, TILE_WIDTH
        pixels = np.full((y0 + 8, x0 + 9, 3), (0.25, 0.35, 0.1), dtype=np.float32)
        impulse = np.array([0.375, -0.25, 0], dtype=np.float32)
        pixels[y0, x0] += impulse
        state = ImageState.from_pixels(pixels, self.metadata, geometry_revision=2)
        state = self.process(state, STATISTICS)

        # Independent 2D impulse response for sigma = 1.5, support ±5.
        x, y = np.mgrid[-5:6, -5:6]
        weights = np.exp(-(x**2 + y**2) / (2 * 1.5**2))
        expected = np.broadcast_to(pixels[0, 0], pixels.shape).copy()
        expected[y0 - 5:y0 + 6, x0 - 5:x0 + 6] += (weights / weights.sum())[:, :, None] * impulse
        for descriptor, target in ((LOW_PASS, expected), (HIGH_PASS, 0.5 + (pixels - expected))):
            with self.subTest(stage=descriptor.title):
                result = self.process(state, descriptor, radius=1.5)
                np.testing.assert_allclose(result.pixels, target, atol=6e-8, rtol=0)
                np.testing.assert_array_equal(state.pixels, pixels)
                self.assertIs(result.metadata, self.metadata)
                self.assertEqual((result.geometry_revision, result.pixel_revision, result.context),
                                 (2, 1, state.context))
                self.assertFalse(result.pixels.flags.writeable)

    def test_borders_on_single_row_and_overlay_sharpening(self):
        levels = np.array([.2, .2, .8, .8], dtype=np.float32)
        pixels = np.tile(levels[None, :, None], (1, 1, 3))
        state = ImageState.from_pixels(pixels, self.metadata)
        low = self.process(state, LOW_PASS).pixels[0, :, 0]
        self.assertTrue(np.all(np.diff(low) > 0))
        self.assertGreater(low[0], .2)
        self.assertLess(low[0], .5)
        np.testing.assert_allclose(low + low[::-1], 1)
        sharpened = self.process(state, HIGH_PASS, blend_mode="overlay").pixels[0, :, 0]
        self.assertLess(sharpened[1], levels[1])
        self.assertGreater(sharpened[2], levels[2])
        self.assertIs(self.process(state, HIGH_PASS, blend=0).pixels, state.pixels)

    def test_single_pixel_and_clipped_residuals(self):
        color = np.array([[[.021, .51, .88]]], dtype=np.float32)
        state = ImageState.from_pixels(color, self.metadata)
        np.testing.assert_array_equal(self.process(state, LOW_PASS, radius=32).pixels, color)
        np.testing.assert_array_equal(self.process(state, HIGH_PASS, radius=32).pixels, 0.5)
        pixels = np.zeros((5, 5, 3), dtype=np.float32)
        pixels[:, :, 1] = 1
        pixels[2, 2] = (1, 0, 0)
        result = self.process(ImageState.from_pixels(pixels, self.metadata), HIGH_PASS)
        np.testing.assert_array_equal(result.pixels[2, 2], (1, 0, 0.5))

    def test_low_pass_screen_keeps_flat_neighborhoods_in_a_nonuniform_image(self):
        pixels = np.full((9, 233, 3), (.021, .51, .88), dtype=np.float32)
        pixels[:, 120:] = 0.25
        state = ImageState.from_pixels(pixels, self.metadata)
        filtered = self.process(state, LOW_PASS, radius=32, blend_mode="screen")
        np.testing.assert_array_equal(filtered.pixels[:, :24], pixels[:, :24])
        np.testing.assert_array_equal(filtered.pixels[:, 216:], pixels[:, 216:])
        self.assertFalse(np.array_equal(filtered.pixels[:, 120], pixels[:, 120]))

    def test_missing_selected_provider_fails_preparation_without_another_session(self):
        state = ImageState.from_pixels(np.ones((1, 1, 3), dtype=np.float32), self.metadata)
        with (patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]),
              patch("onnxruntime.InferenceSession") as inference):
            with self.assertRaises(RetouchingError) as error:
                self.process(state, LOW_PASS, backend="directml")
        self.assertEqual(error.exception.code, "AI_PROVIDER_UNAVAILABLE")
        self.assertIn("DmlExecutionProvider", str(error.exception))
        inference.assert_not_called()
        failed = next(call.kwargs for call in self.log.emit.call_args_list if call.args[0] == "STAGE_PREPARE_FAILED")
        self.assertTrue(failed["exc_info"])
        self.assertEqual((failed["backend"], failed["operation"]), ("directml", LOW_PASS.operation))

    def test_invalid_radius_and_correlated_failure(self):
        state = ImageState.from_pixels(np.ones((1, 1, 3), dtype=np.float32), self.metadata)
        for descriptor in (HIGH_PASS, LOW_PASS):
            for radius in (0, 33, float("nan")):
                with self.assertRaises(RetouchingError) as error:
                    self.process(state, descriptor, radius=radius)
                self.assertEqual(error.exception.code, "PARAMETER_INVALID")
            with patch("retouching_tool.operations.frequency_filters.open_session") as open_session:
                open_session.return_value.run.side_effect = MemoryError("simulated tile allocation failure")
                with self.assertRaisesRegex(MemoryError, "simulated tile allocation failure"):
                    self.process(state, descriptor)
            failed = next(call.kwargs for call in reversed(self.log.emit.call_args_list)
                          if call.args[0] == "STAGE_PROCESS_FAILED")
            self.assertTrue(failed["exc_info"])
            self.assertEqual((failed["operation"], failed["stage_instance_id"], failed["image_id"],
                              failed["job_id"], failed["backend"]), (descriptor.operation, "filter", "photo", "job", "cpu"))

    def test_each_setup_backend_is_forwarded_and_session_is_reused_and_released(self):
        state = ImageState.from_pixels(np.ones((1, 2, 3), dtype=np.float32), self.metadata)
        for descriptor in (LOW_PASS, HIGH_PASS):
            for backend in ("directml", "cuda", "cpu"):
                stage = StageDefinition("filter", descriptor.operation, 1, True)
                runtime = StageRuntime(self.log, descriptor.execution_backend(backend, "cpu"), Path("models"), "job", stage)
                with patch("retouching_tool.operations.frequency_filters.open_session") as open_session:
                    open_session.return_value.run.return_value = [np.ones((1, 3, 1, 2), dtype=np.float32)]
                    with StageSession(self.registry, ModelCatalog(), runtime) as session:
                        operation = session.operation
                        session.process(state, image_id="first")
                        session.process(state, image_id="second")
                    open_session.assert_called_once()
                    self.assertEqual(open_session.call_args.args[1], backend)
                    self.assertEqual(open_session.return_value.run.call_count, 2)
                    self.assertIsNone(operation.session)
