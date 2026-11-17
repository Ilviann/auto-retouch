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

from PIL import Image, JpegImagePlugin

from retouching_tool.application.job_service import JobControl
from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.imaging.jpeg import JpegSettings
from retouching_tool.jobs.journal import MAX_ATTEMPTS, JobJournal, JournalError, open_journal, runner_lock
from retouching_tool.operations.reference import Exposure

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

def fail_pending_rename(path, target):
    assert path.suffix == '.pending' and Path(target).suffix == '.jpg'
    die()

fault = patch.object(Path, 'rename', fail_pending_rename) if boundary == 'before' else patch.object(JobJournal, 'complete', die)
with fault:
    app.job_service.run(job_id)
"""
        worker = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", script, str(self.root), job_id, boundary],
                                capture_output=True, encoding="utf-8", timeout=20, check=False)
        self.assertEqual(worker.returncode, 79, worker.stderr)
        before = self.jobs.status(job_id)
        self.assertEqual((before.status, before.images[0].status, before.images[0].attempts), ("running", "prepared", 1))

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
        self.assertEqual(self.jobs.run(job_id), result)

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
        control.cancel.set()
        canceled = self.open_app().job_service.run(job_id, control=control)
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
                with (patch(APP_JOBS + ".decode_jpeg", side_effect=AssertionError("unexpected re-render")) as decode,
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
        with patch(APP_JOBS + ".decode_jpeg", side_effect=KeyboardInterrupt):
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
        with open_journal(self.root, job_id) as journal, journal.connection:
            journal.connection.execute("UPDATE job SET snapshot = '{}' WHERE id = 1")
        event = self.failure("JOB_JOURNAL_FAILED", lambda: self.jobs.run(job_id))
        self.assertIn("checksum", event["message"])
        self.assertFalse(list(self.destination.iterdir()))
