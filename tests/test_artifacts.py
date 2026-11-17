"""Lossless TIFF artifacts and simulated failures, not portrait-quality acceptance."""

from dataclasses import asdict, replace
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.operations.reference import LUMINANCE
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.definition import ContextSpec, parse_pipeline
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ContextEntry, ImageState, SourceMetadata
from retouching_tool.pipeline.registry import reference_registry
from retouching_tool.storage.artifacts import ArtifactIdentity, ArtifactRef, ArtifactStore, fingerprint

ROOT = Path(__file__).resolve().parents[1]


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-work").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-work", prefix="artifacts пример ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = Diagnostics(self.root / "logs", console=io.StringIO())
        self.addCleanup(self.log.close)
        self.registry, self.catalog = reference_registry(), ModelCatalog()
        raw = {"schema_version": 1, "name": "Artifact reference", "stages": [
            {"id": "stats", "operation": "analysis.image_statistics", "operation_version": 1, "enabled": True, "parameters": {}},
            {"id": "exposure", "operation": "core.exposure", "operation_version": 1, "enabled": True, "parameters": {"stops": .25}},
            {"id": "stats-after", "operation": "analysis.image_statistics", "operation_version": 1, "enabled": True, "parameters": {}}]}
        self.pipeline = parse_pipeline(raw, self.registry)
        self.runtime = StageRuntime(self.log, "directml", self.root / "models", "job-1", self.pipeline.stages[0], "image-1")
        self.store = ArtifactStore.create(self.root / "scratch", "job-1", self.log)
        pixels = np.random.default_rng(42).random((19, 23, 3), dtype=np.float32)
        pixels[0, 0] = [np.nextafter(np.float32(.5), np.float32(1)), np.finfo(np.float32).smallest_subnormal, -0.0]
        self.state = ImageState.from_pixels(pixels, SourceMetadata("source-1", b"Exif\0\0\xff\x80 original MakerNote fixture",
                                                                  b"opaque source profile fixture"))
        self.identity = ArtifactIdentity("source-1", fingerprint("original-file-bytes"), fingerprint(raw),
            fingerprint({"backend": "directml", "device": "reference-cpu", "numpy": np.__version__, "precision": "float32"}),
            fingerprint({}), fingerprint({"image.luminance": asdict(LUMINANCE)}), 42)

    def run_stage(self, stage, state):
        runtime = replace(self.runtime, stage=stage)
        with StageSession(self.registry, self.catalog, runtime) as session:
            return session.process(state, image_id="image-1")

    def commit(self, state=None, identity=None):
        return self.store.commit(self.state if state is None else state, identity or self.identity, runtime=self.runtime)

    def manifest(self, ref):
        return self.store.directory / "states" / (ref.key + ".json")

    def rewrite(self, ref, mutate):
        path = self.manifest(ref)
        data = json.loads(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(json.dumps(data), encoding="utf-8")
        return replace(ref, manifest_sha256=fingerprint(data))

    def test_float32_tiff_bit_exact_roundtrip_metadata_context_and_reopen(self):
        import tifffile
        state = self.run_stage(self.pipeline.stages[0], self.state)
        ref = self.commit(state)
        reopened = ArtifactStore.open(self.store.directory, "job-1", self.log)
        result = reopened.read(ref, self.identity, runtime=self.runtime)
        self.assertEqual(result.pixels.tobytes(), state.pixels.tobytes())
        self.assertEqual(result.metadata, state.metadata)
        self.assertEqual(result.context, state.context)
        self.assertEqual(result.representation, state.representation)
        self.assertFalse(result.pixels.flags.writeable)
        with self.assertRaises(ValueError):
            result.pixels.setflags(write=True)
        path = next((self.store.directory / "blobs").glob("*.tif"))
        with tifffile.TiffFile(path) as tiff:
            self.assertEqual(tiff.pages[0].dtype, np.dtype("float32"))
            self.assertEqual(tiff.pages[0].compression, 1)
            self.assertEqual(tiff.pages[0].shape, state.pixels.shape)
            self.assertEqual(tiff.pages[0].tags["Software"].value, "Auto ReTouch 0.1.0")
        self.assertEqual(tifffile.imread(path).tobytes(), state.pixels.tobytes())

    def test_no_exif_or_profile_remains_empty(self):
        state = replace(self.state, metadata=SourceMetadata("source-1"))
        result = self.store.read(self.commit(state), self.identity)
        self.assertEqual(result.metadata.exif, b"")
        self.assertEqual(result.metadata.icc_profile, b"")
        self.assertEqual((result.metadata.xmp, result.metadata.iptc), (b"", b""))

    def test_optional_jpeg_metadata_survives_reopen_and_collection(self):
        state = replace(self.state, metadata=replace(self.state.metadata, xmp=b"original XMP", iptc=b"original IPTC"))
        ref = self.commit(state)
        reopened = ArtifactStore.open(self.store.directory, "job-1", self.log)
        self.assertEqual(reopened.read(ref, self.identity, runtime=self.runtime).metadata, state.metadata)
        self.assertEqual(reopened.collect(), 0)
        self.assertEqual(self.commit(state), ref)

        data = json.loads(self.manifest(ref).read_text(encoding="utf-8"))
        blob = self.store.directory / "blobs" / (data["xmp"]["sha256"] + ".bin")
        blob.write_bytes(b"corrupted XMP")
        with self.assertRaises(RetouchingError) as failure:
            reopened.read(ref, self.identity, runtime=self.runtime)
        self.assertEqual(failure.exception.code, "ARTIFACT_INVALID")

    def test_equal_samples_with_different_shapes_use_distinct_tiffs(self):
        first = self.commit()
        state = ImageState.from_pixels(self.state.pixels.reshape(self.state.width, self.state.height, 3), self.state.metadata)
        identity = replace(self.identity, pipeline_prefix_sha256=fingerprint("different-geometry"))
        second = self.commit(state, identity)
        self.assertEqual(len(list((self.store.directory / "blobs").glob("*.tif"))), 2)
        self.assertEqual(self.store.read(first, self.identity).pixels.shape, self.state.pixels.shape)
        self.assertEqual(self.store.read(second, identity).pixels.shape, state.pixels.shape)

    def test_spatial_measurements_provenance_survives_photometric_revision(self):
        entry = ContextEntry(ContextSpec("test.spatial", 2, False), "detector", "test.detector", 3, 0, 0,
            self.state.width, self.state.height, (1., 2., 3.), source_id="source-1", producer_settings_hash=fingerprint("settings"),
            confidence=.75, model_ref="test-model-v2")
        state = replace(self.state, pixel_revision=2, context=(entry,))
        result = self.store.read(self.commit(state), self.identity)
        self.assertEqual(result.context, state.context)
        self.assertEqual(result.pixel_revision, 2)

    def test_identity_changes_invalidate_cache_and_are_not_paths(self):
        ref = self.commit()
        variants = [replace(self.identity, **{field: fingerprint(field)}) for field in (
            "source_sha256", "pipeline_prefix_sha256", "environment_sha256", "model_assets_sha256", "dependencies_sha256")]
        variants += [replace(self.identity, seed=43), replace(self.identity, source_id="another-source")]
        for identity in variants:
            self.assertNotEqual(identity.key, self.identity.key)
            with self.subTest(identity=identity), self.assertRaises(RetouchingError):
                self.store.read(ref, identity, runtime=self.runtime)
        self.assertEqual(fingerprint({"b": 2, "a": 1}), fingerprint({"a": 1, "b": 2}))
        with self.assertRaises(ValueError):
            ArtifactRef(self.store.marker["store_id"], "../outside", "0" * 64)
        with self.assertRaises(ValueError):
            replace(self.identity, seed=True)

    def test_idempotent_commit_and_conflict_does_not_replace_state(self):
        ref = self.commit()
        self.assertEqual(self.commit(), ref)
        before = self.manifest(ref).read_bytes()
        with self.assertRaises(RetouchingError):
            self.commit(replace(self.state, pixel_revision=1))
        self.assertEqual(self.manifest(ref).read_bytes(), before)
        self.store.read(ref, self.identity)

    def test_context_only_reuses_tiff_and_collection_retains_shared_pixels(self):
        first = self.commit()
        state = self.run_stage(self.pipeline.stages[0], self.state)
        identity = replace(self.identity, pipeline_prefix_sha256=fingerprint("analysis-prefix"))
        second = self.commit(state, identity)
        self.assertEqual(len(list((self.store.directory / "blobs").glob("*.tif"))), 1)
        self.store.discard(first)
        self.assertEqual(self.store.collect(), 0)
        self.store.read(second, identity)
        outsider = self.store.directory / "blobs" / "user-notes.txt"
        outsider.write_text("keep", encoding="utf-8")
        pending = self.store.directory / "blobs" / ("a" * 32 + ".pending")
        pending.write_bytes(b"interrupted write evidence")
        self.store.discard(second)
        self.assertEqual(self.store.collect(), 3)  # TIFF, EXIF and profile.
        self.assertTrue(outsider.exists())
        self.assertTrue(pending.exists())
        self.assertTrue((self.store.directory / "owner.json").exists())

    def test_manifest_last_failure_preserves_predecessor_and_orphans_are_collectable(self):
        predecessor = self.commit()
        state = self.run_stage(self.pipeline.stages[1], self.state)
        identity = replace(self.identity, pipeline_prefix_sha256=fingerprint("exposure-prefix"))
        with patch("retouching_tool.storage.artifacts.atomic_json", side_effect=OSError("simulated disk full during manifest commit")):
            with self.assertRaises(RetouchingError) as caught:
                self.commit(state, identity)
        self.assertEqual(caught.exception.code, "ARTIFACT_WRITE_FAILED")
        self.assertFalse((self.store.directory / "states" / (identity.key + ".json")).exists())
        self.store.read(predecessor, self.identity)
        self.assertEqual(self.store.collect(), 1)
        event = next(e for e in self.log.records if e["code"] == "ARTIFACT_WRITE_FAILED")
        self.assertIn("simulated disk full", event["exception"])
        self.assertEqual((event["job_id"], event["image_id"], event["stage_instance_id"]), ("job-1", "image-1", "stats"))

    def test_partial_tiff_write_is_never_a_committed_state(self):
        def fail(path, pixels):
            path.write_bytes(b"II partial TIFF fixture")
            raise OSError("simulated interrupted TIFF write")
        with patch("retouching_tool.storage.artifacts.tiff_pixels.write", side_effect=fail):
            with self.assertRaises(RetouchingError):
                self.commit()
        self.assertEqual(list((self.store.directory / "states").iterdir()), [])
        self.assertEqual(list((self.store.directory / "blobs").iterdir()), [])

    def test_fsync_failure_does_not_publish_tiff(self):
        with patch("os.fsync", side_effect=OSError("simulated artifact flush failure")):
            with self.assertRaises(RetouchingError):
                self.commit()
        self.assertEqual(list((self.store.directory / "states").iterdir()), [])
        self.assertEqual(list((self.store.directory / "blobs").iterdir()), [])

    def test_corrupt_and_missing_blobs_fail_with_causes_and_collection_stops(self):
        ref = self.commit()
        path = next((self.store.directory / "blobs").glob("*.tif"))
        original = path.read_bytes()
        path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with self.assertRaises(RetouchingError):
            self.store.read(ref, self.identity, runtime=self.runtime)
        self.assertIn("checksum mismatch", self.log.records[-1]["exception"])
        orphan = self.store.directory / "blobs" / (hashlib.sha256(b"orphan").hexdigest() + ".bin")
        orphan.write_bytes(b"orphan")
        with self.assertRaises(RetouchingError):
            self.store.collect()
        self.assertTrue(orphan.exists())
        path.write_bytes(original)
        path.unlink()
        with self.assertRaises(RetouchingError):
            self.store.read(ref, self.identity)
        self.assertIn("FileNotFoundError", self.log.records[-1]["exception"])

    def test_manifest_corruption_version_unknown_payload_and_allocation_limit(self):
        mutations = [lambda d: d.update(schema_version=2), lambda d: d.update(width=10 ** 20),
                     lambda d: d.update(source_id="another-source"), lambda d: d.update(extra="unknown"),
                     lambda d: d["context"][0]["value"].update(kind="pickle"),
                     lambda d: d["context"][0].update(pixel_revision=5),
                     lambda d: d["context"][0].update(coordinates="crop-local"),
                     lambda d: d["context"][0]["value"]["data"].update(histogram=[0] * 256)]
        state = self.run_stage(self.pipeline.stages[0], self.state)
        ref = self.commit(state)
        original = self.manifest(ref).read_bytes()
        for mutation in mutations:
            self.manifest(ref).write_bytes(original)
            changed = self.rewrite(ref, mutation)
            with self.subTest(mutation=mutation), self.assertRaises(RetouchingError):
                self.store.read(changed, self.identity)
        self.manifest(ref).write_bytes(original + b" ")
        # Canonical manifest hash deliberately tolerates insignificant whitespace.
        self.store.read(ref, self.identity)
        self.manifest(ref).write_text("{}", encoding="utf-8")
        with self.assertRaises(RetouchingError):
            self.store.read(ref, self.identity)

    def test_wrong_source_writable_pixels_and_budget_rejected_before_writing(self):
        for state in (replace(self.state, metadata=SourceMetadata("wrong-source")),
                      replace(self.state, pixels=self.state.pixels.copy()),
                      replace(self.state, pixel_revision=True)):
            with self.assertRaises(RetouchingError):
                self.commit(state)
        limited = ArtifactStore.open(self.store.directory, "job-1", self.log, max_pixel_bytes=12)
        with self.assertRaises(RetouchingError):
            limited.commit(self.state, self.identity, runtime=self.runtime)
        with self.assertRaises(RetouchingError):
            self.store.commit(self.state, self.identity, runtime=replace(self.runtime, job_id="other-job"))
        self.assertEqual(list((self.store.directory / "blobs").iterdir()), [])

    def test_store_ownership_cross_job_and_alias_rejection(self):
        ref = self.commit()
        other = ArtifactStore.create(self.root / "scratch", "job-2", self.log)
        with self.assertRaises(RetouchingError):
            other.read(ref, self.identity)
        with self.assertRaises(RetouchingError):
            ArtifactStore.open(self.store.directory, "wrong-job", self.log)
        # Branch verification only; actual junction creation is opt-in below.
        with patch("retouching_tool.storage.artifacts._linked", return_value=True):
            with self.assertRaises(RetouchingError):
                ArtifactStore.open(self.store.directory, "job-1", self.log)
            with self.assertRaises(RetouchingError):
                self.store.collect()
        marker = self.store.directory / "owner.json"
        marker.write_text("{}", encoding="utf-8")
        with self.assertRaises(RetouchingError):
            self.store.collect()
        self.assertTrue(self.manifest(ref).exists())

    @unittest.skipUnless(os.environ.get("AUTO_RETOUCH_TEST_JUNCTION") == "1", "Actual junction creation requires explicitly authorized opt-in")
    def test_actual_windows_junction_is_rejected(self):
        # A real Windows junction avoids requiring symbolic-link privileges.
        link = self.root / "junction"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.store.directory)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(link.rmdir)  # Remove this junction only; never recurse through its target.
        with self.assertRaises(RetouchingError):
            ArtifactStore.open(link, "job-1", self.log)

    def test_writer_precision_regression_is_rejected_before_publish(self):
        import tifffile
        from retouching_tool.storage.tiff_pixels import DESCRIPTION
        def quantized(path, pixels):
            tifffile.imwrite(path, (pixels * 255).astype(np.uint8), photometric="rgb", metadata=None, description=DESCRIPTION)
        with patch("retouching_tool.storage.artifacts.tiff_pixels.write", side_effect=quantized):
            with self.assertRaises(RetouchingError):
                self.commit()
        self.assertEqual(list((self.store.directory / "states").iterdir()), [])
        self.assertEqual(list((self.store.directory / "blobs").iterdir()), [])

    def test_persisted_stage_pass_matches_in_memory_reference_execution_bit_exactly(self):
        in_memory = self.state
        persisted = self.state
        last_ref = None
        for index, stage in enumerate(self.pipeline.stages):
            in_memory = self.run_stage(stage, in_memory)
            persisted = self.run_stage(stage, persisted)
            identity = replace(self.identity, pipeline_prefix_sha256=fingerprint(
                {"stages": self.pipeline.to_json()["stages"][:index + 1]}))
            ref = self.store.commit(persisted, identity, runtime=replace(self.runtime, stage=stage))
            persisted = ArtifactStore.open(self.store.directory, "job-1", self.log).read(ref, identity)
            if last_ref:
                self.store.discard(last_ref)
                self.store.collect()
            last_ref = ref
            self.assertEqual(persisted.pixels.tobytes(), in_memory.pixels.tobytes())
            self.assertEqual(persisted.context, in_memory.context)
            self.assertEqual(persisted.metadata, in_memory.metadata)


class ArtifactImportTests(unittest.TestCase):
    def test_shared_artifact_modules_import_without_qt_numpy_or_tiff(self):
        script = """import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'PySide6', 'numpy', 'tifffile', 'onnxruntime'}:
            raise AssertionError('Eager processing dependency: ' + fullname)
sys.meta_path.insert(0, Block())
from retouching_tool.storage.artifacts import ArtifactIdentity, ArtifactStore
print('headless lazy imports passed')
"""
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
