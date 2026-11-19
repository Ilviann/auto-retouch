"""Sequential jobs with generated JPEGs and real local SQLite recovery."""

import errno
import hashlib
from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageCms, JpegImagePlugin

from retouching_tool.application.batch import BASE_SCRATCH_BYTES, ExistingOutputDecision, ExistingOutputs
from retouching_tool.application.job_service import JobControl
from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.imaging.export_settings import JpegSettings, PngSettings, TiffSettings, parse_settings
from retouching_tool.imaging.jpeg import JpegRuntime
from retouching_tool.imaging.source import decode_source
from retouching_tool.imaging.xmp import EMPTY_XMP
from retouching_tool.jobs.journal import MAX_ATTEMPTS, JobJournal, JournalError, open_journal, runner_lock, saved_job_files
from retouching_tool.operations.reference import Exposure, ReferenceOperation
from retouching_tool.pipeline.definition import OperationDescriptor
from retouching_tool.storage.artifacts import ArtifactStore, MANIFEST_LIMIT
from retouching_tool.storage.tiff_pixels import TIFF_OVERHEAD_LIMIT

ROOT = Path(__file__).resolve().parents[1]
APP_JOBS = "retouching_tool.application.job_service"


class JobTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".test-work"
        work.mkdir(exist_ok=True)
        folder = tempfile.TemporaryDirectory(dir=work, prefix="jobs пример ")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve(strict=True)
        self.source = self.root / "вход"
        self.destination = self.root / "выход"
        self.source.mkdir()
        self.destination.mkdir()
        self.log = Diagnostics(self.root / "logs", console=StringIO())
        self.addCleanup(self.log.close)
        self.app = self.open_app()
        self.jobs = self.app.job_service

    def open_app(self):
        app = ApplicationService(self.root, load_config(self.root), self.log)
        app.startup(LaunchOptions((), None, None), "directml")
        return app

    def jpeg(self, relative):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with Image.new("RGB", (36, 20), (96, 72, 48)) as image:
            exif = Image.Exif()
            exif[271] = "Generated Test Camera"
            exif[274] = 6
            image.save(path, exif=exif)
        return path

    def create(self, **options):
        index = self.app.batch_service.index((self.source,), recursive=True)
        return self.jobs.create(index, self.destination, **options)

    def interrupted_worker(self, job_id, boundary):
        script = """
import os
from pathlib import Path
import sys
from unittest.mock import patch
from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.jobs.journal import JobJournal

root, job_id, boundary = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
app = ApplicationService(root, load_config(root), Diagnostics(root / 'child-logs'))
app.startup(LaunchOptions((), None, None), 'directml')

def die(*args, **kwargs):
    os._exit(79)

rename = Path.rename
def fail_pending_rename(path, target):
    if path.suffix == '.pending':
        die()
    return rename(path, target)

commit = JobJournal.commit_stage
def stop_at_checkpoint(journal, image_id, stage, ref, warnings):
    if stage == 1 and boundary == 'before_checkpoint':
        die()
    commit(journal, image_id, stage, ref, warnings)
    if stage == 1 and boundary == 'after_checkpoint':
        die()

faults = {
    'before': patch.object(Path, 'rename', fail_pending_rename),
    'before_replace': patch.object(Path, 'replace', die),
    'after': patch.object(JobJournal, 'complete', die),
    'before_checkpoint': patch.object(JobJournal, 'commit_stage', stop_at_checkpoint),
    'after_checkpoint': patch.object(JobJournal, 'commit_stage', stop_at_checkpoint),
}
fault = faults[boundary]
with fault:
    app.job_service.run(job_id)
"""
        worker = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", script, str(self.root), job_id, boundary],
                                capture_output=True, encoding="utf-8", timeout=20, check=False)
        self.assertEqual(worker.returncode, 79, worker.stderr)
        before = self.jobs.status(job_id)
        if boundary in ("before", "before_replace", "after"):
            attempts = len(before.stage_ids) + 1 if before.mode == "staged" else 1
            self.assertEqual((before.status, before.images[0].status, before.images[0].attempts),
                             ("running", "prepared", attempts))
        return before

    def load_staged_reference(self):
        editor = self.app.pipeline_service
        editor.load(ROOT / "examples/pipelines/reference.json")
        editor.set_enabled("exposure-reference", True)
        editor.set_parameters("exposure-reference", {"stops": 2})

    def pause_after_checkpoint(self, job_id, *, count=1):
        control = JobControl()
        commit = JobJournal.commit_stage
        calls = []

        def pause(journal, image_id, stage, ref, warnings):
            commit(journal, image_id, stage, ref, warnings)
            calls.append((image_id, stage))
            if len(calls) == count:
                control.pause.set()

        with patch.object(JobJournal, "commit_stage", pause):
            result = self.jobs.run(job_id, control=control)
        self.assertEqual(result.status, "paused")
        return result

    def checkpoint_directories(self, job_id):
        with open_journal(self.jobs.folder_for(job_id), job_id) as journal:
            scratch = Path(journal.snapshot()["scratch"])
            return {row["image_id"]: scratch / f"auto-retouch-job-{row['store_id']}"
                    for row in journal.images() if row["store_id"] is not None}

    def test_new_format_recovery_uses_frozen_suffix(self):
        source = self.jpeg("nested/a.jpeg")
        for mode, settings, suffix in (("sequential", PngSettings(1), ".png"), ("staged", TiffSettings(), ".tif")):
            with self.subTest(mode=mode):
                job_id = self.create(mode=mode, settings=settings)
                before = self.interrupted_worker(job_id, "before")
                output = self.destination / source.relative_to(self.source).with_suffix(suffix)
                self.assertEqual(before.images[0].output, output)
                with patch(APP_JOBS + ".export_image", side_effect=AssertionError("Unexpected encode")):
                    recovered = self.open_app().job_service.run(job_id)
                self.assertEqual(recovered.settings, settings)
                self.assertEqual(recovered.status, "completed")
                with Image.open(output) as image:
                    self.assertEqual(image.getexif()[271], "Generated Test Camera")

    def test_converted_name_collisions_and_legacy_settings(self):
        self.jpeg("same.jpg")
        self.jpeg("same.jpeg")
        with self.assertRaises(RetouchingError) as caught:
            self.create(settings=PngSettings())
        self.assertEqual(caught.exception.code, "OUTPUT_COLLISION")
        self.assertEqual(parse_settings({"quality": 80, "progressive": True, "subsampling": "4:2:0"}),
                         JpegSettings(80, "4:2:0", True))

    def test_still_avif_sources_preview_and_export_in_both_schedules(self):
        source = self.source / "portrait.AVIF"
        exif = Image.Exif()
        exif[271] = "AVIF Camera"
        exif[274] = 6
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        with Image.new("RGBA", (36, 20), (112, 64, 48, 0)) as photo:
            photo.paste((112, 64, 48, 255), (0, 0, 18, 20))
            photo.save(source, exif=exif.tobytes(), icc_profile=profile, xmp=EMPTY_XMP)
        original = source.read_bytes()
        self.app.startup(LaunchOptions((source,), None, None), "directml")
        index = self.app.index_sources((source,))
        item, = index.images
        state = decode_source(source, source_id=item.image_id, runtime=JpegRuntime(self.log, item.image_id))
        # Pillow's encoder moved the orientation into an AVIF container transform.
        del exif[274]
        self.assertEqual(state.metadata.exif, exif.tobytes())
        self.assertEqual(state.metadata.icc_profile, profile)
        self.assertEqual(state.metadata.xmp, EMPTY_XMP)
        preview = self.app.preview_service
        thumb = preview.thumbnail(item)
        picture = preview.picture(index, item.image_id, "avif-preview", process=True)
        self.assertEqual((thumb.width, thumb.height), (20, 36))
        self.assertEqual(picture.original, picture.result)
        self.assertEqual(picture.original.rgb[-3:], bytes([255, 255, 255]))
        self.assertLess(picture.original.rgb[0], 200)
        self.load_staged_reference()
        for mode in ("sequential", "staged"):
            with self.subTest(mode=mode):
                destination = self.destination / mode
                destination.mkdir()
                job_id = self.jobs.create(index, destination, mode=mode)
                status = self.open_app().job_service.run(job_id)
                self.assertEqual(status.status, "completed")
                with Image.open(destination / "portrait.jpg") as exported:
                    self.assertEqual((exported.format, exported.size), ("JPEG", (20, 36)))
                    self.assertEqual(exported.getexif()[271], "AVIF Camera")
                    self.assertEqual(exported.getexif()[274], 1)
        self.assertEqual(source.read_bytes(), original)
        self.jpeg("portrait.jpg")
        with self.assertRaises(RetouchingError) as caught:
            self.create()
        self.assertEqual(caught.exception.code, "OUTPUT_COLLISION")

    def test_corrupt_and_animated_avif_are_accounted_for_during_scan(self):
        (self.source / "broken.avif").write_bytes(b"not an AVIF image")
        with Image.new("RGB", (16, 16), "red") as first, Image.new("RGB", (16, 16), "blue") as second:
            first.save(self.source / "animation.avif", save_all=True, append_images=[second])
        index = self.app.index_sources((self.source,))
        self.assertEqual(len(index.images), 2)
        self.assertTrue(all(item.header is None and item.issue for item in index.images))
        failures = {record["code"] for record in self.log.records if record.get("image_id")}
        self.assertTrue({"INPUT_AVIF_SEQUENCE", "INPUT_HEADER_INVALID"} <= failures)

    def review_existing(self, action, **options):
        with self.assertRaises(ExistingOutputs) as caught:
            self.create(**options)
        return ExistingOutputDecision(action, caught.exception.files)

    def test_all_skipped_outputs_remain_accounted_for_without_reading_photos(self):
        self.jpeg("a.jpg")
        self.jpeg("b.jpg")
        for name in ("a.jpg", "b.jpg"):
            (self.destination / name).write_bytes(b"keep")
        for mode in ("sequential", "staged"):
            with self.subTest(mode=mode):
                decision = self.review_existing("skip", mode=mode)
                self.assertEqual(len(decision.files), 2)
                with patch(APP_JOBS + ".stamp_and_hash", side_effect=AssertionError("read skipped photo")):
                    job_id = self.create(mode=mode, existing_decision=decision)
                status = self.open_app().job_service.run(job_id)
                self.assertEqual(status.status, "completed")
                self.assertEqual([(i.status, i.attempts) for i in status.images], [("skipped", 0)] * 2)
                self.assertTrue(all(i.output.read_bytes() == b"keep" for i in status.images))

    def test_overwrite_requires_review_of_new_collisions_and_preserves_late_changes(self):
        self.jpeg("a.jpeg")
        self.jpeg("b.jpeg")
        a, b = (self.destination / name for name in ("a.png", "b.png"))
        a.write_bytes(b"old")
        settings = PngSettings()
        decision = self.review_existing("overwrite", settings=settings)
        b.write_bytes(b"new collision")
        with self.assertRaises(ExistingOutputs) as caught:
            self.create(settings=settings, existing_decision=decision)
        self.assertEqual({path for path, stamp in caught.exception.files}, {a, b})
        decision = ExistingOutputDecision("overwrite", caught.exception.files)
        job_id = self.create(settings=settings, existing_decision=decision)
        a.write_bytes(b"edited after approval")
        result = self.open_app().job_service.run(job_id)
        self.assertEqual([(i.status, i.code) for i in result.images], [("failed", "OUTPUT_COLLISION"), ("completed", None)])
        self.assertEqual(a.read_bytes(), b"edited after approval")
        with Image.open(b) as image:
            self.assertEqual(image.format, "PNG")

    def test_verified_overwrite_recovers_before_and_after_replacement(self):
        source = self.jpeg("portrait.jpg")
        original = source.read_bytes()
        output = self.destination / source.name
        for mode, boundary in (("sequential", "before_replace"), ("staged", "after")):
            with self.subTest(mode=mode):
                output.write_bytes(b"previous export")
                decision = self.review_existing("overwrite", mode=mode)
                job_id = self.create(mode=mode, existing_decision=decision)
                self.interrupted_worker(job_id, boundary)
                if boundary == "before_replace":
                    self.assertEqual(output.read_bytes(), b"previous export")
                with patch(APP_JOBS + ".export_image", side_effect=AssertionError("unexpected encode")):
                    result = self.open_app().job_service.run(job_id)
                self.assertEqual(result.status, "completed")
                self.assertEqual(source.read_bytes(), original)
                with Image.open(output) as image:
                    self.assertEqual(image.getexif()[271], "Generated Test Camera")

    def test_failed_overwrite_verification_preserves_previous_export(self):
        self.jpeg("portrait.jpg")
        output = self.destination / "portrait.jpg"
        output.write_bytes(b"previous export")
        job_id = self.create(existing_decision=self.review_existing("overwrite"))
        with patch("retouching_tool.imaging.export._verify_export", side_effect=ValueError("invalid metadata")):
            result = self.jobs.run(job_id)
        self.assertEqual(result.images[0].code, "OUTPUT_VALIDATION_FAILED")
        self.assertEqual(output.read_bytes(), b"previous export")
        self.assertEqual(list(self.destination.glob("*.pending")), [])

    def test_staged_passes_match_sequential_and_restore_context(self):
        self.jpeg("a.jpg")
        self.jpeg("sub/b.jpg")
        self.load_staged_reference()
        self.app.pipeline_service.set_parameter("exposure-reference", "blend", .4)
        index = self.app.batch_service.index((self.source,), recursive=True)
        alternate = self.root / "staged-output"
        alternate.mkdir()
        settings = JpegSettings(quality=83, subsampling="4:2:0", progressive=True)
        staged_id = self.jobs.create(index, alternate, mode="staged", settings=settings)
        sequential_id = self.jobs.create(index, self.destination, settings=settings)
        process = Exposure.process

        def observe_context(operation, state):
            self.assertEqual(state.entry("image.luminance").producer_id, "statistics-before")
            return process(operation, state)

        # Both jobs use the same settings even after the editor is changed.
        self.app.pipeline_service.set_parameters("exposure-reference", {"stops": -2})
        with patch.object(Exposure, "process", observe_context):
            staged = self.open_app().job_service.run(staged_id)
            sequential = self.jobs.run(sequential_id)
        self.assertEqual((staged.mode, staged.status), ("staged", "completed"))
        self.assertEqual(staged.stage_ids, ("statistics-before", "exposure-reference", "statistics-after"))
        for a, b in zip(staged.images, sequential.images, strict=True):
            self.assertEqual((a.completed_stages, a.attempts, a.warnings), (3, 4, b.warnings))
            with Image.open(a.output) as left, Image.open(b.output) as right:
                self.assertEqual(left.tobytes(), right.tobytes())
                self.assertEqual(left.getexif(), right.getexif())
                self.assertEqual((left.size, left.getexif()[271], left.getexif()[274]),
                                 ((20, 36), "Generated Test Camera", 1))
                self.assertEqual(JpegImagePlugin.get_sampling(left), 2)
        events = [event for event in self.log.records if event.get("job_id") == staged_id
                  and event["code"] in ("STAGE_PREPARED", "STAGE_COMPLETED", "STAGE_RELEASED")]
        self.assertEqual([(event["stage_instance_id"], event["code"]) for event in events],
                         [(stage, code) for stage in staged.stage_ids
                          for code in ("STAGE_PREPARED", "STAGE_COMPLETED", "STAGE_COMPLETED", "STAGE_RELEASED")])
        self.assertEqual(self.checkpoint_directories(staged_id), {})

    def test_staged_pause_uses_the_committed_state_and_reclaims_old_pixels(self):
        path = self.jpeg("a.jpg")
        self.load_staged_reference()
        job_id = self.create(mode="staged")
        first = self.pause_after_checkpoint(job_id)
        item = first.images[0]
        directory = self.checkpoint_directories(job_id)[item.image_id]
        original_tiff, = (directory / "blobs").glob("*.tif")
        self.assertEqual((item.status, item.completed_stages, item.attempts), ("running", 1, 1))
        self.assertFalse(item.output.exists())

        # The checkpoint has enough validated input state to run the later units.
        path.write_bytes(b"source changed after the committed first stage")
        with patch(APP_JOBS + ".decode_source", side_effect=AssertionError("Unexpected source decode")):
            second = self.pause_after_checkpoint(job_id)
            self.assertEqual(second.images[0].completed_stages, 2)
            self.assertEqual(len(list((directory / "states").glob("*.json"))), 1)
            new_tiff, = (directory / "blobs").glob("*.tif")
            self.assertNotEqual(original_tiff, new_tiff)
            self.assertFalse(original_tiff.exists())
            result = self.open_app().job_service.run(job_id)
        self.assertEqual(result.status, "completed")
        self.assertFalse(directory.exists())

    def test_staged_real_process_exit_on_either_side_of_the_checkpoint_commit(self):
        self.jpeg("a.jpg")
        self.load_staged_reference()
        for boundary, stage_count, final_attempts in (("before_checkpoint", 1, 5), ("after_checkpoint", 2, 4)):
            with self.subTest(boundary=boundary):
                destination = self.root / boundary
                destination.mkdir()
                index = self.app.batch_service.index((self.source,))
                job_id = self.jobs.create(index, destination, mode="staged")
                before = self.interrupted_worker(job_id, boundary)
                self.assertEqual((before.images[0].completed_stages, before.images[0].attempts), (stage_count, 2))
                directory, = self.checkpoint_directories(job_id).values()
                self.assertEqual(len(list((directory / "states").glob("*.json"))), 2)
                result = self.open_app().job_service.run(job_id)
                self.assertEqual((result.status, result.images[0].attempts), ("completed", final_attempts))
                self.assertFalse(directory.exists())

    def test_staged_damaged_intermediate_rebuilds_only_affected_input(self):
        self.jpeg("a.jpg")
        self.jpeg("b.jpg")
        self.jpeg("c.jpg")
        self.load_staged_reference()
        job_id = self.create(mode="staged")
        first = self.pause_after_checkpoint(job_id, count=3)
        damaged_pixels, damaged_manifest, healthy = first.images
        directories = self.checkpoint_directories(job_id)
        tiff, = (directories[damaged_pixels.image_id] / "blobs").glob("*.tif")
        with tiff.open("r+b") as stream:
            stream.write(b"Bad TIFF")
        manifest, = (directories[damaged_manifest.image_id] / "states").glob("*.json")
        manifest.write_bytes(b"{invalid")
        preserved = directories[healthy.image_id] / "blobs" / "operator-note.txt"
        preserved.write_text("Keep this file", encoding="utf-8")

        result = self.open_app().job_service.run(job_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual([image.attempts for image in result.images], [6, 5, 4])
        self.assertEqual(preserved.read_text(encoding="utf-8"), "Keep this file")
        replayed = [event for event in self.log.records if event.get("job_id") == job_id
                    and event["code"] == "JOB_ARTIFACT_REPLAY"]
        self.assertEqual({event["image_id"] for event in replayed}, {damaged_pixels.image_id, damaged_manifest.image_id})
        invalid = [event for event in self.log.records if event.get("job_id") == job_id and event["code"] == "ARTIFACT_INVALID"]
        self.assertEqual(len(invalid), 2)
        self.assertTrue(all("Traceback" in event["exception"] and event["stage_instance_id"] for event in invalid))

    def test_staged_disk_error_keeps_the_predecessor_for_resume(self):
        self.jpeg("a.jpg")
        self.load_staged_reference()
        job_id = self.create(mode="staged")
        self.pause_after_checkpoint(job_id)
        with patch.object(ArtifactStore, "commit", side_effect=OSError(errno.ENOSPC, "fixture disk full")):
            self.failure("JOB_DISK_FULL", lambda: self.jobs.run(job_id))
        interrupted = self.jobs.status(job_id).images[0]
        self.assertEqual((interrupted.status, interrupted.completed_stages), ("running", 1))
        event = next(event for event in self.log.records if event["code"] == "JOB_UNIT_INTERRUPTED")
        self.assertEqual((event["job_id"], event["image_id"], event["stage_instance_id"]),
                         (job_id, interrupted.image_id, "exposure-reference"))
        self.assertIn("fixture disk full", event["exception"])
        with patch(APP_JOBS + ".decode_source", side_effect=AssertionError("Unexpected source decode")):
            result = self.open_app().job_service.run(job_id)
        self.assertEqual((result.status, result.images[0].attempts), ("completed", 5))

    def test_staged_repeated_artifact_corruption_reaches_a_bounded_failure(self):
        self.jpeg("a.jpg")
        self.jpeg("b.jpg")
        self.load_staged_reference()
        job_id = self.create(mode="staged")
        damaged = self.jobs.status(job_id).images[0].image_id
        commit = JobJournal.commit_stage

        def corrupt(journal, image_id, stage, ref, warnings):
            commit(journal, image_id, stage, ref, warnings)
            if image_id == damaged and stage == 0:
                scratch = Path(journal.snapshot()["scratch"])
                directory = scratch / f"auto-retouch-job-{ref['store_id']}"
                tiff, = (directory / "blobs").glob("*.tif")
                tiff.write_bytes(b"Bad TIFF")

        with patch.object(JobJournal, "commit_stage", corrupt):
            result = self.jobs.run(job_id)
        self.assertEqual(result.status, "completed_with_errors")
        failed, good = result.images
        self.assertEqual((failed.code, failed.attempts), ("JOB_ARTIFACT_REPLAYS_EXHAUSTED", 6))
        self.assertEqual(failed.warnings, 5)  # Three sRGB assumptions and two replay warnings.
        self.assertEqual((good.status, good.attempts), ("completed", 4))

    def test_staged_resume_reserves_only_a_replacement_when_all_inputs_are_checkpointed(self):
        self.jpeg("a.jpg")
        self.jpeg("b.jpg")
        self.load_staged_reference()
        job_id = self.create(mode="staged")
        self.pause_after_checkpoint(job_id, count=2)
        with patch.object(self.jobs, "_check_space", wraps=self.jobs._check_space) as space:
            result = self.jobs.run(job_id)
        expected = BASE_SCRATCH_BYTES + (36 * 20 * 12) + TIFF_OVERHEAD_LIMIT + MANIFEST_LIMIT
        self.assertEqual(space.call_args_list[0].args[1], expected)
        self.assertEqual(result.status, "completed")

    def test_staged_late_collision_and_unit_exhaustion_still_allow_a_neighbor_to_complete(self):
        self.jpeg("a.jpg")
        self.jpeg("b.jpg")
        self.jpeg("c.jpg")
        self.load_staged_reference()
        job_id = self.create(mode="staged")
        occupied = self.destination / "a.jpg"
        occupied.write_bytes(b"user file")
        with patch.object(JobJournal, "commit_stage", side_effect=JournalError("fixture journal failure")):
            for _ in range(MAX_ATTEMPTS):
                self.failure("JOB_JOURNAL_FAILED", lambda: self.jobs.run(job_id))
        result = self.open_app().job_service.run(job_id)
        self.assertEqual(result.status, "completed_with_errors")
        collision, exhausted, good = result.images
        self.assertEqual((collision.code, collision.attempts), ("OUTPUT_COLLISION", 0))
        self.assertEqual((exhausted.code, exhausted.attempts), ("JOB_ATTEMPTS_EXHAUSTED", MAX_ATTEMPTS))
        self.assertEqual((good.status, good.completed_stages), ("completed", 3))
        self.assertEqual(occupied.read_bytes(), b"user file")

    def test_staged_export_receipt_recovers_without_intermediate_or_reencoding(self):
        self.jpeg("a.jpg")
        self.load_staged_reference()
        job_id = self.create(mode="staged")
        before = self.interrupted_worker(job_id, "after")
        output = before.images[0].output
        original = output.read_bytes()
        directory, = self.checkpoint_directories(job_id).values()
        tiff, = (directory / "blobs").glob("*.tif")
        tiff.unlink()
        control = JobControl()
        control.cancel.set()
        with patch(APP_JOBS + ".export_image", side_effect=AssertionError("Unexpected JPEG encode")):
            result = self.jobs.run(job_id, control=control)
        self.assertEqual((result.status, result.images[0].attempts), ("completed", 4))
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(self.checkpoint_directories(job_id), {})

    def test_staged_cancel_releases_stage_and_cleans_only_job_artifacts(self):
        self.jpeg("a.jpg")
        self.jpeg("b.jpg")
        self.load_staged_reference()
        job_id = self.create(mode="staged")
        first = self.pause_after_checkpoint(job_id)
        directory, = self.checkpoint_directories(job_id).values()
        unrelated = directory.parent / "keep.bin"
        unrelated.write_bytes(b"keep")
        result = self.jobs.cancel_remaining(job_id)
        self.assertEqual(result.status, "canceled")
        self.assertEqual([image.status for image in result.images], ["canceled", "canceled"])
        self.assertEqual([image.attempts for image in result.images], [1, 0])
        self.assertTrue(all(not image.output.exists() for image in result.images))
        self.assertFalse(directory.exists())
        self.assertEqual(unrelated.read_bytes(), b"keep")
        self.assertEqual(self.jobs.run(job_id), result)
        self.assertEqual(first.images[0].completed_stages, 1)

    def test_startup_reclaims_detached_empty_stores_and_abandoned_checkpoints(self):
        self.jpeg("a.jpg")
        self.load_staged_reference()
        job_id = self.create(mode="staged")
        paused = self.pause_after_checkpoint(job_id)
        image_id = paused.images[0].image_id
        active = self.checkpoint_directories(job_id)[image_id]

        def detached(owner=job_id):
            return ArtifactStore.create(active.parent, owner, self.log, image_id=image_id).directory

        old = detached()
        partial = detached()
        (partial / "states").rmdir()
        foreign = detached("0" * 32)
        with_files = detached()
        note = with_files / "states" / "notes.txt"
        note.write_bytes(b"keep")
        invalid = detached()
        (invalid / "owner.json").write_text("{}")
        with runner_lock(self.root):
            self.jobs.discard_abandoned_jobs()
            self.assertTrue(old.exists())
        self.open_app().job_service.discard_abandoned_jobs()
        self.assertFalse(old.exists())
        self.assertFalse(partial.exists())
        self.assertFalse(active.exists())
        self.assertTrue(foreign.is_dir())
        self.assertTrue(invalid.is_dir())
        self.assertTrue(any(event["code"] == "ARTIFACT_OWNER_UNVERIFIED" and "Traceback" in event["exception"]
                            for event in self.log.records))
        self.assertEqual(note.read_bytes(), b"keep")
        self.assertFalse(paused.images[0].output.exists())

    def test_locked_retired_directory_is_removed_on_next_startup(self):
        self.jpeg("a.jpg")
        self.load_staged_reference()
        job_id = self.create(mode="staged")
        self.pause_after_checkpoint(job_id)
        directory, = self.checkpoint_directories(job_id).values()
        rmdir = Path.rmdir

        def locked(path):
            if path == directory:
                raise PermissionError("fixture open directory handle")
            rmdir(path)

        with patch.object(Path, "rmdir", locked):
            result = self.jobs.run(job_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(ArtifactStore.read_owner(directory)["job_id"], job_id)
        event = next(e for e in self.log.records if e["code"] == "ARTIFACT_CLEANUP_FAILED")
        self.assertIn("fixture open directory handle", event["exception"])
        self.assertEqual(event["image_id"], result.images[0].image_id)
        self.open_app().job_service.discard_abandoned_jobs()
        self.assertFalse(directory.exists())

    def test_discard_abandoned_batches_preserves_exports_and_another_open_batch(self):
        self.jpeg("a.jpg")
        self.load_staged_reference()
        paused = self.create(mode="staged")
        self.pause_after_checkpoint(paused)
        checkpoint, = self.checkpoint_directories(paused).values()
        index = self.app.batch_service.index((self.source,))
        live = self.jobs.create_current(index, self.destination)
        self.addCleanup(self.jobs.discard_current)
        finished = self.create()
        output = self.jobs.run(finished).images[0].output
        expected = output.read_bytes()
        self.open_app().job_service.discard_abandoned_jobs()
        self.assertEqual([path.stem for path in saved_job_files(self.jobs.job_folder)], [live])
        self.assertFalse(checkpoint.exists())
        self.assertEqual(output.read_bytes(), expected)

    def test_discard_interrupted_prepared_export_removes_only_its_pending_file(self):
        self.jpeg("a.jpg")
        job_id = self.create()
        self.interrupted_worker(job_id, "before")
        unrelated = self.destination / ".auto-retouch-other.pending"
        unrelated.write_bytes(b"keep")
        self.jobs.discard_abandoned_jobs()
        self.assertEqual(list(self.destination.iterdir()), [unrelated])
        self.assertEqual(saved_job_files(self.jobs.job_folder), ())

    def test_staged_empty_pipeline_and_isolated_stage_failure(self):
        a = self.jpeg("a.jpg")
        self.jpeg("b.jpg")
        empty_destination = self.root / "empty"
        empty_destination.mkdir()
        index = self.app.batch_service.index((self.source,))
        empty = self.jobs.run(self.jobs.create(index, empty_destination, mode="staged"))
        self.assertEqual((empty.status, empty.stage_ids), ("completed", ()))
        self.assertTrue(all((item.completed_stages, item.attempts) == (0, 1) for item in empty.images))

        self.load_staged_reference()
        job_id = self.jobs.create(index, self.destination, mode="staged")
        process = Exposure.process

        def fail_first(operation, state):
            if operation.runtime.image_id == index.images[0].image_id:
                raise ValueError("fixture stage failure")
            return process(operation, state)

        with patch.object(Exposure, "process", fail_first):
            result = self.jobs.run(job_id)
        self.assertEqual(result.status, "completed_with_errors")
        failed, good = result.images
        self.assertEqual((failed.source, failed.status, failed.completed_stages), (a, "failed", 1))
        self.assertEqual((good.status, good.completed_stages), ("completed", 3))
        event = next(event for event in self.log.records if event["code"] == "STAGE_PROCESS_FAILED")
        self.assertEqual((event["image_id"], event["stage_instance_id"]), (failed.image_id, "exposure-reference"))
        self.assertIn("fixture stage failure", event["exception"])

    def failure(self, code, action):
        with self.assertRaises(RetouchingError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        event = self.log.records[-1]
        self.assertEqual(event["code"], code)
        self.assertIn("Traceback", event["exception"])
        return event

    def test_frozen_pipeline_compression_exif_and_stage_lifecycle(self):
        source = self.jpeg("session/Фото.jpg")
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        editor = self.app.pipeline_service
        reference = editor.load(ROOT / "examples/pipelines/reference.json").definition
        exposure = next(stage for stage in reference.stages if stage.operation == "core.exposure")
        editor.set_enabled(exposure.id, True)
        editor.set_enabled("statistics-after", False)
        editor.set_parameters(exposure.id, {"stops": 1})
        definition = editor.status.definition
        settings = JpegSettings(quality=83, subsampling="4:2:0", progressive=True)
        job_id = self.create(settings=settings)
        queued = self.jobs.status(job_id)
        self.assertEqual((queued.status, queued.images[0].status), ("queued", "queued"))
        self.assertEqual((queued.pipeline_name, queued.destination, queued.settings),
                         (definition.name, self.destination, settings))
        self.assertFalse(queued.terminal)
        self.assertFalse(queued.images[0].output.exists())
        editor.set_parameters(exposure.id, {"stops": -2})
        result = self.open_app().job_service.run(job_id)
        self.assertEqual((result.status, result.images[0].status, result.images[0].attempts), ("completed", "completed", 1))
        self.assertGreater(result.images[0].warnings, 0)
        self.assertEqual(result.images[0].output, self.destination / "session/Фото.jpg")
        with Image.open(result.images[0].output) as image:
            self.assertEqual(image.size, (20, 36))
            self.assertEqual(image.getexif()[271], "Generated Test Camera")
            self.assertEqual(image.getexif()[274], 1)
            self.assertTrue(image.info["progressive"])
            self.assertEqual(JpegImagePlugin.get_sampling(image), 2)
            self.assertGreater(image.getpixel((0, 0))[0], 115)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)
        events = [event for event in self.log.records if event.get("job_id") == job_id]
        self.assertEqual([event["stage_instance_id"] for event in events if event["code"] == "STAGE_PREPARED"],
                         [stage.id for stage in definition.stages if stage.enabled])
        self.assertEqual([event["stage_instance_id"] for event in events if event["code"] == "STAGE_RELEASED"],
                         [stage.id for stage in definition.stages if stage.enabled])
        self.assertEqual(len(result.skipped_stages), 1)
        self.assertTrue(result.terminal)
        self.assertEqual(self.jobs.run(job_id), result)

    def test_discard_only_removes_verified_journals(self):
        self.jpeg("a.jpg")
        job_id = self.create()
        folder = self.jobs.job_folder
        unrelated = folder / "unrelated.sqlite3"
        unrelated.write_bytes(b"keep")
        invalid = "f" * 32
        (folder / f"{invalid}.sqlite3").mkdir()
        self.jobs.discard_abandoned_jobs()
        self.assertFalse((folder / f"{job_id}.sqlite3").exists())
        self.assertEqual(unrelated.read_bytes(), b"keep")
        self.assertTrue((folder / f"{invalid}.sqlite3").is_dir())
        warning = next(event for event in self.log.records if event["code"] == "JOB_DISCARD_FAILED")
        self.assertEqual(warning["job_id"], invalid)
        self.assertIn("Traceback", warning["exception"])

    def test_previous_version_job_directory_is_retired_on_upgrade(self):
        self.jpeg("a.jpg")
        job_id = self.create()
        legacy = self.root / ".state/jobs"
        legacy.mkdir(parents=True)
        journal = self.jobs.job_folder / f"{job_id}.sqlite3"
        journal.rename(legacy / journal.name)
        (legacy / "runner.lock").touch()
        self.jobs.discard_abandoned_jobs()
        self.assertFalse((self.root / ".state").exists())
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_bad_entropy_and_valid_neighbor_are_both_accounted_for(self):
        broken = self.jpeg("a-truncated.jpg")
        raw = broken.read_bytes()
        marker = raw.index(b"\xff\xda")
        broken.write_bytes(raw[:marker + 2 + int.from_bytes(raw[marker + 2:marker + 4], "big") + 1])
        self.jpeg("b-good.jpg")
        job_id = self.create()
        result = self.jobs.run(job_id)
        self.assertEqual(result.status, "completed_with_errors")
        self.assertEqual([item.status for item in result.images], ["failed", "completed"])
        self.assertFalse(result.images[0].output.exists())
        diagnostic = next(e for e in self.log.records if e["code"] == "INPUT_DECODE_FAILED")
        self.assertEqual((diagnostic["job_id"], diagnostic["image_id"], diagnostic["attempt"]),
                         (job_id, result.images[0].image_id, 1))
        self.assertIn("Traceback", diagnostic["exception"])

    def test_content_checksum_detects_change_with_original_size_and_mtime(self):
        source = self.jpeg("original.jpg")
        job_id = self.create()
        info = source.stat()
        raw = source.read_bytes()
        source.write_bytes(raw[:-1] + bytes((raw[-1] ^ 1,)))
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(source.stat().st_mtime_ns, info.st_mtime_ns)
        item, = self.jobs.run(job_id).images
        self.assertEqual((item.status, item.code), ("failed", "SOURCE_CHANGED"))
        self.assertFalse(item.output.exists())
        self.assertTrue(any(e["code"] == "SOURCE_CHANGED" and "frozen job input" in e["message"] for e in self.log.records))

    def test_pause_and_cancel_keep_terminal_and_queued_counts_consistent(self):
        for letter in "abc":
            self.jpeg(f"{letter}.jpg")
        job_id = self.create()
        control = JobControl()
        complete = JobJournal.complete

        def done_and_pause(journal, image_id):
            complete(journal, image_id)
            control.pause.set()

        with patch.object(JobJournal, "complete", done_and_pause):
            paused = self.jobs.run(job_id, control=control)
        self.assertEqual(paused.status, "paused")
        self.assertEqual([item.status for item in paused.images], ["completed", "queued", "queued"])
        canceled = self.open_app().job_service.cancel_remaining(job_id)
        self.assertEqual(canceled.status, "canceled")
        self.assertEqual([item.status for item in canceled.images], ["completed", "canceled", "canceled"])
        self.assertEqual(self.jobs.run(job_id), canceled)

    def test_pause_then_resume_with_edited_pipeline_keeps_completed_bytes(self):
        self.jpeg("a.jpg")
        self.jpeg("b.jpg")
        job_id = self.create()
        control = JobControl()
        complete = JobJournal.complete

        def done_and_pause(journal, image_id):
            complete(journal, image_id)
            control.pause.set()

        with patch.object(JobJournal, "complete", done_and_pause):
            paused = self.jobs.run(job_id, control=control)
        first = paused.images[0].output.read_bytes()
        reopened = self.open_app()
        reopened.pipeline_service.load(ROOT / "examples/pipelines/reference.json")
        finished = reopened.job_service.run(job_id)
        self.assertEqual([item.attempts for item in finished.images], [1, 1])
        self.assertEqual(finished.status, "completed")
        self.assertEqual(finished.images[0].output.read_bytes(), first)

    def test_real_process_exit_before_and_after_rename_recovers_exact_jpeg(self):
        for boundary in ("before", "after"):
            with self.subTest(boundary=boundary):
                self.jpeg("portrait.jpg")
                job_id = self.create()
                self.interrupted_worker(job_id, boundary)
                output = self.destination / "portrait.jpg"
                verified = next(self.destination.glob("*.pending")) if boundary == "before" else output
                expected = verified.read_bytes()
                with (patch(APP_JOBS + ".decode_source", side_effect=AssertionError("unexpected re-render")) as decode,
                      patch(APP_JOBS + ".runtime_versions", side_effect=AssertionError("unused renderer"))):
                    status = self.open_app().job_service.run(job_id)
                self.assertEqual(status.status, "completed")
                self.assertEqual(status.images[0].attempts, 1)
                decode.assert_not_called()
                self.assertEqual(output.read_bytes(), expected)
                self.assertFalse(list(self.destination.glob("*.pending")))
                output.unlink()

    def test_unverified_collisions_preserve_occupied_destination_and_pending_evidence(self):
        self.jpeg("portrait.jpg")
        job_id = self.create()
        self.interrupted_worker(job_id, "before")
        output = self.destination / "portrait.jpg"
        pending, = self.destination.glob("*.pending")
        # Even byte-identical contents under a different file identity cannot be adopted.
        output.write_bytes(pending.read_bytes())
        result = self.jobs.run(job_id)
        self.assertEqual((result.status, result.images[0].code), ("completed_with_errors", "JOB_EXPORT_CONFLICT"))
        self.assertEqual(output.read_bytes(), pending.read_bytes())

    def test_late_ordinary_collision_fails_one_image_and_keeps_existing_bytes(self):
        self.jpeg("a.jpg")
        self.jpeg("b.jpg")
        job_id = self.create()
        occupied = self.destination / "a.jpg"
        occupied.write_bytes(b"prior export")
        status = self.jobs.run(job_id)
        self.assertEqual([item.status for item in status.images], ["failed", "completed"])
        self.assertEqual(status.images[0].code, "OUTPUT_COLLISION")
        self.assertEqual(occupied.read_bytes(), b"prior export")

    def test_stage_failure_releases_resources_and_keeps_image_and_stage_context(self):
        self.jpeg("a.jpg")
        self.jpeg("b.jpg")
        self.app.pipeline_service.load(ROOT / "examples/pipelines/reference.json")
        self.app.pipeline_service.set_enabled("exposure-reference", True)
        job_id = self.create()
        first_id = self.jobs.status(job_id).images[0].image_id
        process = Exposure.process

        def process_or_fail(operation, state):
            if operation.runtime.image_id == first_id:
                raise RetouchingError("TEST_STAGE_FAILED", "simulated exposure failure")
            return process(operation, state)

        with patch.object(Exposure, "process", process_or_fail):
            result = self.jobs.run(job_id)
        self.assertEqual([item.status for item in result.images], ["failed", "completed"])
        self.assertFalse(result.images[0].output.exists())
        event = next(e for e in self.log.records if e["code"] == "STAGE_PROCESS_FAILED")
        self.assertEqual((event["job_id"], event["image_id"], event["stage_instance_id"]),
                         (job_id, first_id, "exposure-reference"))
        self.assertIn("simulated exposure failure", event["exception"])
        self.assertTrue(any(e["code"] == "STAGE_RELEASED" and e["image_id"] == first_id
                            and e["stage_instance_id"] == "exposure-reference" for e in self.log.records))

    def test_failed_receipt_commit_leaves_no_export_and_resumes(self):
        self.jpeg("portrait.jpg")
        job_id = self.create()
        with patch.object(JobJournal, "prepared", side_effect=JournalError("simulated receipt commit failure")):
            self.failure("JOB_JOURNAL_FAILED", lambda: self.jobs.run(job_id))
        self.assertFalse(list(self.destination.iterdir()))
        self.assertEqual(self.jobs.status(job_id).images[0].status, "running")
        self.assertEqual(self.jobs.run(job_id).images[0].attempts, 2)

    def test_changed_runtime_does_not_alter_the_frozen_job(self):
        self.jpeg("portrait.jpg")
        job_id = self.create()
        from retouching_tool.jobs.snapshot import runtime_versions
        simulated = {**runtime_versions("directml"), "pillow": "simulated other version"}
        with patch(APP_JOBS + ".runtime_versions", return_value=simulated):
            self.failure("JOB_RUNTIME_CHANGED", lambda: self.jobs.run(job_id))
        self.assertEqual(self.jobs.status(job_id).images[0].status, "queued")
        self.assertFalse(list(self.destination.iterdir()))
        self.assertEqual(self.jobs.run(job_id).status, "completed")

    def test_mixed_backends_in_preview_and_both_job_modes(self):
        self.jpeg("portrait.jpg")
        index = self.app.batch_service.index((self.source,), recursive=True)
        editor = self.app.pipeline_service
        editor.pytorch_backend = "cpu"
        calls = []

        class RecordDevice(ReferenceOperation):
            def process(self, state):
                calls.append((self.stage.operation, self.runtime.backend))
                return state

        expected = [("test.onnxruntime", "directml"), ("test.pytorch", "cpu")]
        for operation, device in expected:
            descriptor = OperationDescriptor(operation, 1, operation, backends=(device,),
                                             inference_runtime=operation.split(".")[1])
            editor.registry.register(descriptor, RecordDevice)
            editor.add_operation(operation, 1)
        self.app.preview_service.picture(index, index.images[0].image_id, "mixed-preview", process=True)
        self.assertEqual(calls, expected)
        for mode in ("sequential", "staged"):
            with self.subTest(mode=mode):
                destination = self.root / mode
                destination.mkdir()
                job_id = self.jobs.create(index, destination, mode=mode)
                with open_journal(self.jobs.folder_for(job_id), job_id) as journal:
                    snapshot = journal.snapshot()
                self.assertEqual((snapshot["backend"], snapshot["pytorch_backend"]), ("directml", "cpu"))
                self.assertIn("pytorch", snapshot["versions"])
                editor.pytorch_backend = "cuda"
                self.failure("JOB_RUNTIME_CHANGED", lambda: self.jobs.run(job_id))
                editor.pytorch_backend = "cpu"
                calls.clear()
                self.assertEqual(self.jobs.run(job_id).status, "completed")
                self.assertEqual(calls, expected)

    def test_legacy_onnx_checkpoint_resumes_after_pair_is_configured(self):
        self.jpeg("portrait.jpg")
        self.load_staged_reference()
        job_id = self.create(mode="staged")
        with open_journal(self.jobs.folder_for(job_id), job_id) as journal:
            self.assertNotIn("pytorch_backend", journal.snapshot())
        self.pause_after_checkpoint(job_id)
        self.app.pipeline_service.pytorch_backend = "cpu"
        self.assertEqual(self.jobs.run(job_id).status, "completed")

    def test_simulated_disk_full_can_resume_without_marking_image_failed(self):
        self.jpeg("portrait.jpg")
        job_id = self.create()
        with patch.object(Image.Image, "save", side_effect=OSError(errno.ENOSPC, "simulated device full")):
            event = self.failure("JOB_DISK_FULL", lambda: self.jobs.run(job_id))
        self.assertIn("simulated device full", event["exception"])
        self.assertEqual(self.jobs.status(job_id).images[0].status, "running")
        self.assertFalse(list(self.destination.iterdir()))
        result = self.jobs.run(job_id)
        self.assertEqual((result.status, result.images[0].attempts), ("completed", 2))

    def test_fresh_space_check_stops_job_before_starting_image(self):
        self.jpeg("portrait.jpg")
        job_id = self.create()
        with patch("retouching_tool.application.batch.shutil.disk_usage") as disk:
            disk.return_value.free = 1
            self.failure("JOB_SPACE_INSUFFICIENT", lambda: self.jobs.run(job_id))
        self.assertEqual(self.jobs.status(job_id).images[0].attempts, 0)
        self.assertEqual(self.jobs.run(job_id).status, "completed")

    def test_exhausted_interruption_replay_does_not_advance_the_attempt_count(self):
        self.jpeg("portrait.jpg")
        job_id = self.create()
        with patch(APP_JOBS + ".decode_source", side_effect=KeyboardInterrupt):
            for _ in range(MAX_ATTEMPTS):
                with self.assertRaises(KeyboardInterrupt):
                    self.jobs.run(job_id)
        result = self.jobs.run(job_id)
        self.assertEqual((result.images[0].attempts, result.images[0].code), (MAX_ATTEMPTS, "JOB_ATTEMPTS_EXHAUSTED"))
        self.assertFalse(result.images[0].output.exists())

    def test_runner_lock_refuses_a_second_writer(self):
        self.jpeg("portrait.jpg")
        job_id = self.create()
        with runner_lock(self.root):
            self.failure("JOB_RUNNER_BUSY", lambda: self.jobs.run(job_id))
            self.assertEqual(self.jobs.status(job_id).images[0].attempts, 0)
        self.assertEqual(self.jobs.run(job_id).status, "completed")

    def test_corrupt_snapshot_is_rejected_with_no_export(self):
        self.jpeg("portrait.jpg")
        job_id = self.create()
        with open_journal(self.jobs.folder_for(job_id), job_id) as journal, journal.connection:
            journal.connection.execute("UPDATE job SET snapshot = '{}' WHERE id = 1")
        event = self.failure("JOB_JOURNAL_FAILED", lambda: self.jobs.run(job_id))
        self.assertIn("checksum", event["message"])
        self.assertFalse(list(self.destination.iterdir()))
