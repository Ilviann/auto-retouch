# Headless sequential job verification

Verified on 2026-11-18 on Windows 11 build 26300 with Python 3.14.7, SQLite
3.50.4, and Pillow 13.0.0 in the prepared DirectML environment. The reference
stages executed on the CPU. No checkpoints, study photos, or inference probes
were used for these checks.

## Implemented behavior

- After application startup, `job_service.create(index, destination, settings=...)`
  runs pipeline and path/disk preflight and saves a job ID. It snapshots the
  selected inputs and their SHA-256 hashes, relative output paths, effective
  pipeline and skips, JPEG settings, checkpoint manifests and runtime versions.
- `job_service.run(job_id)` decodes one image at a time, prepares and releases
  each enabled stage in order, and exports through the verified JPEG adapter.
  A corrupt JPEG, changed source, stage error or conflicting output has a durable
  per-image failure status. Other eligible images continue. `status(job_id)`
  reads job and image progress without starting processing.
- Versioned SQLite journals live in `.state/jobs`, outside per-job scratch.
  A Windows workspace lock prevents simultaneous runners. The runner records a
  verified pending JPEG's filename, file identity and hash before the atomic
  no-overwrite rename. Recovery validates the recorded pending or published file
  and commits its image status. A conflicting file remains untouched.
- `JobControl` pause and cancel requests take effect between images. Calling
  `run` again resumes paused or interrupted jobs. Interrupted images with no
  recoverable JPEG can replay up to three total attempts. The runner checks the
  remaining output budget and sequential scratch allowance before each image.
  Disk exhaustion or a journal failure stops the run with recoverable progress.

## Checks

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_jobs.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_batch.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_jpeg.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -v -k headless -k static_qt_import_boundary
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_pipeline.py -v -k startup
```

All 16 sequential checks and 33 focused regression checks passed. Generated
fixtures exercised frozen settings, preservation of source camera EXIF, stage
life cycles and two separate process exits immediately before and after rename.
Each interrupted export recovered with the same JPEG bytes and attempt count.
The checks also covered an occupied destination, a source edit that preserved
size and modification time, corrupt scan data, simulated stage/storage/journal
failures, pause/cancel, writer exclusion, and snapshot corruption.

A separate sequential run completed 500 generated 40 × 30 JPEGs through the
statistics/exposure/statistics reference pipeline with zero failures. Each export
was reopened and checked for dimensions and camera EXIF. Every input matched its
original SHA-256. Running the completed job again returned the same accounting.
This check used tiny images and did not benchmark representative throughput,
peak memory, or portrait quality.

## Remaining validation

See [staged job evidence](staged-jobs.md) for staged scheduling and checkpoint
recovery and [worker evidence](worker-transport.md) for subprocess execution.
Desktop integration remains pending. Recovery was
exercised with abrupt process exit. Physical power-loss durability and real Windows
junction behavior still need validation. If a process exits before committing a
JPEG receipt, the untracked `.pending` file is left for inspection. Verified
exports and job journals are retained. Reference runs do not establish CUDA,
neural model compatibility, or retouching quality.
