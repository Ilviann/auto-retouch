"""Synthetic-only contracts and failure checks; no model/portrait acceptance."""

from dataclasses import FrozenInstanceError, replace
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from retouching_tool.application.pipeline_service import PipelineService
from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.json_io import atomic_json, read_json
from retouching_tool.operations.reference import EXPOSURE, LUMINANCE, Exposure, ReferenceOperation
from retouching_tool.pipeline.assets import ModelCatalog, ModelFile, ModelManifest
from retouching_tool.pipeline.definition import ContextSpec, OperationDescriptor, Parameter, parse_pipeline
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ContextEntry, ImageState, SourceMetadata
from retouching_tool.pipeline.registry import reference_registry

ROOT = Path(__file__).resolve().parents[1]


def stage(key="statistics", operation="analysis.image_statistics", *, enabled=True, parameters=None, model_ref=None):
    return {"id": key, "operation": operation, "operation_version": 1, "enabled": enabled,
            "parameters": parameters or {}, **({"model_ref": model_ref} if model_ref else {})}


def pipeline(*stages):
    return {"schema_version": 1, "name": "Synthetic contract fixture", "stages": list(stages)}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-work").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-work", prefix="pipeline пример ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = Diagnostics(self.root / "logs", console=io.StringIO())
        self.addCleanup(self.log.close)
        self.registry = reference_registry()
        self.catalog = ModelCatalog()
        self.editor = PipelineService(self.root / "models", "directml", self.log, autosave=self.root / "last.json",
                                      registry=self.registry, catalog=self.catalog)
        self.state = ImageState.from_pixels(np.array([[[0, 0, 0], [.25, .25, .25]], [[.5, .5, .5], [1, 1, 1]]], dtype=np.float32),
                                            SourceMetadata("synthetic-image", b"EXIF fixture", b"ICC fixture"))

    def run_stage(self, raw, state=None):
        definition = parse_pipeline(pipeline(raw), self.registry).stages[0]
        runtime = StageRuntime(self.log, "directml", self.root / "models", "job-test", definition)
        with StageSession(self.registry, self.editor.catalog, runtime) as session:
            return session.process(self.state if state is None else state, image_id="image-test")

    def register(self, descriptor, factory=None):
        if factory is None:
            class Noop(ReferenceOperation):
                def process(self, state):
                    return state
            factory = Noop
        self.registry.register(descriptor, factory)

    def test_json_roundtrip_defaults_multiple_instances_and_immutable_snapshot(self):
        data = pipeline(stage(), stage("exposure", "core.exposure"), stage("again"))
        status = self.editor.load_data(data)
        snapshot = self.editor.preflight()
        self.assertEqual(snapshot.stages[1].parameter("stops"), 0)
        self.assertEqual(parse_pipeline(read_json(self.root / "last.json"), self.registry), snapshot)
        self.editor.set_parameters("exposure", {"stops": 1})
        self.editor.set_enabled("exposure", False)
        self.assertTrue(snapshot.stages[1].enabled)
        self.assertEqual(snapshot.stages[1].parameter("stops"), 0)
        self.editor.reorder(("again", "exposure", "statistics"))
        self.assertEqual([s.id for s in self.editor.status.definition.stages], ["again", "exposure", "statistics"])
        self.editor.remove("again")
        self.editor.add(stage("new"))
        self.assertEqual(self.editor.status.definition.stages[0].parameter("stops"), 1)
        self.assertEqual(snapshot.stages[1].parameter("stops"), 0)
        with self.assertRaises(FrozenInstanceError):
            snapshot.name = "changed"

    def test_invalid_import_and_edit_preserve_current_and_autosave(self):
        self.editor.load_data(pipeline(stage()))
        old, saved = self.editor.status, (self.root / "last.json").read_bytes()
        invalid = [[], {}, {**pipeline(), "schema_version": True}, {**pipeline(), "schema_version": 9},
                   pipeline(stage(), stage()), pipeline({"id": "bad"}), pipeline([]),
                   pipeline({**stage(), "extra": 2}), pipeline({**stage(), "enabled": 1}),
                   pipeline({**stage(), "operation_version": True}), pipeline(stage(operation="unknown")),
                   pipeline({**stage(), "operation_version": 2}),
                   pipeline(stage("e", "core.exposure", parameters={"stops": True})),
                   pipeline(stage("e", "core.exposure", parameters={"stops": 5})),
                   pipeline(stage("e", "core.exposure", parameters={"stops": 10 ** 1000})),
                   pipeline(stage("e", "core.exposure", parameters={"stops": float("nan")})),
                   pipeline(stage("e", "core.exposure", parameters={"extra": 2})),
                   pipeline({**stage(), "model_ref": "unexpected"})]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(RetouchingError):
                self.editor.load_data(data)
            self.assertIs(self.editor.status, old)
            self.assertEqual((self.root / "last.json").read_bytes(), saved)
        with self.assertRaises(RetouchingError):
            self.editor.reorder(("wrong",))
        with self.assertRaises(RetouchingError):
            self.editor.set_enabled("absent", True)
        with patch("retouching_tool.json_io.os.replace", side_effect=PermissionError("simulated atomic-save denial")):
            with self.assertRaises(RetouchingError):
                self.editor.set_enabled("statistics", False)
        self.assertIs(self.editor.status, old)
        self.assertEqual((self.root / "last.json").read_bytes(), saved)
        self.assertIn("simulated atomic-save denial", self.log.records[-1]["exception"])

    def install_model_fixture(self):
        content = b"synthetic checkpoint identity fixture, never inference"
        manifest = ModelManifest("test-model", "fixture-1", (ModelFile("fixture.bin", hashlib.sha256(content).hexdigest()),),
                                 ("directml",), "https://example.test/model", "Synthetic test bytes authored for tests; no production weights")
        self.editor.catalog = ModelCatalog((manifest,))
        descriptor = OperationDescriptor("test.analysis", 1, "Synthetic model analysis", provides=(LUMINANCE,), model_refs=("test-model",))
        self.register(descriptor)
        consumer = OperationDescriptor("test.consumer", 1, "Required-context consumer", requires=(LUMINANCE,))
        self.register(consumer)
        return content

    def test_asset_disable_dependency_and_explicit_recovery(self):
        content = self.install_model_fixture()
        data = pipeline(stage("model", "test.analysis", model_ref="test-model"), stage("consumer", "test.consumer"))
        explicit = self.root / "explicit.json"
        atomic_json(explicit, data)
        before = explicit.read_bytes()
        status = self.editor.load(explicit)
        self.assertFalse(status.definition.stages[0].enabled)
        self.assertEqual([s.state for s in status.stages], ["unavailable", "blocked"])
        self.assertEqual(status.stages[0].issues[0].code, "MODEL_MISSING")
        self.assertIn("fixture.bin", status.stages[0].issues[0].message)
        self.assertEqual(status.stages[0].model_page, "https://example.test/model")
        self.assertEqual(explicit.read_bytes(), before)
        self.assertFalse(read_json(self.root / "last.json")["stages"][0]["enabled"])
        with self.assertRaises(RetouchingError):
            self.editor.set_enabled("model", True)
        with self.assertRaises(RetouchingError):
            self.editor.preflight()
        self.editor.set_enabled("consumer", False)
        self.assertTrue(self.editor.status.executable)
        path = self.root / "models" / "fixture.bin"
        path.parent.mkdir()
        path.write_bytes(b"wrong bytes")
        self.assertEqual(self.editor.recheck().stages[0].issues[0].code, "MODEL_CHECKSUM_INVALID")
        path.write_bytes(content)
        status = self.editor.recheck()
        self.assertEqual(status.stages[0].state, "skipped")
        self.assertFalse(status.definition.stages[0].enabled)
        self.editor.set_enabled("model", True)
        self.editor.set_enabled("consumer", True)
        self.assertTrue(self.editor.status.executable)
        self.editor.reorder(("consumer", "model"))
        self.assertFalse(self.editor.status.executable)
        self.editor.reorder(("model", "consumer"))
        path.unlink()
        with self.assertRaises(RetouchingError) as failure:
            self.run_stage(stage("model", "test.analysis", model_ref="test-model"))
        self.assertEqual(failure.exception.code, "STAGE_UNAVAILABLE")
        self.assertFalse(self.editor.recheck().definition.stages[0].enabled)
        codes = [e["code"] for e in self.log.records]
        self.assertIn("STAGE_ASSET_RECOVERED", codes)
        self.assertIn("STAGE_CONTEXT_BLOCKED", codes)

    def test_assets_unknown_backend_unreadable_and_unsafe_paths(self):
        content = self.install_model_fixture()
        self.assertEqual(self.editor.catalog.check("unknown", self.root, "directml")[0].code, "MODEL_UNKNOWN")
        self.assertEqual(self.editor.catalog.check("test-model", self.root, "cuda")[0].code, "MODEL_BACKEND_UNSUPPORTED")
        with patch("pathlib.Path.resolve", side_effect=PermissionError("simulated access denial")):
            issue = self.editor.catalog.check("test-model", self.root, "directml")[0]
            self.assertEqual(issue.code, "MODEL_UNREADABLE")
            self.assertIn("simulated access denial", issue.message)
            self.assertIn("PermissionError", issue.technical_cause)
        with patch("pathlib.Path.resolve", side_effect=[self.root, self.root.parent / "outside.bin"]):
            self.assertEqual(self.editor.catalog.check("test-model", self.root, "directml")[0].code, "MODEL_PATH_INVALID")
        multiple = ModelManifest("multiple", "fixture-1", (ModelFile("a.bin", hashlib.sha256(content).hexdigest()),
            ModelFile("b.bin", hashlib.sha256(content).hexdigest())), ("directml",), "https://example.test/model", "Authored synthetic test bytes")
        self.assertEqual(len(ModelCatalog((multiple,)).check("multiple", self.root, "directml")), 2)
        for path in ("../outside", "C:/absolute", "/absolute", "a:stream", "\\\\host\\share\\asset"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                ModelFile(path, hashlib.sha256(content).hexdigest())

    def test_statistics_no_copy_and_exposure_precision_metadata_invalidation(self):
        measured = self.run_stage(stage())
        self.assertIs(measured.pixels, self.state.pixels)
        self.assertIs(measured.metadata, self.state.metadata)
        stats = measured.entry(LUMINANCE.key).value
        self.assertAlmostEqual(stats.mean_luminance, .4375, places=6)
        self.assertEqual(sum(stats.histogram), 4)
        self.assertEqual(stats.clipped_low, 1)
        self.assertEqual(stats.clipped_high, 1)
        after = self.run_stage(stage("e", "core.exposure", parameters={"stops": 1}), measured)
        np.testing.assert_array_equal(after.pixels, np.minimum(self.state.pixels * 2, 1))
        self.assertEqual(after.pixels.dtype, np.float32)
        self.assertEqual(after.pixel_revision, 1)
        self.assertIs(after.metadata, self.state.metadata)
        self.assertIsNone(after.entry(LUMINANCE.key))
        np.testing.assert_array_equal(self.state.pixels[0, 1], [.25, .25, .25])
        with self.assertRaises(ValueError):
            self.state.pixels.flags.writeable = True
        with self.assertRaises(ValueError):
            self.state.pixels[0, 0] = 1
        codes = [event["code"] for event in self.log.records]
        self.assertIn("EXPOSURE_CLIPPED", codes)
        self.assertIn("IMAGE_CLIPPING_DETECTED", codes)
        for event in self.log.records:
            if event["code"] in ("STAGE_COMPLETED", "EXPOSURE_CLIPPED", "IMAGE_STATISTICS"):
                self.assertEqual(event["job_id"], "job-test")
                self.assertEqual(event["image_id"], "image-test")
                self.assertIn("stage_instance_id", event)

    def test_pass_through_disabled_and_zero_stops(self):
        self.assertIs(self.run_stage(stage(enabled=False)), self.state)
        result = self.run_stage(stage("e", "core.exposure"))
        self.assertIs(result.pixels, self.state.pixels)
        self.assertEqual(result.pixel_revision, 0)
        self.assertIn("STAGE_SKIPPED", [r["code"] for r in self.log.records])

    def test_missing_stale_and_wrong_version_required_context_fail(self):
        self.register(OperationDescriptor("test.needs_stats", 1, "Consumer", requires=(LUMINANCE,)))
        measured = self.run_stage(stage())
        for state in (self.state, replace(measured, pixel_revision=1), replace(measured, geometry_revision=1),
                      replace(measured, metadata=SourceMetadata("different-image")),
                      replace(measured, context=(replace(measured.context[0], spec=ContextSpec(LUMINANCE.key, 2)),))):
            with self.subTest(state=state.pixel_revision), self.assertRaises(RetouchingError) as error:
                self.run_stage(stage("consumer", "test.needs_stats"), state)
            self.assertEqual(error.exception.code, "CONTEXT_REQUIRED")
        self.assertIn("CONTEXT_REQUIRED", self.log.records[-2]["exception"])

    def test_static_pixel_and_geometry_context_invalidation(self):
        self.register(OperationDescriptor("test.consumer", 1, "Consumer", requires=(LUMINANCE,)))
        status = self.editor.load_data(pipeline(stage(), stage("e", "core.exposure", parameters={"stops": 1}), stage("c", "test.consumer")))
        self.assertEqual(status.stages[-1].state, "blocked")
        self.editor.set_enabled("e", False)
        self.assertTrue(self.editor.status.executable)
        self.editor.set_enabled("statistics", False)
        self.assertFalse(self.editor.status.executable)
        spatial = ContextSpec("test.spatial", pixel_sensitive=False)
        self.register(OperationDescriptor("test.spatial", 1, "Spatial provider", provides=(spatial,)))
        self.register(OperationDescriptor("test.spatial_consumer", 1, "Spatial consumer", requires=(spatial,)))
        self.register(replace(EXPOSURE, operation="test.preserving_exposure", preserves=(spatial.key,)))
        self.register(OperationDescriptor("test.geometry", 1, "Geometry edit", changes_pixels=True, changes_geometry=True))
        status = self.editor.load_data(pipeline(stage("s", "test.spatial"), stage("e", "test.preserving_exposure"), stage("c", "test.spatial_consumer")))
        self.assertTrue(status.executable)
        self.editor.add(stage("g", "test.geometry"))
        self.editor.reorder(("s", "e", "g", "c"))
        self.assertFalse(self.editor.status.executable)

    def test_shared_stage_pass_and_fresh_stage_per_image_match(self):
        raw = stage("e", "core.exposure", parameters={"stops": -.5})
        definition = parse_pipeline(pipeline(raw), self.registry).stages[0]
        inputs = [self.state, ImageState.from_pixels(np.full((3, 5, 3), .3, dtype=np.float32), self.state.metadata)]
        fresh = [self.run_stage(raw, image) for image in inputs]
        runtime = StageRuntime(self.log, "directml", self.root, "stage-pass", definition)
        with StageSession(self.registry, self.catalog, runtime) as session:
            shared = [session.process(image, image_id=f"fixture-{index}") for index, image in enumerate(inputs)]
        for first, second in zip(fresh, shared):
            np.testing.assert_array_equal(first.pixels, second.pixels)
            self.assertEqual(first.context, second.context)
            self.assertEqual(first.metadata, second.metadata)

    def test_explicit_spatial_preservation_and_geometry_invalidation_at_runtime(self):
        spatial = ContextSpec("test.spatial", pixel_sensitive=False)
        self.register(OperationDescriptor("test.spatial", 1, "Synthetic spatial context", provides=(spatial,)))
        self.register(replace(EXPOSURE, operation="test.preserve", preserves=(spatial.key,)), Exposure)
        context = ContextEntry(spatial, "producer", "test.spatial", 1, 0, 0, self.state.width, self.state.height, (),
                               source_id=self.state.metadata.source_id, producer_settings_hash="a" * 64)
        original = replace(self.state, context=(context,))
        changed = self.run_stage(stage("e", "test.preserve", parameters={"stops": -1}), original)
        self.assertIs(changed.entry(spatial.key), context)
        self.assertEqual(changed.pixel_revision, 1)
        class Resize(ReferenceOperation):
            def process(self, state):
                return ImageState.from_pixels(state.pixels[:1, :1], state.metadata, pixel_revision=state.pixel_revision + 1,
                                              geometry_revision=state.geometry_revision + 1)
        self.register(OperationDescriptor("test.resize", 1, "Synthetic crop", changes_pixels=True, changes_geometry=True), Resize)
        cropped = self.run_stage(stage("crop", "test.resize"), changed)
        self.assertIsNone(cropped.entry(spatial.key))
        self.assertEqual((cropped.width, cropped.height), (1, 1))

    def test_optional_stale_context_is_absent_and_fallback_is_visible(self):
        observed = []
        class Fallback(ReferenceOperation):
            def process(self, state):
                observed.append(state.entry(LUMINANCE.key))
                if observed[-1] is None:
                    self.runtime.emit("SYNTHETIC_OPTIONAL_FALLBACK", "Synthetic no-op fallback: optional measurements absent", level="WARNING")
                return state
        self.register(OperationDescriptor("test.optional", 1, "Optional-context fixture", optional=(LUMINANCE,)), Fallback)
        measured = self.run_stage(stage())
        self.run_stage(stage("optional", "test.optional"), replace(measured, pixel_revision=1))
        self.assertIsNone(observed[-1])
        self.assertTrue(any(event["code"] == "SYNTHETIC_OPTIONAL_FALLBACK" and event["image_id"] == "image-test" for event in self.log.records))

    def test_prepare_process_release_failures_preserve_causes_and_release(self):
        for where in ("prepare", "prepare_interrupt", "process", "release", "process_and_release"):
            calls = []
            class Failing(ReferenceOperation):
                def prepare(self, runtime):
                    super().prepare(runtime)
                    calls.append("prepare")
                    if where == "prepare":
                        raise OSError("simulated preparation failure")
                    if where == "prepare_interrupt":
                        raise KeyboardInterrupt("simulated interruption during preparation")
                def process(self, state):
                    calls.append("process")
                    if where.startswith("process"):
                        try:
                            raise MemoryError("simulated bounded worker memory exhaustion")
                        except MemoryError as exc:
                            raise RetouchingError("SIMULATED_PROCESS_FAILURE", "simulated processing failure") from exc
                    return state
                def release(self):
                    calls.append("release")
                    if where in ("release", "process_and_release"):
                        raise OSError("simulated release failure")
            name = f"test.{where}"
            self.register(OperationDescriptor(name, 1, name), Failing)
            with self.subTest(where=where), self.assertRaises(KeyboardInterrupt if where == "prepare_interrupt" else Exception) as error:
                self.run_stage(stage("failure", name))
            self.assertEqual(calls[-1], "release")
            if where.startswith("process"):
                self.assertEqual(error.exception.code, "SIMULATED_PROCESS_FAILURE")
                event = next(e for e in reversed(self.log.records) if e["code"] == "STAGE_PROCESS_FAILED")
                self.assertIn("MemoryError", event["exception"])
                self.assertEqual(event["image_id"], "image-test")
            self.assertTrue(any(e["code"] == f"STAGE_{'PREPARE' if where.startswith('prepare') else 'RELEASE' if where == 'release' else 'PROCESS'}_FAILED" for e in self.log.records))

    def test_bad_stage_result_rejected(self):
        class Bad(ReferenceOperation):
            def process(self, state):
                return replace(state, metadata=SourceMetadata("replacement"))
        self.register(OperationDescriptor("test.bad", 1, "Bad result"), Bad)
        with self.assertRaises(RetouchingError) as error:
            self.run_stage(stage("bad", "test.bad"))
        self.assertEqual(error.exception.code, "STAGE_RESULT_INVALID")

    def test_invalid_working_pixels_rejected_and_caller_array_is_owned(self):
        for array in (np.zeros((1, 1, 3), dtype=np.uint8), np.zeros((0, 1, 3), dtype=np.float32),
                      np.full((1, 1, 3), np.nan, dtype=np.float32), np.full((1, 1, 3), 1.1, dtype=np.float32)):
            with self.assertRaises(RetouchingError):
                ImageState.from_pixels(array, self.state.metadata)
        original = np.full((2, 2, 3), .5, dtype=np.float32)
        state = ImageState.from_pixels(original, self.state.metadata)
        original[:] = 0
        self.assertEqual(float(state.pixels[0, 0, 0]), .5)

    def test_nonempty_startup_restore_and_explicit_file_preservation(self):
        explicit = self.root / "pipeline.json"
        atomic_json(explicit, pipeline(stage(), stage("e", "core.exposure", parameters={"stops": .25})))
        service = ApplicationService(self.root, load_config(self.root), self.log)
        snapshot = service.startup(LaunchOptions((), explicit, None), "directml")
        self.assertEqual(len(snapshot.stages), 2)
        self.assertFalse(snapshot.processing_available)
        service.pipeline_service.set_enabled("e", False)
        restored = ApplicationService(self.root, load_config(self.root), self.log).startup(LaunchOptions((), None, None), "directml")
        self.assertFalse(restored.stages[1].stage.enabled)
        self.assertEqual(restored.stages[1].stage.parameter("stops"), .25)
        self.assertTrue(read_json(explicit)["stages"][1]["enabled"])

    def test_headless_registry_service_and_executor_imports_remain_lazy(self):
        code = """
import sys
class RejectHeavy:
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PySide6', 'shiboken6', 'onnxruntime', 'numpy')):
            raise RuntimeError('Heavy import crossed boundary: ' + fullname)
sys.meta_path.insert(0, RejectHeavy())
from retouching_tool.application.pipeline_service import PipelineService
from retouching_tool.pipeline.registry import reference_registry
from retouching_tool.pipeline.executor import StageSession
from retouching_tool.pipeline.image_state import ImageState
from retouching_tool.pipeline.definition import parse_pipeline
registry = reference_registry()
parse_pipeline({'schema_version': 1, 'name': 'Headless', 'stages': []}, registry)
assert len(registry.descriptors()) == 2
"""
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
