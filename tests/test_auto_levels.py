"""Auto Levels histograms, per-image settings and stage lifecycle."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import numpy as np

from retouching_tool.application.pipeline_service import PipelineService
from retouching_tool.errors import RetouchingError
from retouching_tool.imaging.transfer import srgb8_to_linear
from retouching_tool.operations import auto_levels
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ContextEntry, ImageState, PERSON_FOREGROUND, PersonForegroundMask, SourceMetadata


class AutoLevelsTests(unittest.TestCase):
    def setUp(self):
        self.log = Mock()
        self.editor = PipelineService(Path("models"), "cpu", self.log)
        self.stage = self.editor.add_operation("core.auto_levels", 1).definition.stages[0]

    def session(self, **parameters):
        stage = self.editor.set_parameters(self.stage.id, parameters).definition.stages[0]
        runtime = StageRuntime(self.log, "cpu", Path("models"), "job", stage)
        return StageSession(self.editor.registry, self.editor.catalog, runtime)

    def state(self, codes):
        pixels = srgb8_to_linear(codes).reshape(1, -1, 3)
        mask = ContextEntry(PERSON_FOREGROUND, "mask", "analysis.person_foreground", 1, 0, 3, pixels.shape[1], 1,
                            PersonForegroundMask(1, 1, b"\x01"), source_id="photo")
        return ImageState.from_pixels(pixels, SourceMetadata("photo", exif=b"capture"), context=(mask,),
                                      geometry_revision=3)

    def test_linked_percentiles_match_manual_levels_with_outliers_and_fractional_samples(self):
        codes = np.array([[0]*3] + [[32, 64, 72]]*48 + [[95.5, 130.25, 172.75]]*2 +
                         [[128, 145, 208]]*47 + [[250]*3]*2)
        state = self.state(codes)
        with (patch.object(auto_levels, "HISTOGRAM_BLOCK_PIXELS", 7),
              self.session(shadow_clip=1, highlight_clip=2) as session):
            result = session.process(state, image_id="first")
        expected = srgb8_to_linear(np.clip((codes - 32) / (208 - 32), 0, 1) * 255)
        np.testing.assert_allclose(result.pixels[0], expected, rtol=2e-6, atol=2e-7)
        np.testing.assert_array_equal(state.pixels[0], srgb8_to_linear(codes))
        self.assertIs(result.metadata, state.metadata)
        self.assertEqual((result.context, result.geometry_revision, result.pixel_revision), (state.context, 3, 1))
        only = next(c.kwargs for c in self.log.emit.call_args_list if c.args[0] == "AUTO_LEVELS_APPLIED")
        self.assertEqual((only["input_black"], only["input_white"], only["image_id"]), ([32]*3, [208]*3, "first"))

    def test_reused_session_remeasures_and_preserves_degenerate_channel(self):
        first = self.state([[20, 50, 64], [80, 51, 96], [140, 52, 160]])
        second = self.state([[64, 50, 35], [80, 51, 50], [224, 52, 185]])
        with (patch.object(auto_levels, "open_session", wraps=auto_levels.open_session) as opener,
              self.session(mode="Per channel", blend=.5) as session):
            for name, state, mid in (("A", first, (127.5, 51, 85)), ("B", second, (25.5, 51, 25.5)),
                                     ("A again", first, (127.5, 51, 85))):
                result = session.process(state, image_id=name)
                expected = (state.pixels[0, 1] + srgb8_to_linear(mid)) / 2
                np.testing.assert_allclose(result.pixels[0, 1], expected, atol=2e-7)
                np.testing.assert_array_equal(result.pixels[..., 1], state.pixels[..., 1])
        opener.assert_called_once()
        self.assertIsNone(session.operation)

    def test_nearly_flat_full_range_and_disabled_pass_through_without_a_graph(self):
        flat = self.state([[10, 50, 90], [14, 52, 91]])
        full = self.state([[0, 0, 0], [125, 91, 80], [255, 255, 255]])
        with patch.object(auto_levels, "open_session") as opener:
            with self.session(shadow_clip=0, highlight_clip=0) as session:
                for name, state in (("flat", flat), ("full", full)):
                    self.assertIs(session.process(state, image_id=name).pixels, state.pixels)
            self.editor.set_enabled(self.stage.id, False)
            with self.session() as session:
                self.assertIs(session.process(flat, image_id="off").pixels, flat.pixels)
            opener.assert_not_called()
        warning = next(c.kwargs for c in self.log.emit.call_args_list if c.args[0] == "AUTO_LEVELS_LOW_CONTRAST")
        self.assertEqual((warning["level"], warning["image_id"], warning["stage_instance_id"]),
                         ("WARNING", "flat", self.stage.id))

    def test_bad_settings_and_session_failure(self):
        for key, value in (("shadow_clip", -1), ("highlight_clip", 6), ("mode", "Other")):
            with self.assertRaises(RetouchingError):
                self.editor.set_parameter(self.stage.id, key, value)
        with (patch.object(auto_levels, "open_session", side_effect=RuntimeError("device failed")),
              self.session() as session, self.assertRaisesRegex(RuntimeError, "device failed")):
            session.process(self.state([[20]*3, [200]*3]), image_id="failed")
        failed = next(c.kwargs for c in self.log.emit.call_args_list if c.args[0] == "STAGE_PROCESS_FAILED")
        self.assertEqual((failed["exc_info"], failed["job_id"], failed["image_id"]), (True, "job", "failed"))
