"""Stage routing, resize consent, mask persistence and local CPU inference."""

from dataclasses import replace
import hashlib
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import ResizeApprovalRequired, RetouchingError
from retouching_tool.jobs.journal import open_journal
from retouching_tool.operations.alibaba_skin import DESCRIPTOR, MODEL
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ContextEntry, FACE_REGIONS, FaceRegion, FaceRegions, ImageState, SourceMetadata
from retouching_tool.storage.artifact_codec import decode_context, encode_context

ROOT = Path(__file__).resolve().parents[1]


class AlibabaTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".test-work"
        work.mkdir(exist_ok=True)
        temp = TemporaryDirectory(dir=work)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.log = Diagnostics(self.root / "logs", console=StringIO())
        self.addCleanup(self.log.close)
        self.app = ApplicationService(self.root, load_config(self.root), self.log)
        self.app.startup(LaunchOptions((), None, None), "directml", pytorch_backend="cpu")
        self.editor = self.app.pipeline_service
        self.stage = self.editor.add_operation(DESCRIPTOR.operation, 1, model_ref=MODEL.ref).stages[-1].stage

    def install_fixture(self):
        files = []
        for f in MODEL.files:
            path = self.editor.checkpoints / f.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(path.name.encode())
            files.append(replace(f, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        self.editor.catalog = ModelCatalog((replace(MODEL, files=tuple(files)),))
        self.editor.recheck()
        self.editor.set_enabled(self.stage.id, True)
        self.stage, = self.editor.resolve_scaling(self.editor.status.definition).stages

    def test_assets_mask_codec_and_targeted_output(self):
        status, = self.editor.status.stages
        self.assertEqual({issue.path for issue in status.issues}, {self.editor.checkpoints / f.relative_path for f in MODEL.files})
        self.assertEqual(status.model_page, MODEL.publisher_url)
        self.install_fixture()
        state = ImageState.from_pixels(np.zeros((4, 6000, 3), np.float32), SourceMetadata("source"))
        region = FaceRegion(50, 1, 54, 3, 2, 1, bytes([128]))
        faces = FaceRegions((region, FaceRegion(70, 1, 72, 3)))
        entry = ContextEntry(FACE_REGIONS, "faces", "fixture", 1, 0, 0, 6000, 4, faces,
                             source_id="source", producer_settings_hash="a" * 64)
        state = replace(state, context=(decode_context(encode_context(entry)),))
        runtime = StageRuntime(self.log, "cpu", self.editor.checkpoints, None, self.stage)
        with patch("retouching_tool.operations.alibaba_inference.SkinNetworks") as networks:
            networks.return_value.apply.side_effect = lambda rgb, *_: np.ones_like(rgb)
            with StageSession(self.editor.registry, self.editor.catalog, runtime) as session:
                result = session.process(state, image_id="source")
                self.assertEqual(networks.return_value.apply.call_count, 2)
                expected = np.zeros_like(result.pixels)
                expected[1:3, 50:52] = expected[1:3, 70:72] = 1
                np.testing.assert_array_equal(expected, result.pixels)
                self.assertIs(result.metadata, state.metadata)
                self.assertEqual(result.entry(FACE_REGIONS.key), entry)
                empty = replace(state, context=(replace(entry, value=FaceRegions(())),))
                self.assertIs(session.process(empty, image_id="source").pixels, state.pixels)
                with self.assertRaisesRegex(RetouchingError, "Regenerate"):
                    session.process(replace(state, geometry_revision=1), image_id="source")

            self.editor.set_parameter(self.stage.id, "use_face_rects", False)
            unchecked, = self.editor.resolve_scaling(self.editor.status.definition).stages
            with StageSession(self.editor.registry, self.editor.catalog, replace(runtime, stage=unchecked)) as session:
                for image in (state, empty, replace(state, geometry_revision=1)):
                    with self.assertRaises(ResizeApprovalRequired):
                        session.process(image, image_id="source")
            with StageSession(self.editor.registry, self.editor.catalog,
                              replace(runtime, stage=unchecked, resize_approved=True)) as session:
                for image in (state, empty):
                    networks.return_value.apply.reset_mock()
                    edited = session.process(image, image_id="source")
                    networks.return_value.apply.assert_called_once()
                    np.testing.assert_array_equal(edited.pixels, np.ones_like(image.pixels))
                    self.assertEqual(edited.entry(FACE_REGIONS.key), image.entry(FACE_REGIONS.key))

    def test_resize_approval_preview_and_saved_jobs(self):
        self.install_fixture()
        src = self.root / "input.jpg"
        Image.new("RGB", (6000, 10), (80, 120, 90)).save(src)
        index = self.app.batch_service.index((src,))
        image_id = index.images[0].image_id
        with patch("retouching_tool.operations.alibaba_inference.SkinNetworks") as networks:
            networks.return_value.apply.side_effect = lambda rgb, *_: 1 - rgb
            with self.assertRaises(ResizeApprovalRequired) as required:
                self.app.preview_service.picture(index, image_id, "first", process=True)
            self.assertEqual(required.exception.stage_id, self.stage.id)
            networks.return_value.apply.assert_not_called()
            approval = (self.stage.id,)
            preview = self.app.preview_service.picture(index, image_id, "approved", process=True, resize_approvals=approval)
            self.assertNotEqual(preview.original.rgb, preview.result.rgb)
            exports = []
            for mode in ("sequential", "staged"):
                dest = self.root / mode
                dest.mkdir()
                jobs = self.app.job_service
                with self.assertRaises(ResizeApprovalRequired):
                    jobs.create(index, dest, mode=mode)
                job_id = jobs.create(index, dest, mode=mode, resize_approvals=approval)
                with open_journal(self.app.job_service.folder_for(job_id), job_id) as journal:
                    self.assertEqual(journal.snapshot()["resize_approvals"], list(approval))
                result = jobs.run(job_id)
                self.assertEqual(result.status, "completed")
                with Image.open(result.images[0].output) as image:
                    self.assertEqual(image.size, (6000, 10))
                    exports.append(image.tobytes())
            self.assertEqual(exports[0], exports[1])

    def test_two_local_checkpoints_run_in_both_schedules(self):
        checkpoints = load_config(ROOT).checkpoints
        if not all((checkpoints / f.relative_path).is_file() for f in MODEL.files):
            self.skipTest("Local Alibaba checkpoints required")
        self.editor.checkpoints = checkpoints
        self.editor.recheck()
        self.editor.set_enabled(self.stage.id, True)
        source = self.root / "fixture.jpg"
        samples = np.random.default_rng(2).integers(40, 230, (80, 120, 3), dtype=np.uint8)
        Image.fromarray(samples).save(source)
        index = self.app.batch_service.index((source,))
        results = []
        for mode in ("sequential", "staged"):
            dest = self.root / mode
            dest.mkdir()
            job = self.app.job_service.create(index, dest, mode=mode)
            outcome = self.app.job_worker.start(job).wait(90)
            self.assertIsNone(outcome.error_code, outcome)
            status = self.app.job_service.status(job)
            self.assertEqual(status.status, "completed", status.images)
            with Image.open(status.images[0].output) as image:
                results.append(image.tobytes())
        self.assertEqual(results[0], results[1])

    def test_inpainting_uses_original_halos_and_blends_only_detected_pixels(self):
        import torch
        from retouching_tool.operations.alibaba_inference import SkinNetworks

        nets = SkinNetworks.__new__(SkinNetworks)
        roi = torch.full((1, 3, 9, 518), -.5)
        weight = torch.zeros(1, 1, 9, 518)
        weight[:, :, 2:6, 510:515] = .4
        patches = []

        def repair(masked, keep):
            patches.append((masked.clone(), keep.clone()))
            return torch.ones_like(masked)

        nets.repair = repair
        result = nets._repair_tiles(roi, weight)
        torch.testing.assert_close(result, roi * (1 - weight) + weight)
        self.assertEqual(len(patches), 2)
        torch.testing.assert_close(patches[0][0][:, :, :, 512:], patches[1][0][:, :, :, :64])
        self.assertEqual(patches[1][0].shape, (1, 3, 576, 576))
