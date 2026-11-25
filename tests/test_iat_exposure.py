"""Exercise IAT's two-file manifest, color conversion and shape-bound sessions."""

from dataclasses import replace
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from retouching_tool.application.pipeline_service import PipelineService
from retouching_tool.errors import RetouchingError
from retouching_tool.operations.iat_exposure import DESCRIPTOR, MODEL
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ImageState, SourceMetadata


class IatExposureTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.log = Mock()
        self.editor = PipelineService(self.root, "directml", self.log)
        self.stage = self.editor.add_operation(DESCRIPTOR.operation, 1, model_ref=MODEL.ref).stages[0].stage

    def install_fixture(self):
        files = []
        for item in MODEL.files:
            path = self.root / item.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = path.name.encode()
            path.write_bytes(payload)
            files.append(replace(item, sha256=hashlib.sha256(payload).hexdigest()))
        self.editor.catalog = ModelCatalog((replace(MODEL, files=tuple(files)),))
        self.editor.recheck()

    def test_both_files_required_and_recovery_preserves_settings(self):
        status, = self.editor.status.stages
        self.assertFalse(status.stage.enabled)
        self.assertEqual([item.path for item in status.issues], [self.root / f.relative_path for f in MODEL.files])
        self.assertEqual(status.model_page, MODEL.publisher_url)
        self.assertEqual(DESCRIPTOR.backends, MODEL.backends)
        self.editor.set_parameter(self.stage.id, "analysis_size", 384)
        self.install_fixture()
        self.assertFalse(self.editor.status.stages[0].stage.enabled)
        self.editor.set_enabled(self.stage.id, True)
        path = self.root / MODEL.files[1].relative_path
        path.write_bytes(b"wrong weights")
        status, = self.editor.recheck().stages
        self.assertEqual([i.code for i in status.issues], ["MODEL_CHECKSUM_INVALID"])
        self.assertFalse(status.stage.enabled)
        path.write_bytes(path.name.encode())
        status, = self.editor.recheck().stages
        self.assertEqual(status.state, "skipped")
        self.assertEqual(status.stage.parameter("analysis_size"), 384)

    def session(self, **parameters):
        self.install_fixture()
        self.editor.set_enabled(self.stage.id, True)
        self.editor.set_parameters(self.stage.id, parameters)
        stage, = self.editor.resolve_scaling(self.editor.preflight()).stages
        runtime = StageRuntime(self.log, "directml", self.root, "job", stage)
        return StageSession(self.editor.registry, self.editor.catalog, runtime)

    def fake_model(self, transform):
        def create(path, backend, *, free_dimensions):
            self.assertEqual((path, backend), (self.root / MODEL.files[0].relative_path, "directml"))
            shape = [1, 3, free_dimensions["height"], free_dimensions["width"]]
            def run(outputs, inputs):
                self.assertEqual(outputs, ["enhanced"])
                tensor = inputs["input"]
                self.assertEqual(list(tensor.shape), shape)
                self.assertEqual(tensor.dtype, np.float32)
                self.assertTrue(tensor.flags.c_contiguous)
                return [transform(tensor)]
            return SimpleNamespace(get_inputs=lambda: [SimpleNamespace(name="input", type="tensor(float)", shape=shape)],
                                   get_outputs=lambda: [SimpleNamespace(name="enhanced", type="tensor(float)")],
                                   get_providers=lambda: ["DmlExecutionProvider"], run=run)
        return patch("retouching_tool.operations.iat_exposure.open_session", side_effect=create)

    def test_native_samples_and_blending_use_the_correct_transfer_function(self):
        source = np.array([[[0, .003, .5], [.8, .1, 1]]], dtype=np.float32)
        codes = np.where(source <= .0031308, 12.92 * source, 1.055 * source**(1/2.4) - .055)
        def transform(tensor):
            np.testing.assert_allclose(tensor, codes.transpose(2, 0, 1)[None], atol=1e-7)
            return np.full_like(tensor, .5)
        state = ImageState.from_pixels(source, SourceMetadata("source", exif=b"capture"), geometry_revision=2)
        with self.fake_model(transform), self.session(blend=.5) as session:
            result = session.process(state, image_id="photo")
        expected = (source + ((.5 + .055) / 1.055)**2.4) * .5
        np.testing.assert_allclose(result.pixels, expected, atol=1e-7)
        self.assertIs(result.metadata, state.metadata)
        self.assertEqual((result.geometry_revision, result.pixel_revision), (2, 1))

    def test_zero_correction_keeps_native_detail_and_reuses_only_the_matching_shape(self):
        with self.fake_model(lambda tensor: tensor) as create, self.session(analysis_size=128) as session:
            for h, w in ((257, 129), (257, 129), (9, 1)):
                source = np.random.default_rng(9).random((h, w, 3), dtype=np.float32)
                state = ImageState.from_pixels(source, SourceMetadata("photo"))
                result = session.process(state, image_id="photo")
                np.testing.assert_allclose(result.pixels, source, atol=2e-7)
            self.assertEqual([c.kwargs["free_dimensions"] for c in create.call_args_list],
                             [dict(batch=1, height=128, width=64), dict(batch=1, height=9, width=1)])

    def test_spatial_correction_maps_back_to_original_pixels(self):
        # A dark/light checkerboard is finer than the analysis grid. The fixture
        # moves the two sides in opposite directions by a known linear increment.
        values = .25 + .125 * (np.indices((128, 256)).sum(axis=0) % 2)
        source = np.repeat(values[:, :, None], 3, axis=2).astype(np.float32)
        def transform(tensor):
            guide = ((tensor + .055) / 1.055)**2.4
            guide[:, :, :, :64] += .1
            guide[:, :, :, 64:] -= .1
            return 1.055 * guide**(1/2.4) - .055
        with self.fake_model(transform), self.session(analysis_size=128, upscale="nearest") as session:
            state = ImageState.from_pixels(source, SourceMetadata("photo"))
            result = session.process(state, image_id="photo")
        expected = source.copy()
        expected[:, :128] += .1
        expected[:, 128:] -= .1
        np.testing.assert_allclose(result.pixels, expected, atol=2e-7)

    def test_invalid_output_and_provider_failure_preserve_diagnostics(self):
        state = ImageState.from_pixels(np.zeros((1, 1, 3), np.float32), SourceMetadata("photo"))
        def fail(_):
            raise RuntimeError("device allocation failed")
        for transform, code in ((lambda x: x[:, :, :, :0], "AI_MODEL_CONTRACT_INVALID"),
                                (lambda x: x + np.nan, "AI_MODEL_CONTRACT_INVALID"),
                                (fail, "AI_INFERENCE_FAILED")):
            with self.subTest(code=code), self.fake_model(transform), self.session() as session:
                with self.assertRaises(RetouchingError) as caught:
                    session.process(state, image_id="photo")
                self.assertEqual(caught.exception.code, code)
                if transform is fail:
                    self.assertIsInstance(caught.exception.__cause__, RuntimeError)
                event = self.log.emit.call_args
                self.assertEqual(event.args[0], "STAGE_PROCESS_FAILED")
                self.assertTrue(event.kwargs["exc_info"])
                self.assertEqual((event.kwargs["stage_instance_id"], event.kwargs["image_id"]),
                                 (self.stage.id, "photo"))
