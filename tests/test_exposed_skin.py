"""Exercise class mapping, skin-only compositing, dependency validation and checkpoint replay."""

from dataclasses import replace
import hashlib
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from retouching_tool.application.job_service import JobControl
from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.jobs.journal import JobJournal
from retouching_tool.operations.alibaba_skin import SMOOTHING, SMOOTHING_MODEL
from retouching_tool.operations.exposed_skin import DESCRIPTOR, MODEL, SIZE
from retouching_tool.operations.skin_tone_evening import DESCRIPTOR as TONE_EVENING
from retouching_tool.operations.skin_shine import DESCRIPTOR as SKIN_SHINE
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ImageState, SourceMetadata
from retouching_tool.storage.artifact_codec import decode_context, encode_context


class ExposedSkinTests(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[1] / ".test-work"
        work.mkdir(exist_ok=True)
        self.root = Path(self.enterContext(TemporaryDirectory(dir=work)))
        self.log = Diagnostics(self.root / "logs", console=StringIO())
        self.addCleanup(self.log.close)
        self.app = ApplicationService(self.root, load_config(self.root), self.log)
        self.app.startup(LaunchOptions((), None, None), "directml", pytorch_backend="cpu")
        self.editor = self.app.pipeline_service
        self.analysis = self.editor.add_operation(DESCRIPTOR.operation, 1, model_ref=MODEL.ref).stages[-1].stage
        self.assertFalse(self.analysis.enabled)
        self.assertEqual(self.editor.status.stages[-1].issues[0].path, self.editor.checkpoints / MODEL.files[0].relative_path)
        self.assertEqual(self.editor.status.stages[-1].model_page, MODEL.publisher_url)
        manifests = []
        for manifest in (MODEL, SMOOTHING_MODEL):
            file, = manifest.files
            path = self.editor.checkpoints / file.relative_path
            path.parent.mkdir(parents=True)
            path.write_bytes(b"fixture")
            manifests.append(replace(manifest, files=(replace(file, sha256=hashlib.sha256(b"fixture").hexdigest()),)))
        self.editor.catalog = ModelCatalog(manifests)
        self.assertEqual(self.editor.recheck().stages[0].state, "skipped")
        self.editor.set_enabled(self.analysis.id, True)
        self.smoothing = self.editor.add_operation(SMOOTHING.operation, 1, model_ref=SMOOTHING_MODEL.ref).stages[-1].stage
        self.assertNotIn("use_exposed_skin", self.smoothing.portable_parameters())
        self.editor.set_parameter(self.smoothing.id, "use_exposed_skin", True)
        source = self.root / "fixture.jpg"
        Image.new("RGB", (513, 129), (86, 86, 86)).save(source)
        self.index = self.app.batch_service.index((source,))
        self.image = self.index.images[0]
        session = self.enterContext(patch("retouching_tool.operations.exposed_skin.open_session")).return_value
        for getter, name, channels in ((session.get_inputs, "input_29", 3), (session.get_outputs, "Identity", 6)):
            getter.return_value = [SimpleNamespace(name=name, type="tensor(float)", shape=[1, SIZE, SIZE, channels])]
        session.get_providers.return_value = ["DmlExecutionProvider"]
        self.run_model = session.run
        self.logits = np.zeros((1, SIZE, SIZE, 6), np.float32)
        for index, region in enumerate(np.array_split(np.arange(SIZE), 6)):
            self.logits[:, :, region, index] = 1
        self.run_model.return_value = [self.logits]
        self.infer = self.enterContext(patch("retouching_tool.operations.alibaba_inference.SkinNetworks")).return_value.apply
        self.infer.side_effect = lambda image, *_: np.ones_like(image)

    def test_preview_and_checkpointed_batch(self):
        picture = self.app.preview_service.picture
        preview = picture(self.index, self.image.image_id, "mask", stage_id=self.analysis.id)
        tensor = self.run_model.call_args.args[1]["input_29"]
        self.assertEqual(tensor.shape, (1, SIZE, SIZE, 3))
        np.testing.assert_allclose(tensor, 86 / 255, atol=1e-6)
        entry, = preview.context
        self.assertEqual(decode_context(encode_context(entry)), entry)
        encoded = encode_context(entry)
        encoded["value"]["data"]["labels"] = "////" + encoded["value"]["data"]["labels"][4:]
        with self.assertRaises(ValueError):
            decode_context(encoded)
        before = np.frombuffer(preview.original.rgb, np.uint8).reshape(129, 513, 3)
        overlay = np.frombuffer(preview.result.rgb, np.uint8).reshape(before.shape)
        self.assertIn("magenta", preview.result_caption)
        for x in (30, 110, 377, 463):
            np.testing.assert_array_equal(before[:, x], overlay[:, x])
        self.assertGreater(overlay[0, 210, 1], overlay[0, 210, 0])
        self.assertGreater(overlay[0, 287, 0], overlay[0, 287, 1])
        result = picture(self.index, self.image.image_id, "target", process=True)
        edited = np.frombuffer(result.result.rgb, np.uint8).reshape(before.shape)
        for x in (30, 110, 377, 463):
            np.testing.assert_array_equal(before[:, x], edited[:, x])
        self.assertTrue((edited[:, 210] == 255).all() and (edited[:, 287] == 255).all())
        self.assertTrue(((edited[0, :, 0] > 86) & (edited[0, :, 0] < 255)).any())

        outputs = []
        jobs = self.app.job_service
        for mode in ("sequential", "staged"):
            destination = self.root / mode
            destination.mkdir()
            job = jobs.create(self.index, destination, mode=mode)
            if mode == "staged":
                control = JobControl()
                commit = JobJournal.commit_stage
                def pause(journal, *args):
                    commit(journal, *args)
                    control.pause.set()
                with patch.object(JobJournal, "commit_stage", pause):
                    self.assertEqual(jobs.run(job, control=control).status, "paused")
                self.run_model.reset_mock()
            finished = jobs.run(job)
            self.assertEqual(finished.status, "completed")
            if mode == "staged":
                self.run_model.assert_not_called()
            with Image.open(finished.images[0].output) as image:
                outputs.append(image.tobytes())
        self.assertEqual(*outputs)

    def test_empty_missing_stale_and_invalid(self):
        picture = self.app.preview_service.picture
        self.logits.fill(0)
        result = picture(self.index, self.image.image_id, "empty", stage_id=self.smoothing.id, through_stage=True)
        self.assertEqual(result.original, result.result)
        self.assertIn("empty", result.stage_warning)
        self.infer.assert_not_called()
        for mode in ("sequential", "staged"):
            destination = self.root / mode
            destination.mkdir()
            job = self.app.job_service.create(self.index, destination, mode=mode)
            self.assertEqual(self.app.job_service.run(job).status, "completed")
            self.assertTrue(any(r["code"] == "RETOUCH_NO_EXPOSED_SKIN" and r["job_id"] == job
                                and r["image_id"] == self.image.image_id for r in self.log.records))
        self.infer.assert_not_called()
        mask, = picture(self.index, self.image.image_id, "mask", stage_id=self.analysis.id).context
        state = ImageState.from_pixels(np.zeros((129, 513, 3), np.float32), SourceMetadata(self.image.image_id))
        _, stage = self.editor.resolve_scaling(self.editor.status.definition).stages
        runtime = StageRuntime(self.log, "cpu", self.editor.checkpoints, None, stage)
        with StageSession(self.editor.registry, self.editor.catalog, runtime) as session:
            for state in (replace(state, context=(replace(mask, pixel_revision=1),)),
                          replace(state, context=(mask,), geometry_revision=1),
                          replace(state, context=(mask,), metadata=SourceMetadata("other"))):
                with self.assertRaisesRegex(RetouchingError, "requires current skin.exposed"):
                    session.process(state, image_id="fixture")
        self.logits[0, 0, 0, 0] = np.nan
        with self.assertRaisesRegex(RetouchingError, "invalid class scores"):
            picture(self.index, self.image.image_id, "bad", stage_id=self.analysis.id)
        self.run_model.side_effect = RuntimeError("fixture provider failure")
        with self.assertRaisesRegex(RetouchingError, "analysis failed"):
            picture(self.index, self.image.image_id, "fail", stage_id=self.analysis.id)
        record = next(r for r in self.log.records if r["code"] == "STAGE_PROCESS_FAILED"
                      and r.get("preview_request_id") == "fail")
        self.assertIn("fixture provider failure", record["exception"])
        self.editor.set_enabled(self.analysis.id, False)
        self.assertEqual(self.editor.status.stages[-1].state, "blocked")
        with self.assertRaisesRegex(RetouchingError, "skin.exposed"):
            picture(self.index, self.image.image_id, "alone", stage_id=self.smoothing.id)
        self.editor.set_parameter(self.smoothing.id, "use_exposed_skin", False)
        self.assertTrue(self.editor.status.executable)

    def test_latest_enabled_mask_replaces_previous_even_when_empty(self):
        second = self.editor.add_operation(DESCRIPTOR.operation, 1, model_ref=MODEL.ref).stages[-1].stage
        self.editor.reorder((self.analysis.id, second.id, self.smoothing.id))
        self.run_model.side_effect = ([self.logits], [np.zeros_like(self.logits)])
        picture = self.app.preview_service.picture
        preview = picture(self.index, self.image.image_id, "new-mask", stage_id=self.smoothing.id, through_stage=True)
        latest, = preview.context
        self.assertEqual(latest.producer_id, second.id)
        self.assertIn("empty", preview.stage_warning)
        self.infer.assert_not_called()

        self.editor.set_enabled(second.id, False)
        self.run_model.side_effect = None
        preview = picture(self.index, self.image.image_id, "first-mask", stage_id=self.smoothing.id, through_stage=True)
        first, = preview.context
        self.assertEqual(first.producer_id, self.analysis.id)
        self.assertNotEqual(preview.original, preview.result)

    def test_disconnected_body_regions_without_a_face(self):
        self.logits.fill(0)
        self.logits[:, :, 16:64, 2] = 1
        self.logits[:, :, 180:210, 2] = 1
        preview = self.app.preview_service.picture(self.index, self.image.image_id, "body-only", process=True)
        before = np.frombuffer(preview.original.rgb, np.uint8).reshape(129, 513, 3)
        after = np.frombuffer(preview.result.rgb, np.uint8).reshape(before.shape)
        self.assertTrue((after[:, 60] == 255).all() and (after[:, 390] == 255).all())
        np.testing.assert_array_equal(after[:, 230], before[:, 230])

    def test_invalidated_model_blocks_target_until_explicit_recovery(self):
        file = self.editor.checkpoints / MODEL.files[0].relative_path
        file.write_bytes(b"wrong weights")
        analysis, smoothing = self.editor.recheck().stages
        self.assertEqual(analysis.issues[0].code, "MODEL_CHECKSUM_INVALID")
        self.assertFalse(analysis.stage.enabled)
        self.assertEqual(smoothing.state, "blocked")
        file.write_bytes(b"fixture")
        self.assertEqual([s.state for s in self.editor.recheck().stages], ["skipped", "blocked"])
        self.editor.set_enabled(self.analysis.id, True)
        self.assertTrue(self.editor.status.executable)

    def test_local_tone_evening_preview_and_frozen_schedules(self):
        self.editor.remove(self.smoothing.id)
        stage = self.editor.add_operation(TONE_EVENING.operation, 1).stages[-1].stage
        self.editor.set_parameter(stage.id, "radius", 8)
        self.editor.set_parameter(stage.id, "strength", 1)
        self.editor.load_data(self.editor.status.definition.to_json())
        self.assertEqual(self.editor.status.stages[-1].stage.parameter("radius"), 8)
        self.editor.reorder((stage.id, self.analysis.id))
        self.assertEqual(self.editor.status.stages[0].state, "blocked")
        exposure = self.editor.add_operation("core.exposure", 1).stages[-1].stage
        self.editor.set_parameter(exposure.id, "stops", -.5)
        self.editor.reorder((self.analysis.id, exposure.id, stage.id))
        self.assertTrue(self.editor.status.executable)
        samples = np.full((71, 119, 3), [180, 143, 125], dtype=np.uint8)
        samples[28:36, 52:60, 0] += 8
        source = self.root / "color.jpg"
        Image.fromarray(samples).save(source, quality=100, subsampling=0)
        index = self.app.batch_service.index((source,))
        picture = self.app.preview_service.picture
        with self.assertRaises(RetouchingError) as missing:
            picture(index, index.images[0].image_id, "standalone", stage_id=stage.id)
        self.assertEqual(missing.exception.code, "PREVIEW_CONTEXT_REQUIRED")
        comparison = picture(index, index.images[0].image_id, "masked", stage_id=stage.id, through_stage=True)
        self.assertNotEqual(comparison.original.rgb, comparison.result.rgb)
        mask, = comparison.context
        self.assertEqual((mask.producer_id, mask.pixel_revision), (self.analysis.id, 0))
        jobs = self.app.job_service
        frozen = []
        for mode in ("sequential", "staged"):
            destination = self.root / mode
            destination.mkdir()
            frozen.append(jobs.create(index, destination, mode=mode))
        self.editor.set_parameter(stage.id, "strength", 0)
        exports = []
        for mode, job in zip(("sequential", "staged"), frozen, strict=True):
            if mode == "staged":
                control = JobControl()
                commit = JobJournal.commit_stage

                def pause_after_exposure(journal, image_id, stage_index, artifact, warnings):
                    commit(journal, image_id, stage_index, artifact, warnings)
                    if stage_index == 1:
                        control.pause.set()

                with patch.object(JobJournal, "commit_stage", pause_after_exposure):
                    self.assertEqual(jobs.run(job, control=control).status, "paused")
                self.run_model.reset_mock()
            result = jobs.run(job)
            self.assertEqual(result.status, "completed")
            if mode == "staged":
                self.run_model.assert_not_called()
            self.assertTrue(any(r["code"] == "SKIN_TONE_APPLIED" and r["job_id"] == job for r in self.log.records))
            with Image.open(result.images[0].output) as image:
                exports.append(image.tobytes())
        self.assertEqual(*exports)

    def test_shine_preview_overlay_and_batch_schedules(self):
        self.editor.remove(self.smoothing.id)
        stage = self.editor.add_operation(SKIN_SHINE.operation, 1).stages[-1].stage
        self.editor.set_parameter(stage.id, "strength", 1)
        samples = np.full((60, 120, 3), [156, 118, 97], np.uint8)
        samples[23:28, 51:56] = [206, 185, 173]
        path = self.root / "shine.png"
        Image.fromarray(samples).save(path)
        index = self.app.batch_service.index((path,))
        comparison = self.app.preview_service.stage_comparison(index, index.images[0].image_id, stage.id, through_stage=True)
        result = comparison.render("edit", {})
        areas = comparison.render("overlay", {}, show_affected_areas=True)
        self.run_model.assert_called_once()
        self.assertIn("pink", areas.result_caption)
        def pixels(display):
            return np.frombuffer(display.rgb, np.uint8).reshape(samples.shape)
        self.assertLess(pixels(result.result)[25, 53, 0], samples[25, 53, 0])
        self.assertGreater(pixels(areas.result)[25, 53, 0], samples[25, 53, 0])
        self.assertTrue((pixels(areas.result)[0] == samples[0]).all())
        for setting in ("strength", "blend"):
            zero = comparison.render(setting, {setting: 0}, show_affected_areas=True)
            self.assertEqual(zero.original, zero.result)
        full = self.app.preview_service.picture(index, index.images[0].image_id, "full", process=True)
        self.assertEqual(full.result, result.result)
        jobs = self.app.job_service
        exports = []
        for mode in ("sequential", "staged"):
            destination = self.root / mode
            destination.mkdir()
            job = jobs.create(index, destination, mode=mode)
            finished = jobs.run(job)
            self.assertEqual(finished.status, "completed")
            self.assertTrue(any(r["code"] == "SKIN_SHINE_APPLIED" and r["job_id"] == job and r["backend"] == "directml"
                                for r in self.log.records))
            with Image.open(finished.images[0].output) as image:
                exports.append(image.tobytes())
        self.assertEqual(*exports)
