# Headless staged job verification

## Job commands and recovery

- `ApplicationService.job_service.create(index, destination, mode="staged", settings=...)`
  validates paths and volume budgets, then freezes the pipeline, selected inputs,
  JPEG settings, runtime versions and checkpoint identities. Sequential mode is
  the default and existing version-1 sequential journals remain readable.
- `run(job_id, control=...)` prepares each enabled stage for a pass over the
  eligible images. It releases that stage before preparing the next one. A
  separate export pass publishes verified JPEGs after the final stage releases.
  The empty pipeline goes directly to decode and export.
- One image's lossless float32 TIFF, source metadata, and typed context form a
  checkpoint. The version-2 staged journal commits its manifest receipt and
  next-stage cursor together. The previous state can then be reclaimed, with
  shared TIFF or metadata blobs kept when the successor references them.
- Pause and cancel take effect between image-stage or export units. A resumed
  job verifies the checkpoint's identity, payload checksums and context before
  using it. It reuses a verified JPEG receipt if export was interrupted at
  publication. Storage errors leave the last committed progress recoverable.
- An interrupted unit can start up to three times. A missing or corrupt
  checkpoint can restart its image from the frozen source up to twice. A
  changed source or an isolated processing failure is recorded for its image.
  Other eligible images continue. An ownership or alias violation stops cleanup.
- `status(job_id)` exposes mode, enabled stage IDs, and each staged image's
  completed stage count. Staged image attempts count all started stage and export
  units. Published outputs and journals remain available after completion or
  cancellation. Cleanup removes owned checkpoint payloads and keeps store markers
  and unknown files.

Staged preflight includes the full image set, source headers, TIFF/manifest
overhead and one transactional replacement. Resume reserves additional
space for images that still need a checkpoint, one replacement for the largest
pending stage input, and remaining output. The runner checks each actual successor
size before committing it, including any geometry change. Scratch and output
reservations share a single free-space check when they use the same volume.

## Focused checks

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_jobs.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_artifacts.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_batch.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_pipeline.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -v -k headless -k static_qt_import_boundary
```

Generated fixtures cover sequential/staged pixel and EXIF equivalence, typed
context restored at an operation boundary, stage prepare/release order, and safe
checkpoint cleanup. Separate processes exited immediately before and after
the SQLite checkpoint commit. A third exited after the final JPEG rename. The
checks also exercised damaged TIFF and manifest data, disk exhaustion, remaining
space estimates, late output collisions, and both replay limits. The focused
checks passed 81 tests. The opt-in Windows junction test was skipped.

## 500-image reference run

On 2026-11-18, both modes completed the same 500 generated 40 × 30 JPEGs with
zero failures. All exported pairs matched in decoded RGB pixels and EXIF, every
source SHA-256 matched its original value, and staged TIFF payloads were reclaimed.
Repeating `run` on either completed job returned the same accounting.

The statistics/exposure/statistics pipeline ran on the CPU on the Ryzen 7 7840HS
development machine. The existing environment used Windows 11 build 26300,
Python 3.14.7, SQLite 3.50.4, Pillow 13.0.0, NumPy 2.5.5 and the installed
ONNX Runtime DirectML 1.24.4 package. The staged run took 57.47 s and the
sequential run took 17.21 s, timed inside `run` after job creation. The combined
Python process peaked at a 58.8 MiB working set. The fixture dimensions limit
these measurements to the orchestration check.

## Remaining validation

Worker transport has separate [verification](worker-transport.md). Desktop job
controls, physical power-loss durability, and actual Windows junction behavior
still need validation. The
reference stages exercise local orchestration and have no production inference
sessions. Representative portrait-scale memory, retouching quality, neural stage
resource release, and CUDA acceptance remain pending.
