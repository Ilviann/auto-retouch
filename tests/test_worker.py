"""Real Windows subprocess transport with generated local JPEG fixtures."""

import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from PIL import Image

from retouching_tool.application.service import ApplicationService
from retouching_tool.cli import LaunchOptions
from retouching_tool.config import load_config
from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.jobs.journal import runner_lock


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_WORKER = """
import os
from pathlib import Path
import sys
from unittest.mock import patch
from retouching_tool.application.job_service import JobService
from retouching_tool.jobs.journal import JobJournal
from retouching_tool.jobs.worker_process import main

run = JobService.run
commit = JobJournal.commit_stage
marker = Path(sys.argv[1]) / 'worker-at-checkpoint'
action = sys.argv.pop()

def run_with_controls(service, job_id, *, control):
    def checkpoint(journal, *args):
        commit(journal, *args)
        marker.touch()
        if action == 'crash':
            sys.stderr.write('z' * 16000 + 'failure at committed checkpoint: пример\\n')
            sys.stderr.flush()
            os._exit(79)
        signal = control.pause if action == 'pause' else control.cancel
        if not signal.wait(15):
            raise RuntimeError('Test controller timed out')

    with patch.object(JobJournal, 'commit_stage', checkpoint):
        return run(service, job_id, control=control)

assert not any(name.startswith('PySide6') for name in sys.modules)
with patch.object(JobService, 'run', run_with_controls):
    raise SystemExit(main())
"""
OVERLOADED_WORKER = """
import os
import sys
from threading import Event
from unittest.mock import patch
from retouching_tool.application.job_service import JobService
from retouching_tool.jobs.worker_process import WorkerDiagnostics, main

ready = Event()
run = JobService.run
send = WorkerDiagnostics._send

def wait_for_flood(diagnostics, frame):
    if not ready.wait(10):
        raise OSError("Test diagnostic gate timed out")
    send(diagnostics, frame)

def flood_then_run(service, job_id, *, control):
    # Introduce an invalid record between startup and buffered job events.
    os.write(service.diagnostics.stream.fileno(), b"not a diagnostic frame\\n")
    for number in range(140):
        service.diagnostics.emit("WORKER_TEST_INFO", f"Info {number}")
    for number in range(20):
        service.diagnostics.emit("WORKER_TEST_WARNING", f"Warning {number}", level="WARNING")
    ready.set()
    return run(service, job_id, control=control)

with (patch.object(WorkerDiagnostics, "_send", wait_for_flood),
      patch.object(JobService, "run", flood_then_run)):
    raise SystemExit(main())
"""


class WorkerTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".test-work"
        work.mkdir(exist_ok=True)
        folder = tempfile.TemporaryDirectory(dir=work, prefix="worker пример ")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve(strict=True)
        self.log = Diagnostics(self.root / "logs", console=StringIO())
        self.addCleanup(self.log.close)
        self.app = ApplicationService(self.root, load_config(self.root), self.log)
        self.app.startup(LaunchOptions((), None, None), "directml", pytorch_backend="cpu")
        self.app.pipeline_service.load(ROOT / "examples/pipelines/reference.json")
        self.app.pipeline_service.set_enabled("exposure-reference", True)
        self.jobs, self.worker = self.app.job_service, self.app.job_worker
        self.source = self.root / "вход"
        self.source.mkdir()
        self.originals = {}
        for name in ("a.jpg", "фото.jpg"):
            path = self.source / name
            with Image.new("RGB", (40, 30), (96, 72, 48)) as image:
                exif = Image.Exif()
                exif[271] = "Worker Test Camera"
                image.save(path, exif=exif)
            self.originals[path] = hashlib.sha256(path.read_bytes()).hexdigest()

    def create(self, mode="staged"):
        destination = self.root / mode
        destination.mkdir()
        index = self.app.batch_service.index((self.source,), recursive=False)
        return self.jobs.create(index, destination, mode=mode)

    def scripted_worker(self, job_id, script, *arguments):
        command = self.worker._command

        def with_test_script(job_id, run_id):
            args = command(job_id, run_id)
            offset = args.index("-m")
            return [*args[:offset], "-c", script, *args[offset + 2:], *arguments]

        with patch.object(self.worker, "_command", with_test_script):
            run = self.worker.start(job_id)
        self.addCleanup(run.wait, 20)
        return run

    def wait_for_checkpoint(self, run):
        marker = self.root / "worker-at-checkpoint"
        deadline = monotonic() + 10
        while not marker.exists():
            self.assertIsNone(run.poll())
            self.assertLess(monotonic(), deadline, "Worker did not reach the checkpoint")
            sleep(0.02)
        marker.unlink()

    def persisted_records(self):
        return [json.loads(line) for line in self.log.path.read_text(encoding="utf-8").splitlines()]

    def test_both_schedules_export_from_frozen_pipeline_and_use_one_log(self):
        sequential, staged = self.create("sequential"), self.create()
        self.app.pipeline_service.set_enabled("exposure-reference", False)
        for job_id in (sequential, staged):
            run = self.worker.start(job_id)
            result = run.wait(20)
            self.assertEqual((result.exit_code, result.error_code, result.diagnostics_incomplete), (0, None, False))
            status = self.jobs.status(job_id)
            self.assertEqual(status.status, "completed")
            self.assertIn("exposure-reference", status.stage_ids)
            self.assertTrue(all(image.status == "completed" for image in status.images))
            events = [event for event in self.persisted_records() if event.get("worker_run_id") == run.run_id]
            ready = next(event for event in events if event["code"] == "WORKER_READY")
            self.assertEqual((ready["session_id"], ready["job_id"]), (self.log.session_id, job_id))
            self.assertNotEqual(ready["process_id"], os.getpid())
            self.assertEqual(Path(ready["executable"]), Path(sys.executable))
            completed = [event for event in events if event["code"] == "JOB_IMAGE_COMPLETED"]
            self.assertEqual({event["image_id"] for event in completed}, {image.image_id for image in status.images})
            self.assertTrue(any("фото.jpg" in event.get("output_path", "") for event in completed))
        self.assertEqual(list(self.log.path.parent.glob("*.jsonl")), [self.log.path])
        for path, checksum in self.originals.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), checksum)
            with (Image.open(self.root / "sequential" / path.name) as left,
                  Image.open(self.root / "staged" / path.name) as right):
                self.assertEqual(left.tobytes(), right.tobytes())
                self.assertEqual(left.getexif()[271], "Worker Test Camera")

    def test_auto_exposure_preview_and_worker_schedules(self):
        editor = self.app.pipeline_service
        editor.load_data({"schema_version": 1, "name": "Auto exposure test", "stages": []})
        stage = editor.add_operation("core.auto_exposure", 1).stages[0].stage
        index = self.app.batch_service.index((self.source,), recursive=False)
        image_id = index.images[0].image_id
        preview = self.app.preview_service
        comparison = preview.stage_comparison(index, image_id, stage.id, through_stage=False)
        unchanged = comparison.render("zero-strength", {"strength": 0})
        corrected = comparison.render("default-strength", {})
        self.assertEqual(unchanged.original, unchanged.result)
        self.assertGreater(corrected.result.rgb[0], corrected.original.rgb[0])
        self.assertEqual(preview.picture(index, image_id, "whole", process=True).result, corrected.result)

        for mode in ("sequential", "staged"):
            job_id = self.create(mode)
            result = self.worker.start(job_id).wait(20)
            self.assertEqual((result.exit_code, result.error_code, result.diagnostics_incomplete), (0, None, False))
            status = self.jobs.status(job_id)
            self.assertEqual(status.status, "completed")
            self.assertTrue(all(image.status == "completed" for image in status.images))
            events = [event for event in self.persisted_records()
                      if event["code"] == "AUTO_EXPOSURE_APPLIED" and event.get("job_id") == job_id]
            self.assertEqual({(event["image_id"], event["stage_instance_id"]) for event in events},
                             {(image.image_id, stage.id) for image in status.images})

        for path, checksum in self.originals.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), checksum)
            with (Image.open(self.root / "sequential" / path.name) as left,
                  Image.open(self.root / "staged" / path.name) as right):
                self.assertEqual(left.tobytes(), right.tobytes())
                self.assertGreater(left.getpixel((0, 0))[0], 96)
                self.assertEqual(left.getexif()[271], "Worker Test Camera")

    def test_frequency_filters_in_preview_and_both_workers(self):
        path = self.source / "edge.jpg"
        with Image.new("RGB", (40, 30), (75, 96, 125)) as edge:
            edge.paste((165, 155, 140), (20, 0, 40, 30))
            edge.save(path, quality=100)
        editor = self.app.pipeline_service
        editor.load_data({"schema_version": 1, "name": "Frequency filters", "stages": []})
        low = editor.add_operation("core.low_pass", 1).stages[0].stage
        high = editor.add_operation("core.high_pass", 1).stages[1].stage
        editor.set_parameters(low.id, {"radius": 1.5})
        editor.set_parameters(high.id, {"blend_mode": "overlay", "radius": 4.5})
        index = self.app.batch_service.index((path,))
        image_id = index.images[0].image_id
        preview = self.app.preview_service
        softened = preview.stage_comparison(index, image_id, low.id, through_stage=False).render("low", {})
        sharpened = preview.stage_comparison(index, image_id, high.id, through_stage=True).render("high", {})
        self.assertNotEqual(softened.original, softened.result)
        self.assertEqual(sharpened.original, softened.result)
        self.assertNotEqual(sharpened.original, sharpened.result)
        self.assertEqual(preview.picture(index, image_id, "both", process=True).result, sharpened.result)

        for mode in ("sequential", "staged"):
            job_id = self.create(mode)
            self.assertIsNone(self.worker.start(job_id).wait(20).error_code)
            self.assertEqual(self.jobs.status(job_id).status, "completed")
            applied = {(record["stage_instance_id"], record["backend"]) for record in self.persisted_records()
                       if record.get("job_id") == job_id and record["code"] == "FREQUENCY_FILTER_APPLIED"}
            self.assertEqual(applied, {(low.id, "directml"), (high.id, "directml")})
        with (Image.open(self.root / "sequential" / path.name) as sequential,
              Image.open(self.root / "staged" / path.name) as staged):
            self.assertEqual(sequential.tobytes(), staged.tobytes())
        for mode in ("sequential", "staged"):
            with Image.open(self.root / mode / "a.jpg") as output:
                self.assertEqual(output.getexif()[271], "Worker Test Camera")

    def test_fixed_white_balance_replays_in_both_workers(self):
        editor = self.app.pipeline_service
        editor.load_data({"schema_version": 1, "name": "Same lighting", "stages": []})
        stage = editor.add_operation("core.white_balance", 1).stages[0].stage
        index = self.app.batch_service.index((self.source,))
        comparison = self.app.preview_service.stage_comparison(index, index.images[0].image_id, stage.id,
                                                              through_stage=False)
        comparison.render("pick", {})
        settings = comparison.neutral_settings(10, 10)
        editor.set_parameters(stage.id, settings)
        for mode in ("sequential", "staged"):
            job = self.create(mode)
            self.assertIsNone(self.worker.start(job).wait(30).error_code)
            self.assertEqual(self.jobs.status(job).status, "completed")
        for path, checksum in self.originals.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), checksum)
            with (Image.open(self.root / "sequential" / path.name) as left,
                  Image.open(self.root / "staged" / path.name) as right):
                self.assertEqual(left.tobytes(), right.tobytes())
                rgb = left.getpixel((10, 10))
                self.assertLessEqual(max(rgb) - min(rgb), 2)
                self.assertEqual(left.getexif()[271], "Worker Test Camera")

    def test_pause_cancel_and_one_active_worker(self):
        job_id = self.create()
        run = self.scripted_worker(job_id, CHECKPOINT_WORKER, "pause")
        self.wait_for_checkpoint(run)
        self.assertEqual(self.jobs.status(job_id).images[0].completed_stages, 1)
        with self.assertRaisesRegex(TimeoutError, "still active"):
            run.wait(0)
        with self.assertRaises(RetouchingError) as busy:
            self.worker.start(job_id)
        self.assertEqual(busy.exception.code, "JOB_RUNNER_BUSY")
        self.assertTrue(run.pause())
        self.assertFalse(run.pause())
        self.assertIsNone(run.wait(20).error_code)
        self.assertEqual(self.jobs.status(job_id).status, "paused")

        resumed = self.scripted_worker(job_id, CHECKPOINT_WORKER, "cancel")
        self.wait_for_checkpoint(resumed)
        self.assertTrue(resumed.cancel())
        self.assertIsNone(resumed.wait(20).error_code)
        status = self.jobs.status(job_id)
        self.assertEqual(status.status, "canceled")
        self.assertTrue(all(image.status == "canceled" for image in status.images))
        self.assertFalse(any(image.output.exists() for image in status.images))

    def test_abrupt_worker_exit_keeps_checkpoint_and_is_recoverable(self):
        job_id = self.create()
        run = self.scripted_worker(job_id, CHECKPOINT_WORKER, "crash")
        outcome = run.wait(20)
        self.assertEqual((outcome.exit_code, outcome.error_code, outcome.diagnostics_incomplete),
                         (79, "WORKER_EXITED", True))
        records = self.persisted_records()
        stderr = next(event for event in records if event["code"] == "WORKER_STDERR")
        self.assertIn("failure at committed checkpoint: пример", stderr["stderr_tail"])
        self.assertTrue(stderr["stderr_truncated"])
        crash = next(event for event in records if event["code"] == "WORKER_EXITED")
        self.assertEqual((crash["job_id"], crash["worker_run_id"], crash["exit_code"]), (job_id, run.run_id, 79))
        self.assertEqual(self.jobs.status(job_id).images[0].completed_stages, 1)
        self.assertEqual(self.worker.start(job_id).wait(20).exit_code, 0)
        status = self.jobs.status(job_id)
        self.assertEqual(status.status, "completed")
        self.assertTrue(all(image.attempts == len(status.stage_ids) + 1 for image in status.images))

    def test_worker_pauses_at_checkpoint_if_the_control_pipe_closes(self):
        job_id = self.create()
        run = self.scripted_worker(job_id, CHECKPOINT_WORKER, "pause")
        self.wait_for_checkpoint(run)
        with run._control_lock:
            run._process.stdin.close()
        self.assertEqual(run.wait(20).exit_code, 0)
        self.assertEqual(self.jobs.status(job_id).status, "paused")

    def test_invalid_and_overflowed_diagnostics_do_not_lose_job_progress(self):
        job_id = self.create("sequential")
        run = self.scripted_worker(job_id, OVERLOADED_WORKER)
        outcome = run.wait(20)
        self.assertEqual((outcome.exit_code, outcome.error_code, outcome.diagnostics_incomplete), (0, None, True))
        self.assertEqual(self.jobs.status(job_id).status, "completed")
        events = {event["code"]: event for event in self.persisted_records() if event.get("worker_run_id") == run.run_id}
        self.assertIn("JSONDecodeError", events["WORKER_PROTOCOL_FAILED"]["exception"])
        self.assertGreater(events["WORKER_LOG_DROPPED"]["dropped"]["INFO"], 0)
        self.assertGreater(events["WORKER_LOG_DROPPED"]["dropped"]["WARNING"], 0)
        self.assertIn("Diagnostic fallback", events["WORKER_STDERR"]["stderr_tail"])
        warnings = [event["message"] for event in self.persisted_records()
                    if event.get("worker_run_id") == run.run_id and event["code"] == "WORKER_TEST_WARNING"]
        self.assertCountEqual(warnings, [f"Warning {number}" for number in range(20)])

    def test_run_failure_returns_actionable_code_and_preserves_child_traceback(self):
        job_id = self.create("sequential")
        with runner_lock(self.root):
            run = self.worker.start(job_id)
            outcome = run.wait(20)
        self.assertEqual((outcome.exit_code, outcome.error_code, outcome.diagnostics_incomplete),
                         (1, "JOB_RUNNER_BUSY", False))
        self.assertEqual(self.jobs.status(job_id).status, "queued")
        event = next(event for event in self.persisted_records()
                     if event["code"] == "JOB_RUNNER_BUSY" and event.get("worker_run_id") == run.run_id)
        self.assertEqual(event["job_id"], job_id)
        self.assertIn("PermissionError", event["exception"])
        self.assertIn("msvcrt.locking", event["exception"])

    def test_launch_failure_has_cause_and_can_be_retried(self):
        job_id = self.create("sequential")
        with patch("retouching_tool.application.worker_service.subprocess.Popen", side_effect=OSError("denied")):
            with self.assertRaises(RetouchingError) as error:
                self.worker.start(job_id)
        self.assertEqual(error.exception.code, "WORKER_START_FAILED")
        event = self.log.records[-1]
        self.assertEqual(event["job_id"], job_id)
        self.assertIn("OSError: denied", event["exception"])
        self.assertEqual(self.worker.start(job_id).wait(20).exit_code, 0)
