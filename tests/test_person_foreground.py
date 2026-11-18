"""Focused AI lifecycle and checkpoint checks with a deterministic session double."""

from dataclasses import replace
import hashlib
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from weakref import ref

import numpy as np
from PIL import Image

from retouching_tool.application.job_service import JobControl
from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import ScalingSettings, load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.jobs.journal import JobJournal, runner_lock
from retouching_tool.operations.person_foreground import DESCRIPTOR, MODEL, MODEL_SIZE, SCORES, PersonForeground
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.image_state import PERSON_FOREGROUND
from retouching_tool.storage.artifact_codec import decode_context, encode_context

ROOT = Path(__file__).resolve().parents[1]


class PersonForegroundTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".test-work"
        work.mkdir(exist_ok=True)
        folder = TemporaryDirectory(dir=work, prefix="person-foreground-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.log = Diagnostics(self.root / "logs", console=StringIO())
        self.addCleanup(self.log.close)
        self.app = ApplicationService(self.root, load_config(self.root), self.log)
        self.app.startup(LaunchOptions((), None, None), "directml")
        self.editor = self.app.pipeline_service
        self.source = self.root / "source"
        self.source.mkdir()
        for name in ("one.jpg", "two.jpg"):
            with Image.new("RGB", (64, 32), (84, 84, 84)) as image:
                image.save(self.source / name)
        self.index = self.app.batch_service.index((self.source,))

    def install_test_asset(self):
        contents = b"Fixture identity. Inference is supplied by the test session."
        item = replace(MODEL.files[0], sha256=hashlib.sha256(contents).hexdigest())
        self.editor.checkpoints.mkdir(exist_ok=True)
        (self.editor.checkpoints / item.relative_path).write_bytes(contents)
        self.editor.catalog = ModelCatalog((replace(MODEL, files=(item,)),))

    def add_stage(self):
        return self.editor.add_operation(DESCRIPTOR.operation, DESCRIPTOR.version, model_ref=MODEL.ref).stages[-1]

    def session_factory(self, sessions, runs, *, fail_first=False):
        class Session:
            def get_inputs(self):
                return [SimpleNamespace(name="input", type="tensor(float)")]

            def get_outputs(self):
                return [SimpleNamespace(name=SCORES)]

            def get_providers(self):
                return ["DmlExecutionProvider"]

            def run(session, outputs, inputs):
                self.assertEqual(outputs, [SCORES])
                tensor = inputs["input"]
                self.assertEqual(tensor.shape, (1, 3, MODEL_SIZE, MODEL_SIZE))
                self.assertTrue(tensor.flags.c_contiguous)
                self.assertEqual(tensor.dtype, np.float32)
                np.testing.assert_allclose(tensor, 2 * (84 / 255) - 1, atol=2e-6)
                runs.append(1)
                if fail_first and len(runs) == 1:
                    raise MemoryError("Generated inference allocation failure")
                scores = np.zeros((1, 2, MODEL_SIZE, MODEL_SIZE), dtype=np.float32)
                scores[:, 1, :, MODEL_SIZE // 2:] = 1
                return [scores]

        def open_fixture(path, backend):
            self.assertEqual(backend, "directml")
            self.assertEqual(path, self.editor.checkpoints / MODEL.files[0].relative_path)
            self.assertFalse(any(session() is not None for session in sessions), "Two model sessions are live")
            session = Session()
            sessions.append(ref(session))
            return session

        return patch("retouching_tool.operations.person_foreground.open_session", new=open_fixture)

    def create(self, mode, name):
        destination = self.root / name
        destination.mkdir()
        return self.app.job_service.create(self.index, destination, mode=mode)

    def test_missing_asset_recovery_and_preview_overlay_are_local(self):
        stage = self.add_stage()
        self.assertFalse(stage.stage.enabled)
        self.assertEqual(stage.issues[0].code, "MODEL_MISSING")
        self.assertEqual(stage.issues[0].path, self.editor.checkpoints / MODEL.files[0].relative_path)
        self.assertEqual(stage.model_page, MODEL.publisher_url)
        self.install_test_asset()
        self.assertEqual(self.editor.recheck().stages[0].state, "skipped")
        self.editor.set_enabled(stage.stage.id, True)
        picture = self.app.preview_service.picture
        image_id = self.index.images[0].image_id
        sessions, runs = [], []
        with self.session_factory(sessions, runs):
            with runner_lock(self.root), self.assertRaises(RetouchingError) as busy:
                picture(self.index, image_id, "busy", stage_id=stage.stage.id)
            self.assertEqual(busy.exception.code, "JOB_RUNNER_BUSY")
            self.assertEqual(len(runs), 0)
            preview = picture(self.index, image_id, "overlay", stage_id=stage.stage.id)
            whole = picture(self.index, image_id, "whole", process=True)
        row_bytes = preview.result.width * 3
        self.assertEqual(preview.original.rgb[:row_bytes // 2], preview.result.rgb[:row_bytes // 2])
        self.assertNotEqual(preview.original.rgb[row_bytes // 2:row_bytes], preview.result.rgb[row_bytes // 2:row_bytes])
        self.assertEqual(preview.result_caption, "Detected person foreground")
        self.assertEqual(whole.original, whole.result)
        entry, = preview.context
        self.assertEqual(decode_context(encode_context(entry)), entry)
        bad = encode_context(entry)
        bad["value"]["data"]["labels"] = "!!!!" + bad["value"]["data"]["labels"][4:]
        with self.assertRaises(ValueError):
            decode_context(bad)
        self.assertTrue(all(session() is None for session in sessions))
        self.assertFalse(self.app.config.temporary.exists())

    def test_batch_session_ownership_and_staged_mask_recovery(self):
        self.install_test_asset()
        first, second = self.add_stage().stage, self.add_stage().stage
        jobs = self.app.job_service
        seq = self.create("sequential", "seq")
        staged = self.create("staged", "staged")
        original_process = PersonForeground.process
        masks = []

        def inspect(operation, state):
            if operation.stage.id == second.id:
                entry = state.entry(PERSON_FOREGROUND.key)
                self.assertEqual(entry.producer_id, first.id)
                self.assertTrue(entry.compatible(state))
                masks.append(entry.value.labels)
            return original_process(operation, state)

        control = JobControl()
        original_commit = JobJournal.commit_stage

        def pause_at_first_checkpoint(journal, *args):
            original_commit(journal, *args)
            control.pause.set()

        sessions, runs = [], []
        with self.session_factory(sessions, runs), patch.object(PersonForeground, "process", inspect):
            seq_result = jobs.run(seq)
            self.assertEqual(seq_result.status, "completed")
            self.assertEqual((len(sessions), len(runs)), (4, 4))
            with patch.object(JobJournal, "commit_stage", pause_at_first_checkpoint):
                paused = jobs.run(staged, control=control)
            self.assertEqual(paused.status, "paused")
            self.assertEqual((len(sessions), len(runs)), (5, 5))
            self.assertTrue(all(session() is None for session in sessions))
            staged_result = jobs.run(staged)
        self.assertEqual(staged_result.status, "completed")
        self.assertEqual((len(sessions), len(runs)), (7, 8))
        self.assertTrue(all(session() is None for session in sessions))
        self.assertEqual(len(masks), 4)
        self.assertTrue(all(mask == masks[0] for mask in masks))
        for left, right in zip(seq_result.images, staged_result.images, strict=True):
            with Image.open(left.output) as a, Image.open(right.output) as b:
                self.assertEqual((a.size, a.tobytes()), (b.size, b.tobytes()))

    def test_allocation_failure_is_accounted_for_and_the_next_image_runs(self):
        self.install_test_asset()
        stage = self.add_stage().stage
        for mode in ("sequential", "staged"):
            with self.subTest(mode=mode):
                sessions, runs = [], []
                job = self.create(mode, mode)
                with self.session_factory(sessions, runs, fail_first=True):
                    result = self.app.job_service.run(job)
                self.assertEqual(result.status, "completed_with_errors")
                failed, passed = result.images
                self.assertEqual((failed.code, passed.status), ("AI_INFERENCE_FAILED", "completed"))
                self.assertFalse(failed.output.exists())
                self.assertEqual(len(runs), 2)
                self.assertTrue(all(session() is None for session in sessions))
                evidence = next(event for event in self.log.records if event["code"] == "STAGE_PROCESS_FAILED"
                                and event.get("job_id") == job)
                self.assertEqual(evidence["stage_instance_id"], stage.id)
                self.assertEqual(evidence["image_id"], failed.image_id)
                self.assertIn("Generated inference allocation failure", evidence["exception"])

    def test_scaling_inheritance_preview_and_frozen_jobs(self):
        self.install_test_asset()
        stage = self.add_stage().stage
        self.editor.set_parameter(stage.id, "downscale", "nearest")
        old_job = self.create("staged", "old")
        self.app.save_settings(self.app.config.folders, self.app.config.logging, ScalingSettings("bilinear", "nearest"))
        new_job = self.create("sequential", "new")
        for job_id, expected_up in ((old_job, "bilinear"), (new_job, "nearest")):
            snapshot, _ = self.app.job_service.read_snapshot(job_id)
            self.assertEqual(dict(snapshot.pipeline.stages[0].parameters),
                             {"downscale": "nearest", "upscale": expected_up})
        portable = self.editor.status.definition.to_json()
        self.assertEqual(portable["stages"][0]["parameters"], {"downscale": "nearest"})
        for selection in (None, stage.id):
            _, stages = self.app.preview_service._stages(selection, False, None)
            self.assertEqual(dict(stages[0].parameters), {"downscale": "nearest", "upscale": "nearest"})
        with self.assertRaisesRegex(RetouchingError, "Unknown downscale"):
            self.editor.set_parameter(stage.id, "downscale", "typo")
        self.editor.load_data(portable)
        self.assertEqual(self.editor.status.definition.stages[0].parameter("upscale"), "inherit")
