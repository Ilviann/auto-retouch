"""Focused tone-curve and image-state checks on generated linear RGB data."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np

from retouching_tool.operations.auto_exposure import DESCRIPTOR
from retouching_tool.operations.reference import STATISTICS
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.definition import StageDefinition
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import (
    PERSON_FOREGROUND, ContextEntry, ImageState, PersonForegroundMask, SourceMetadata,
)
from retouching_tool.pipeline.registry import application_registry


class AutoExposureTests(unittest.TestCase):
    def setUp(self):
        self.log = Mock()
        self.registry = application_registry()
        self.metadata = SourceMetadata("fixture", exif=b"capture EXIF")

    def process(self, state, descriptor=DESCRIPTOR, **parameters):
        stage = StageDefinition("test-stage", descriptor.operation, descriptor.version, True, tuple(parameters.items()))
        runtime = StageRuntime(self.log, "directml", Path("models"), "test-job", stage)
        with StageSession(self.registry, ModelCatalog(), runtime) as session:
            return session.process(state, image_id="test-photo")

    def event(self, code):
        return [call.kwargs for call in self.log.emit.call_args_list if call.args[0] == code][-1]

    def test_dark_photo_respects_limit_preserves_color_and_invalidates_analysis(self):
        pixels = np.full((130, 5, 3), (0.08, 0.05, 0.03), dtype=np.float32)
        pixels[0, :4] = [(0, 0, 0), (1, 0.9, 0.7), (0.98, 0.9, 0.8), (0.95, 0.9, 0.8)]
        foreground = ContextEntry(PERSON_FOREGROUND, "foreground", "analysis.person_foreground", 1, 0, 2, 5, 130,
                                  PersonForegroundMask(1, 1, b"\x01"), source_id=self.metadata.source_id)
        original = ImageState.from_pixels(pixels, self.metadata, geometry_revision=2, context=(foreground,))
        measured = self.process(original, STATISTICS)
        edited = self.process(measured, strength=0.5, max_stops=0.5)

        event = self.event("AUTO_EXPOSURE_APPLIED")
        self.assertEqual(event["applied_stops"], 0.25)
        self.assertEqual((event["job_id"], event["image_id"], event["stage_instance_id"]),
                         ("test-job", "test-photo", "test-stage"))
        self.assertGreater(float(edited.pixels[-1, -1, 0]), float(pixels[-1, -1, 0]))
        np.testing.assert_array_equal(edited.pixels[0, :2], pixels[0, :2])
        ratios = edited.pixels[0, 2:] / pixels[0, 2:]
        np.testing.assert_allclose(ratios, np.broadcast_to(ratios[:, :1], ratios.shape), rtol=2e-7)
        self.assertGreater(edited.pixels[0, 2, 0], edited.pixels[0, 3, 0])
        self.assertLess(edited.pixels[0, 2, 0], 1)
        np.testing.assert_array_equal(measured.pixels, pixels)
        self.assertEqual((edited.geometry_revision, edited.pixel_revision, edited.context), (2, 1, ()))
        self.assertIs(measured.entry(PERSON_FOREGROUND.key), foreground)
        self.assertIsNotNone(measured.entry("image.luminance"))
        self.assertIs(edited.metadata, self.metadata)
        self.assertFalse(edited.pixels.flags.writeable)

    def test_bright_midtone_moves_down_and_in_range_or_zero_controls_are_identity(self):
        bright = ImageState.from_pixels(np.full((1, 1, 3), 0.55, dtype=np.float32), self.metadata)
        tuned = self.process(bright, strength=1, max_stops=2)
        self.assertAlmostEqual(float(tuned.pixels[0, 0, 0]), 0.30, places=3)
        self.assertLess(self.event("AUTO_EXPOSURE_APPLIED")["applied_stops"], 0)

        balanced = ImageState.from_pixels(np.array([[[0.04] * 3, [0.50] * 3]], dtype=np.float32), self.metadata)
        self.assertIs(self.process(balanced).pixels, balanced.pixels)
        self.assertAlmostEqual(self.event("AUTO_EXPOSURE_UNCHANGED")["median_luminance"], 0.27, places=3)
        for parameters in ({"strength": 0}, {"max_stops": 0}):
            with self.subTest(parameters=parameters):
                result = self.process(bright, **parameters)
                self.assertIs(result.pixels, bright.pixels)
                self.assertEqual(result.pixel_revision, bright.pixel_revision)

    def test_sparse_midtones_warn_and_processing_failure_keeps_cause(self):
        pixels = np.zeros((10, 10, 3), dtype=np.float32)
        pixels[:5] = 1
        pixels[0, 0] = 0.5
        state = ImageState.from_pixels(pixels, self.metadata)
        self.assertIs(self.process(state).pixels, state.pixels)
        event = self.event("AUTO_EXPOSURE_INSUFFICIENT_MIDTONES")
        self.assertEqual((event["level"], event["usable_fraction"], event["image_id"]), ("WARNING", 0.01, "test-photo"))

        with patch("retouching_tool.operations.auto_exposure._measure_midtones",
                   side_effect=MemoryError("simulated histogram allocation failure")):
            with self.assertRaisesRegex(MemoryError, "simulated histogram allocation failure"):
                self.process(state)
        failed = self.event("STAGE_PROCESS_FAILED")
        self.assertTrue(failed["exc_info"])
        self.assertEqual((failed["image_id"], failed["operation"]), ("test-photo", DESCRIPTOR.operation))
