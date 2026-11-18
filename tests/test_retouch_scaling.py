import unittest

import numpy as np

from retouching_tool.config import ScalingSettings
from retouching_tool.errors import RetouchingError
from retouching_tool.imaging.scaling import resize_rgb
from retouching_tool.operations.alibaba_skin import FaceCrop, retouch_regions


class RetouchScalingTests(unittest.TestCase):
    def setUp(self):
        self.sizes = []
        self.events = []

    def process(self, pixels, crops=None, approved=False):
        def infer(region):
            self.sizes.append(region.shape[:2])
            return 1 - region

        return retouch_regions(pixels, crops, methods=ScalingSettings(), resize_approved=approved,
                               infer=infer, emit=lambda code, message, **fields: self.events.append((code, fields)))

    def test_limit_applies_after_crop_and_restores_exact_dimensions(self):
        self.assertEqual(FaceCrop(0, 0, 7200, 4800).processing_size, (5000, 3333))
        self.assertEqual(FaceCrop(0, 0, 5000, 3).processing_size, (5000, 3))
        pixels = np.zeros((4, 7200, 3), np.float32)
        crops = (FaceCrop(20, 0, 25, 4), FaceCrop(1000, 0, 7200, 4))
        with self.assertRaises(RetouchingError) as caught:
            self.process(pixels, crops)
        self.assertEqual(caught.exception.code, "RETOUCH_RESIZE_APPROVAL_REQUIRED")
        self.assertEqual(self.sizes, [])
        result = self.process(pixels, crops, approved=True)
        self.assertEqual(self.sizes, [(4, 5), (3, 5000)])
        self.assertEqual(result.shape, pixels.shape)
        self.assertTrue((result[:, 1000:] == 1).all())
        self.assertTrue((result[:, 25:1000] == 0).all())
        self.assertFalse(pixels.any())
        self.assertEqual(self.events[-1][1]["input_size"], (6200, 4))
        with self.assertRaises(RetouchingError):
            self.process(pixels)
        self.process(pixels, approved=True)
        self.assertEqual(self.sizes[-1], (3, 5000))

    def test_masks_preserve_other_pixels_and_faces_use_the_original_input(self):
        pixels = np.full((10, 12, 3), .25, dtype=np.float32)
        mask = np.eye(10, 12, dtype=bool)
        crop = FaceCrop.from_mask(mask)
        self.assertEqual(crop.size, (10, 10))
        result = self.process(pixels, (crop, crop))
        np.testing.assert_array_equal(result[~mask], pixels[~mask])
        np.testing.assert_array_equal(result[mask], 1 - pixels[mask])
        self.assertIs(self.process(pixels, ()), pixels)
        with self.assertRaises(RetouchingError):
            self.process(pixels, (crop, FaceCrop(-1, 0, 2, 3)))
        self.assertEqual(self.sizes, [(10, 10), (10, 10)])

    def test_directional_filter_for_mixed_axis_resize(self):
        grid = np.repeat(np.arange(8, dtype=np.float32).reshape(2, 4, 1), 3, axis=2)
        result = resize_rgb(grid, (2, 4), ScalingSettings("nearest", "bilinear"))
        np.testing.assert_array_equal(result[:, :, 0], [[1, 3], [2, 4], [4, 6], [5, 7]])
        result = resize_rgb(grid, (2, 4), ScalingSettings("bilinear", "nearest"))
        np.testing.assert_array_equal(result[:, :, 0], [[.5, 2.5], [.5, 2.5], [4.5, 6.5], [4.5, 6.5]])
