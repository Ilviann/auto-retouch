"""Manual Levels transfer, persistence and execution contracts."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np

from retouching_tool.application.pipeline_service import PipelineService
from retouching_tool.errors import RetouchingError
from retouching_tool.imaging.transfer import srgb8_to_linear
from retouching_tool.operations.levels import DESCRIPTOR
from retouching_tool.pipeline.definition import parse_pipeline
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import PERSON_FOREGROUND, ContextEntry, ImageState, PersonForegroundMask, SourceMetadata


class LevelsTests(unittest.TestCase):
    def setUp(self):
        self.log = Mock()
        self.editor = PipelineService(Path("models"), "cpu", self.log)
        self.stage = self.editor.add_operation(DESCRIPTOR.operation, 1).stages[0].stage
        gray = np.array([0, 8, 16, 29.75, 64, 128, 180, 235, 246, 255])
        self.codes = np.stack([gray, gray[::-1], np.roll(gray, 1)], axis=-1)[None, ...]
        mask = ContextEntry(PERSON_FOREGROUND, "mask", "analysis.person_foreground", 1, 0, 2, 10, 1,
                            PersonForegroundMask(1, 1, b"\x01"), source_id="photo")
        self.state = ImageState.from_pixels(srgb8_to_linear(self.codes), SourceMetadata("photo", exif=b"capture"),
                                            context=(mask,), geometry_revision=2)

    def run_levels(self, **parameters):
        stage = self.editor.set_parameters(self.stage.id, parameters).definition.stages[0]
        runtime = StageRuntime(self.log, "cpu", Path("models"), "job", stage)
        with StageSession(self.editor.registry, self.editor.catalog, runtime) as session:
            return session.process(self.state, image_id="photo")

    def test_defaults_and_bypass(self):
        with patch("retouching_tool.operations.levels.open_session") as opened:
            self.assertIs(self.run_levels().pixels, self.state.pixels)
            self.assertIs(self.run_levels(channel="Blue").pixels, self.state.pixels)
            self.editor.set_enabled(self.stage.id, False)
            self.assertIs(self.run_levels(gamma=2).pixels, self.state.pixels)
            opened.assert_not_called()

    def test_channel_curve_clipping_gamma_and_blending(self):
        for channel in ("RGB", "Red", "Green", "Blue"):
            for gamma in (.5, 2):
                with self.subTest(channel=channel, gamma=gamma), patch(
                        "retouching_tool.operations.color_curves.CHUNK_PIXELS", 3):
                    result = self.run_levels(channel=channel, input_black=16, input_white=235,
                                             gamma=gamma, output_black=8, output_white=246, blend=.75)
                    target = slice(None) if channel == "RGB" else ("Red", "Green", "Blue").index(channel)
                    expected = self.state.pixels.copy()
                    unit = np.clip((self.codes[..., target] - 16) / 219, 0, 1)
                    mapped = srgb8_to_linear(8 + 238 * unit ** (1 / gamma))
                    expected[..., target] += .75 * (mapped - expected[..., target])
                    np.testing.assert_allclose(result.pixels, expected, rtol=2e-6, atol=2e-7)
                    if channel != "RGB":
                        untouched = [i for i in range(3) if i != target]
                        np.testing.assert_array_equal(result.pixels[..., untouched], self.state.pixels[..., untouched])
                    self.assertIs(result.metadata, self.state.metadata)
                    self.assertEqual((result.context, result.geometry_revision, result.pixel_revision),
                                     (self.state.context, 2, 1))

    def test_extreme_gamma_endpoints_inversion_and_flat_output(self):
        result = self.run_levels(input_black=16, input_white=17, gamma=10, output_black=255, output_white=0)
        np.testing.assert_array_equal(result.pixels, self.codes <= 16)
        result = self.run_levels(output_black=8, output_white=8)
        np.testing.assert_allclose(result.pixels, srgb8_to_linear(8), atol=1e-8)

    def test_invalid_edits_are_atomic_and_valid_settings_round_trip(self):
        self.run_levels(channel="Blue", input_black=32, input_white=192, gamma=1.5)
        before = self.editor.status.definition
        for name, value in (("input_black", 192), ("input_white", 10), ("gamma", 0),
                            ("output_white", 256), ("channel", "Luminance")):
            with self.subTest(name=name), self.assertRaises(RetouchingError):
                self.editor.set_parameter(self.stage.id, name, value)
            self.assertEqual(self.editor.status.definition, before)
        self.assertEqual(parse_pipeline(before.to_json(), self.editor.registry, frozen=True), before)

    def test_matched_ranges_keep_interior_pixels_exact_under_screen_blending(self):
        result = self.run_levels(channel="Red", input_black=16, input_white=235,
                                 output_black=16, output_white=235, blend_mode="screen")
        interior = (self.codes[..., 0] >= 16) & (self.codes[..., 0] <= 235)
        np.testing.assert_array_equal(result.pixels[interior], self.state.pixels[interior])
        self.assertGreater(result.pixels[0, 0, 0], self.state.pixels[0, 0, 0])

    def test_backend_failure_keeps_traceback_and_stage_identity(self):
        with (patch("retouching_tool.operations.levels.open_session", side_effect=RuntimeError("device failed")),
              self.assertRaisesRegex(RuntimeError, "device failed")):
            self.run_levels(gamma=2)
        failed = next(c.kwargs for c in self.log.emit.call_args_list if c.args[0] == "STAGE_PROCESS_FAILED")
        self.assertEqual((failed["exc_info"], failed["job_id"], failed["image_id"], failed["stage_instance_id"]),
                         (True, "job", "photo", self.stage.id))
