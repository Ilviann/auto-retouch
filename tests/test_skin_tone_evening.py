"""Native-scale color, spatial boundaries, and dependency contract of skin tone evening."""

from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np

from retouching_tool.errors import RetouchingError
from retouching_tool.operations.skin_tone_evening import DESCRIPTOR
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.definition import StageDefinition
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ContextEntry, EXPOSED_SKIN, ExposedSkinMask, ImageState, SourceMetadata
from retouching_tool.pipeline.registry import application_registry


LUMA = np.array([.2126, .7152, .0722])


class SkinToneEveningTests(unittest.TestCase):
    def setUp(self):
        self.log = Mock()
        self.registry = application_registry()

    def image(self, pixels, labels):
        value = ExposedSkinMask(labels.shape[1], labels.shape[0], labels.tobytes())
        metadata = SourceMetadata("photo", exif=b"original metadata")
        entry = ContextEntry(EXPOSED_SKIN, "mask", "analysis.exposed_skin", 1, 0, 2,
                             pixels.shape[1], pixels.shape[0], value,
                             source_id=metadata.source_id, producer_settings_hash="a" * 64, model_ref="fixture")
        return ImageState.from_pixels(pixels, metadata, geometry_revision=2, context=(entry,))

    def process(self, state, **parameters):
        stage = StageDefinition("tone", DESCRIPTOR.operation, 1, True, tuple(parameters.items()))
        runtime = StageRuntime(self.log, "directml", Path("models"), "job", stage)
        with StageSession(self.registry, ModelCatalog(), runtime) as session:
            return session.process(state, image_id="photo")

    def test_local_patch_reduced_at_constant_luminance_and_bounded_strength(self):
        y, x = np.indices((39, 57))
        texture = (.93 + .07 * ((x + y) % 2)).astype(np.float32)
        pixels = texture[:, :, None] * np.array([.48, .29, .21], np.float32)
        # Color-only perturbation across alternating bright and dark pores.
        pixels[16:23, 24:31] += [.014, -.014 * .2126 / .7152, 0]
        labels = np.ones(pixels.shape[:2], np.uint8)
        labels[:, :18] = 0
        labels[:, 32:] = 2
        state = self.image(pixels, labels)
        edited = self.process(state, strength=1, radius=10, max_shift=1)
        self.assertLess(edited.pixels[19, 27, 0], pixels[19, 27, 0] - .001)
        np.testing.assert_allclose(edited.pixels @ LUMA, pixels @ LUMA, rtol=0, atol=4e-8)
        bound = .01 * (pixels @ LUMA)
        self.assertTrue(np.all(np.max(np.abs(edited.pixels - pixels), axis=2) <= bound + 4e-8))
        np.testing.assert_array_equal(edited.pixels[:, :18], pixels[:, :18])
        self.assertIs(edited.metadata, state.metadata)
        self.assertIs(edited.entry(EXPOSED_SKIN.key), state.entry(EXPOSED_SKIN.key))
        self.assertEqual((edited.geometry_revision, edited.pixel_revision), (2, 1))
        half = self.process(state, strength=.5, radius=10, max_shift=1)
        np.testing.assert_allclose(half.pixels - pixels, .5 * (edited.pixels - pixels), atol=5e-8)
        applied = next(c.kwargs for c in self.log.emit.call_args_list if c.args[0] == "SKIN_TONE_APPLIED")
        self.assertEqual((applied["image_id"], applied["job_id"], applied["stage_instance_id"]), ("photo", "job", "tone"))

    def test_foreign_colors_gamut_and_tile_seams(self):
        rng = np.random.default_rng(1)
        pixels = rng.uniform(.26, .29, (63, 83, 3)).astype(np.float32)
        pixels[20:30, 35:40] = [.9, .05, .15]  # Strong chromatic edge inside the coarse mask.
        pixels[12, 40] = [1, .9, .8]
        pixels[18, 50] = 0
        labels = np.ones(pixels.shape[:2], np.uint8)
        labels[:, :25] = 0
        state = self.image(pixels, labels)
        full = self.process(state, radius=30, strength=1, max_shift=20)
        with patch("retouching_tool.operations.skin_tone_evening.TILE_SIZE", 17):
            tiled = self.process(state, radius=30, strength=1, max_shift=20)
        np.testing.assert_allclose(tiled.pixels, full.pixels, atol=4e-8, rtol=0)
        np.testing.assert_array_equal(tiled.pixels[20:30, 35:40], pixels[20:30, 35:40])
        self.assertTrue(np.isfinite(tiled.pixels).all())
        np.testing.assert_array_equal(tiled.pixels[[12, 18], [40, 50]], pixels[[12, 18], [40, 50]])
        pixels[:, :25] = [.85, .01, .15]
        other_background = self.process(self.image(pixels, labels), radius=30, strength=1, max_shift=20)
        np.testing.assert_array_equal(other_background.pixels[:, 25:], full.pixels[:, 25:])

    def test_identity_empty_and_stale_context(self):
        pixels = np.full((5, 1, 3), .32, np.float32)
        skin = self.image(pixels, np.ones((1, 1), np.uint8))
        for settings in ({}, {"radius": 96}, {"strength": 0}, {"max_shift": 0}):
            self.assertIs(self.process(skin, **settings).pixels, skin.pixels)
        empty = self.image(pixels, np.zeros((1, 1), np.uint8))
        self.assertIs(self.process(empty).pixels, empty.pixels)
        warning = next(c.kwargs for c in self.log.emit.call_args_list if c.args[0] == "SKIN_TONE_EMPTY_MASK")
        self.assertEqual((warning["level"], warning["image_id"]), ("WARNING", "photo"))
        for invalid in (replace(skin, context=()), replace(skin, geometry_revision=3),
                        replace(skin, metadata=SourceMetadata("different"))):
            with self.assertRaises(RetouchingError) as error:
                self.process(invalid)
            self.assertEqual(error.exception.code, "CONTEXT_REQUIRED")

    def test_mask_feathers_strength_at_native_resolution(self):
        pixels = np.full((31, 64, 3), [.4, .3, .24], np.float32)
        pixels[15, :, 0] += .012
        state = self.image(pixels, np.array([[2, 0]], np.uint8))
        result = self.process(state, radius=24, strength=1)
        correction = pixels[15, :, 0] - result.pixels[15, :, 0]
        self.assertGreater(correction[10], 0)
        self.assertGreater(correction[32], 0)
        self.assertAlmostEqual(correction[32] / correction[10], .48, delta=.02)
        np.testing.assert_array_equal(result.pixels[:, 48:], pixels[:, 48:])
