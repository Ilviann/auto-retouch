# Desktop batch workflow

Milestone 5a connects the Qt Widgets desktop to the existing JPEG index,
batch preflight, saved jobs and worker process.

## Desktop controls

- **Batch setup** accepts folders or explicit JPEGs from launch, restored
  source paths, or the file dialogs. The recursion toggle invalidates the index.
  Scanning reads headers, remembers the current paths, and lists every indexed
  JPEG. All entries start selected. An unreadable header blocks planning until
  the affected photo is repaired or explicitly excluded.
- The destination must be an existing folder. Choosing folders, rescanning,
  changing included photos, or switching schedules automatically refreshes
  scratch and output estimates and required and available bytes per volume.
  **Start processing** revalidates paths, free space and pipeline readiness, then
  freezes the pipeline and JPEG quality, chroma and progressive settings.
  If validation changes the pipeline definition, the job stays queued for review.
- The processing modal shows frozen settings, skipped operations, per-photo attempts and
  warnings, and committed progress. Staged progress shows work units and per-photo
  completed stages. Failed and canceled photos count toward units accounted for.
  Worker errors and incomplete diagnostic delivery have their own notice.
- **Pause** and a confirmed **Break** apply to an active worker at its next
  scheduler boundary. Escape and the processing window's close control also
  ask before breaking an active run. A paused job can be continued, closed
  for later, or canceled with confirmation. Closing the main application
  requests a pause and waits for the worker to stop. **Open saved job…** lists
  local journals by last update. **Continue** uses the frozen job settings.
- The Qt adapter uses one background thread for scanning, preflight, freezing
  jobs, launching workers and reading journals. Application commands and
  worker transport retain their plain-Python and local process boundaries.

## Focused verification

On 2026-11-19, 12 focused Qt integration checks and two headless cancellation
checks passed in the prepared environment. Generated JPEGs and local workers
exercised both folder-selection orders, same-folder reselection, rescan after
an output collision, and a destination change during an in-flight disk check.
The modal checks covered progress, pause, continue, saved-job recovery and
both answers to the Break confirmation. Headless checks verified that cancellation
keeps completed exports and retires staged checkpoints. The paused processing
window was also inspected in a Qt offscreen render.

Verified on 2026-11-18 on Windows 11 build 26300, Python 3.14.7 and the prepared
DirectML environment with PySide6 Essentials 6.12.0. Integration checks used
generated 40 × 30 JPEGs and CPU reference stages with real local worker subprocesses.

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_desktop_batch.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_jobs.py -v -k saved_jobs_discovers -k frozen_pipeline_compression_exif
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -v -k headless_import_boundary -k static_qt_import_boundary -k explicit_settings_override
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_qt_integration.py -v
```

All 12 focused tests passed. The checks cover recursive scanning, explicit file selection via launch,
unreadable headers, recursive destination rejection, a collision introduced
after preflight, JPEG settings and capture EXIF, and unchanged source bytes.
They also cover staged checkpoint progress, pause, reopening and resume,
cancel, close-to-pause, frozen pipeline review, and recovery from a simulated
worker exit. Headless checks cover frozen status metadata, read-only saved-job
discovery with a malformed entry, and the Qt import boundary. The setup and
completed job layouts were inspected with Qt offscreen rendering.

The desktop [pipeline editor](desktop-pipeline.md) was added in milestone 5b.
Configuration editing, thumbnails, previews, searchable diagnostics and redacted
reports remain pending.
Portrait-model visual acceptance and representative resource acceptance still
need separate validation, including CUDA hardware testing.
