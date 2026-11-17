"""Focused headless checks for source inventory and initial job preflight."""

from contextlib import nullcontext
from dataclasses import FrozenInstanceError
import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, JpegImagePlugin

from retouching_tool.application.batch import BASE_SCRATCH_BYTES, BatchService
from retouching_tool.application.service import ApplicationService
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.storage.artifacts import MANIFEST_LIMIT
from retouching_tool.storage.tiff_pixels import TIFF_OVERHEAD_LIMIT

ROOT = Path(__file__).resolve().parents[1]


class BatchTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-work").mkdir(exist_ok=True)
        folder = tempfile.TemporaryDirectory(dir=ROOT / ".test-work", prefix="batch пример ")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve(strict=True)
        self.source = self.root / "фото"
        self.output = self.root / "фото-ретушь"
        self.source.mkdir()
        self.output.mkdir()
        self.log = Diagnostics(self.root / "logs", console=io.StringIO())
        self.addCleanup(self.log.close)
        config = load_config(self.root)
        self.scratch = config.temporary
        self.batch = ApplicationService(self.root, config, self.log).batch_service

    def jpeg(self, relative, size=(18, 12), *, folder=None, mode="RGB"):
        path = (folder or self.source) / relative
        path.parent.mkdir(exist_ok=True, parents=True)
        with Image.new(mode, size) as image:
            image.save(path, format="JPEG")
        return path

    def failure(self, code, operation):
        with self.assertRaises(RetouchingError) as caught:
            operation()
        event = self.log.records[-1]
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(event["code"], code)
        self.assertIn("Traceback", event["exception"])
        return event

    def test_discovery_deduplicates_explicit_files_and_folders_without_decoding(self):
        flat = self.jpeg("z.JPG", mode="L")
        nested = self.jpeg("child/Фото.JpEg", (30, 20))
        (self.source / "ignored.png").write_bytes(b"ignored")
        with patch.object(JpegImagePlugin.JpegImageFile, "load", side_effect=AssertionError("decoded pixels")):
            flat_index = self.batch.index((flat, self.source, flat))
            tree_index = self.batch.index((nested, self.source, self.source / "child"), recursive=True)
        self.assertEqual([image.path for image in flat_index.images], [flat])
        self.assertEqual([image.path for image in tree_index.images], [flat, nested])
        self.assertEqual(tree_index.images[1].relative_path, Path("child/Фото.JpEg"))
        self.assertEqual([image.working_bytes for image in tree_index.images], [18 * 12 * 12, 30 * 20 * 12])
        self.assertFalse(self.scratch.exists())
        self.assertFalse(list(self.output.iterdir()))
        with self.assertRaises(FrozenInstanceError):
            tree_index.recursive = False

    def test_unreadable_entry_is_reported_and_can_be_deselected(self):
        self.jpeg("a.jpg")
        invalid = self.source / "b.jpg"
        invalid.write_bytes(b"\xff\xd8\xff\xe0\x00\x01")
        index = self.batch.index((self.source,))
        good, broken = index.images
        self.assertEqual(broken.path, invalid)
        self.assertIsNotNone(broken.issue)
        self.assertIsNone(broken.working_bytes)
        event = self.log.records[-2]
        self.assertEqual((event["code"], event["image_id"], event["index_id"]),
                         ("INPUT_HEADER_INVALID", broken.image_id, index.index_id))
        self.assertIn("Invalid JPEG segment length", event["exception"])
        event = self.failure("BATCH_INDEX_INCOMPLETE", lambda: self.batch.preflight(
            index, self.output, mode="sequential", job_id="job-test"))
        self.assertEqual((event["job_id"], event["image_id"]), ("job-test", broken.image_id))
        plan = self.batch.preflight(index, self.output, mode="sequential", selected_ids=(good.image_id,))
        self.assertEqual(tuple(item.source for item in plan.images), (good,))
        self.assertEqual(plan.budget.output_bytes, good.stamp.size * 2)

    def test_oversized_header_is_a_per_image_failure(self):
        source = self.jpeg("oversized.jpg")
        jpeg = bytearray(source.read_bytes())
        dimensions = jpeg.index(b"\xff\xc0") + 5
        jpeg[dimensions:dimensions + 4] = b"\xff\xd0\xff\xd0"
        source.write_bytes(jpeg)
        index = self.batch.index((source,))
        self.assertIsNone(index.images[0].header)
        self.assertEqual(self.log.records[-2]["code"], "INPUT_IMAGE_TOO_LARGE")
        self.assertIn("DecompressionBombError", self.log.records[-2]["exception"])

    def test_failed_scan_does_not_return_a_partial_index(self):
        self.jpeg("valid.jpg")
        with patch("retouching_tool.application.batch.os.scandir", side_effect=PermissionError("simulated denied scan")):
            event = self.failure("SOURCE_SCAN_FAILED", lambda: self.batch.index((self.source,)))
        self.assertIn("simulated denied scan", event["exception"])
        self.assertIn("index_id", event)

    def test_recursive_scan_skips_reparse_entries(self):
        self.jpeg("good.jpg")
        self.jpeg("junction/hidden.jpg")
        real_scandir = os.scandir

        def scan(path):
            with real_scandir(path) as entries:
                return nullcontext([entry if entry.name != "junction" else SimpleNamespace(
                    name=entry.name, path=entry.path, stat=lambda **_: SimpleNamespace(
                        st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT, st_mode=stat.S_IFDIR))
                    for entry in entries])

        with patch("retouching_tool.application.batch.os.scandir", side_effect=scan):
            index = self.batch.index((self.source,), recursive=True)
        self.assertEqual([image.path.name for image in index.images], ["good.jpg"])
        self.assertEqual(self.log.records[-2]["code"], "SOURCE_LINK_SKIPPED")

    def test_invalid_empty_or_stale_selection_fails(self):
        empty = self.batch.index((self.source,))
        self.failure("BATCH_SELECTION_INVALID", lambda: self.batch.preflight(empty, self.output, mode="sequential"))
        self.jpeg("valid.jpg")
        index = self.batch.index((self.source,))
        for selection in ((), ("from-another-index",)):
            with self.subTest(selection=selection):
                self.failure("BATCH_SELECTION_INVALID", lambda: self.batch.preflight(
                    index, self.output, mode="sequential", selected_ids=selection))
        self.failure("DESTINATION_INVALID", lambda: self.batch.preflight(index, None, mode="sequential"))
        self.failure("DESTINATION_INVALID", lambda: self.batch.preflight(
            index, self.root / "missing", mode="sequential"))

    def test_source_and_destination_relationships_use_directory_boundaries(self):
        image = self.jpeg("valid.jpg")
        inside = self.source / "exports"
        inside.mkdir()
        flat = self.batch.index((image, self.source))
        recursive = self.batch.index((self.source,), recursive=True)
        for index in (flat, recursive):
            self.failure("DESTINATION_IS_SOURCE", lambda: self.batch.preflight(
                index, self.source, mode="sequential"))
        self.assertTrue(self.batch.preflight(flat, inside, mode="sequential").ready)
        self.failure("DESTINATION_INSIDE_SOURCE", lambda: self.batch.preflight(
            recursive, inside, mode="sequential"))
        self.assertTrue(self.batch.preflight(recursive, self.output, mode="sequential").ready)

    def test_filesystem_identity_catches_same_directory_under_another_spelling(self):
        self.jpeg("valid.jpg")
        index = self.batch.index((self.source,))
        with patch("retouching_tool.application.batch.directory_chain", side_effect=[
            (index.roots[0].identity,), ((123, 456),),
        ]):
            self.failure("DESTINATION_IS_SOURCE", lambda: self.batch.preflight(index, self.output, mode="sequential"))

    def test_scratch_relationship_and_invalid_folder(self):
        path = self.jpeg("valid.jpg")
        index = self.batch.index((self.source,), recursive=True)
        for scratch, code in ((self.source / "not-yet-created", "SCRATCH_INSIDE_SOURCE"),
                              (self.output, "SCRATCH_IS_DESTINATION"), (path, "SCRATCH_INVALID")):
            with self.subTest(scratch=scratch):
                batch = BatchService(scratch, self.log)
                self.failure(code, lambda: batch.preflight(index, self.output, mode="staged"))

    def test_changed_and_removed_sources_fail_with_image_context(self):
        path = self.jpeg("valid.jpg")
        index = self.batch.index((path,))
        with path.open("ab") as stream:
            stream.write(b"changed")
        event = self.failure("SOURCE_CHANGED", lambda: self.batch.preflight(index, self.output, mode="sequential"))
        self.assertEqual(event["image_id"], index.images[0].image_id)
        path.unlink()
        event = self.failure("SOURCE_CHANGED", lambda: self.batch.preflight(index, self.output, mode="sequential"))
        self.assertIn("FileNotFoundError", event["exception"])

    def test_output_retains_subfolder_and_rechecks_existing_collision(self):
        self.jpeg("lighting/shot.JPG")
        index = self.batch.index((self.source,), recursive=True)
        plan = self.batch.preflight(index, self.output, mode="staged")
        target = self.output / "lighting/shot.JPG"
        self.assertEqual(plan.images[0].output, target)
        self.assertFalse(target.parent.exists())
        target.parent.mkdir()
        target.write_bytes(b"prior export")
        self.failure("OUTPUT_COLLISION", lambda: self.batch.preflight(index, self.output, mode="staged"))
        self.assertEqual(target.read_bytes(), b"prior export")

    def test_case_insensitive_names_and_file_parent_collisions_across_roots(self):
        other = self.root / "second source"
        third = self.root / "third source"
        self.jpeg("Session.JPG")
        self.jpeg("session.jpg", folder=other)
        self.jpeg("Session.JPG/child.jpg", folder=third)
        for folder in (other, third):
            with self.subTest(folder=folder):
                index = self.batch.index((self.source, folder), recursive=True)
                self.failure("OUTPUT_COLLISION", lambda: self.batch.preflight(index, self.output, mode="sequential"))

    def test_blocked_output_parent_and_simulated_reparse_parent(self):
        self.jpeg("sub/shot.jpg")
        index = self.batch.index((self.source,), recursive=True)
        parent = self.output / "sub"
        parent.write_bytes(b"occupied")
        self.failure("OUTPUT_PARENT_INVALID", lambda: self.batch.preflight(index, self.output, mode="staged"))
        parent.unlink()
        parent.mkdir()
        with patch("retouching_tool.storage.batch_paths.is_reparse", side_effect=lambda info: stat.S_ISDIR(info.st_mode)):
            self.failure("OUTPUT_PARENT_INVALID", lambda: self.batch.preflight(index, self.output, mode="staged"))

    def test_budget_uses_actual_working_layout_and_rolling_replacement(self):
        self.jpeg("first.jpg", size=(50, 10))
        self.jpeg("second.jpg", size=(80, 15))
        index = self.batch.index((self.source,))
        sequential = self.batch.preflight(index, self.output, mode="sequential")
        staged = self.batch.preflight(index, self.output, mode="staged", extra_scratch_bytes=423)
        expected_output = 2 * sum(image.path.stat().st_size for image in index.images)
        largest = 80 * 15 * 12
        self.assertEqual(sequential.budget.scratch_bytes, BASE_SCRATCH_BYTES + largest)
        budget = staged.budget
        self.assertEqual(budget.working_bytes, largest + 50 * 10 * 12)
        self.assertEqual(budget.tiff_bytes, 2 * TIFF_OVERHEAD_LIMIT)
        self.assertEqual(budget.manifest_bytes, 2 * MANIFEST_LIMIT)
        self.assertEqual(budget.metadata_bytes, sum(image.header.header_bytes for image in index.images))
        self.assertEqual(budget.replacement_bytes, largest + TIFF_OVERHEAD_LIMIT + MANIFEST_LIMIT)
        self.assertEqual(budget.extra_scratch_bytes, 423)
        self.assertEqual(sequential.budget.output_bytes, expected_output)
        self.assertEqual(budget.output_bytes, expected_output)
        self.assertEqual(len(staged.volumes), 1)
        self.assertEqual(staged.volumes[0].required_bytes, budget.scratch_bytes + expected_output)
        self.assertFalse(self.scratch.exists())
        self.assertFalse(list(self.output.iterdir()))

    def test_shared_disk_shortfall_can_block_individually_fitting_budgets(self):
        self.jpeg("first.jpg")
        index = self.batch.index((self.source,))
        free = BASE_SCRATCH_BYTES + index.images[0].working_bytes
        with patch("retouching_tool.application.batch.shutil.disk_usage", return_value=SimpleNamespace(free=free)):
            plan = self.batch.preflight(index, self.output, mode="sequential", job_id="job-2")
        self.assertFalse(plan.ready)
        self.assertGreater(plan.volumes[0].required_bytes, free)
        event = self.log.records[-1]
        self.assertEqual((event["code"], event["job_id"], event["available_bytes"]),
                         ("BATCH_SPACE_INSUFFICIENT", "job-2", free))

    def test_different_volumes_are_budgeted_separately(self):
        self.jpeg("first.jpg")
        index = self.batch.index((self.source,))
        self.scratch.mkdir()
        from retouching_tool.application.batch import file_identity

        def identity(info):
            return (999, info.st_ino) if info.st_ino == self.scratch.stat().st_ino else file_identity(info)

        with patch("retouching_tool.application.batch.file_identity", side_effect=identity):
            plan = self.batch.preflight(index, self.output, mode="sequential")
        output, scratch = plan.volumes
        self.assertEqual(output.paths, (self.output,))
        self.assertEqual(output.required_bytes, plan.budget.output_bytes)
        self.assertEqual(scratch.paths, (self.scratch,))
        self.assertEqual(scratch.required_bytes, plan.budget.scratch_bytes)

    def test_write_probe_and_disk_query_failures_retain_cause(self):
        self.jpeg("first.jpg")
        index = self.batch.index((self.source,))
        usage = shutil.disk_usage(self.root)
        for target, code in (("probe_writable", "BATCH_FOLDER_UNWRITABLE"),
                             ("shutil.disk_usage", "BATCH_PREFLIGHT_FAILED")):
            with self.subTest(target=target):
                with patch("retouching_tool.application.batch.shutil.disk_usage", return_value=usage):
                    with patch(f"retouching_tool.application.batch.{target}", side_effect=PermissionError("simulated denial")):
                        event = self.failure(code, lambda: self.batch.preflight(index, self.output, mode="staged"))
                self.assertIn("simulated denial", event["exception"])

    def test_batch_service_import_is_headless_and_lazy(self):
        script = """
import sys
class Block:
    def find_spec(self, fullname, *args):
        assert fullname.split('.')[0] not in {'PySide6', 'PIL', 'numpy', 'tifffile', 'onnxruntime'}, fullname
sys.meta_path.insert(0, Block())
from retouching_tool.application.service import ApplicationService
"""
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
