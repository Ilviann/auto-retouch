"""Photometric, masking and lifecycle checks for the CPU skin shine operation."""

from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np

from retouching_tool.errors import RetouchingError
from retouching_tool.operations.skin_shine import DESCRIPTOR
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.definition import StageDefinition
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ContextEntry, EXPOSED_SKIN, ExposedSkinMask, ImageState, SourceMetadata
from retouching_tool.pipeline.registry import application_registry


class SkinShineTests(unittest.TestCase):
    def setUp(self):
        self.log = Mock()
        self.registry = application_registry()

    def image(self, pixels, labels):
        mask = ExposedSkinMask(labels.shape[1], labels.shape[0], labels.tobytes())
        entry = ContextEntry(EXPOSED_SKIN, "mask", "analysis.exposed_skin", 1, 0, 2,
                             pixels.shape[1], pixels.shape[0], mask, source_id="photo",
                             producer_settings_hash="a" * 64, model_ref="fixture")
        return ImageState.from_pixels(pixels, SourceMetadata("photo", b"source metadata"),
                                      geometry_revision=2, context=(entry,))

    def process(self, state, **settings):
        stage = StageDefinition("shine", DESCRIPTOR.operation, 1, True, tuple(settings.items()))
        runtime = StageRuntime(self.log, "cpu", Path("models"), "job", stage)
        with StageSession(self.registry, ModelCatalog(), runtime) as session:
            return session.process(state, image_id="photo")

    def test_neutral_addition_vs_diffuse_light_and_clipping(self):
        pixels = np.full((53, 90, 3), [.36, .21, .13], np.float32)
        pixels[23:28, 9:14] += .21           # Outside the mask.
        pixels[23:28, 32:37] += .21          # Body skin.
        pixels[23:28, 51:56] += .21          # Face skin.
        pixels[10:15, 65:70] *= 1.7          # Brighter skin, original chromaticity.
        pixels[23:28, 77:82] = 1.0           # Lost texture.
        labels = np.ones(pixels.shape[:2], np.uint8)
        labels[:, :22] = 0
        labels[:, 45:] = 2
        state = self.image(pixels, labels)
        result = self.process(state, strength=1)
        delta = pixels - result.pixels
        self.assertGreater(delta[25, 34].min(), .05)
        self.assertGreater(delta[25, 53].min(), .05)
        np.testing.assert_allclose(delta.max(axis=2), delta.min(axis=2), atol=5e-8)
        for area in (delta[:, :22], delta[10:15, 65:70], delta[23:28, 77:82]):
            self.assertFalse(area.any())
        self.assertIs(result.metadata, state.metadata)
        self.assertIs(result.context[0], state.context[0])
        self.assertEqual((result.pixel_revision, result.geometry_revision), (1, 2))
        for settings in ({"strength": .5}, {"strength": 1, "blend": .5}):
            np.testing.assert_allclose(pixels - self.process(state, **settings).pixels, delta * .5, atol=5e-8)
        with patch("retouching_tool.operations.skin_shine.TILE_SIZE", 13):
            np.testing.assert_array_equal(self.process(state, strength=1).pixels, result.pixels)

    def test_feathered_mask_and_native_texture(self):
        pixels = np.full((35, 64, 3), [.36, .21, .13], np.float32)
        pixels[16:19] += .18
        pixels[16:19, ::2] += .012
        state = self.image(pixels, np.array([[2, 0]], np.uint8))
        result = self.process(state)
        delta = pixels - result.pixels
        self.assertGreater(delta[17, 14, 0], delta[17, 26, 0])
        self.assertGreater(delta[17, 26, 0], 0)
        self.assertGreater(result.pixels[17, 8, 0] - result.pixels[17, 9, 0], .005)
        self.assertFalse(delta[:, 48:].any())

    def test_empty_zero_missing_and_wrong_frame(self):
        pixels = np.full((15, 1, 3), .3, np.float32)
        state = self.image(pixels, np.ones((1, 1), np.uint8))
        for settings in ({}, {"strength": 0}):
            self.assertIs(self.process(state, **settings).pixels, state.pixels)
        empty = self.image(pixels, np.zeros((1, 1), np.uint8))
        self.assertIs(self.process(empty).pixels, empty.pixels)
        warning = next(c.kwargs for c in self.log.emit.call_args_list if c.args[0] == "SKIN_SHINE_EMPTY_MASK")
        self.assertEqual((warning["level"], warning["image_id"], warning["stage_instance_id"]), ("WARNING", "photo", "shine"))
        for invalid in (replace(state, context=()), replace(state, geometry_revision=3)):
            with self.assertRaises(RetouchingError) as error:
                self.process(invalid)
            self.assertEqual(error.exception.code, "CONTEXT_REQUIRED")
