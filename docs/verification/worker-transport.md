# Batch worker transport

Verified on 2026-11-18 on Windows 11 build 26300, Python 3.14.7, and the
prepared DirectML environment with ONNX Runtime 1.24.4. The worker tests ran the
CPU reference stages on generated 40 × 30 JPEGs.

## Application commands

- After application startup, use `job_service.create(...)` to validate and freeze
  a job. `job_worker.start(job_id)` runs either saved schedule in a separate
  process with the prepared interpreter and the application's selected backend.
- `run.poll()` returns the outcome once the worker and diagnostic drain have
  completed. Headless callers can also use `run.wait(timeout)`.
  Read `job_service.status(job_id)` for committed progress and image results.
  Job-level errors and unexpected exits have separate outcome codes.
- `run.pause()` and `run.cancel()` submit requests to the current process. The
  scheduler checks them between sequential images or staged image-stage and
  export units. Loss of the control pipe requests a pause at the same boundary.
  Start the saved job again to resume a paused or interrupted run.
- One controller run can be active at a time. The existing workspace runner lock
  also prevents a second application instance or synchronous runner from taking
  control of the journals.

The worker uses the same scheduler, production registry, and recovery storage as
the synchronous service. Only commands, diagnostic records, and run outcomes
cross the pipes. A new worker does not load current editor settings. The parent
writes the worker's records to the existing session log with the originating
process ID and a run ID. Native stderr and Python fault output use a separate
pipe. The worker bounds its outgoing queue and allows three seconds to drain
on orderly exit. The outcome flags dropped or incomplete diagnostic delivery
even when the journal records a successful job.

A blocking read on inherited stdin stalled NumPy's native module loading on
this machine. The control reader uses the Windows API to check whether data is
available before reading. [Microsoft PeekNamedPipe reference](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-peeknamedpipe)

## Focused verification

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_worker.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_jobs.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -v -k diagnostic -k log_ -k headless -k qt_import_boundary
```

The 40 focused tests passed. Worker checks exercised both schedules with Unicode
paths, equal decoded exports, capture EXIF, source hashes, frozen pipeline
settings, pause/cancel/resume, loss of the control pipe, and lock contention.
A subprocess exited immediately after a staged checkpoint commit. Its exit code
and bounded stderr were captured, and a new worker completed the saved job.
Injected diagnostic overflow and an invalid frame were surfaced while committed
job progress remained readable. A simulated launch denial preserved its cause
and a later launch succeeded.

Desktop batch controls and job discovery are covered in
[desktop batch verification](desktop-batch.md). The worker checks did not
exercise native driver crashes or GPU inference. Portrait quality and CUDA
acceptance still require separate validation.
