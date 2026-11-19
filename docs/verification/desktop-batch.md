# Desktop batch workflow

Milestone 5a connects the Qt Widgets desktop to the existing JPEG index,
batch preflight, saved jobs and worker process.

## Desktop controls

- **Batch setup** accepts folders or explicit JPEGs from launch, restored
  source paths, or the file dialogs. The recursion toggle invalidates the index.
  Scanning reads headers, remembers the current paths, and lists every indexed
  JPEG. All entries start checked. **Check all** and **Uncheck all** change batch
  inclusion in one action and preserve the current preview selection.
  An unreadable header blocks planning until
  the affected photo is repaired or explicitly excluded.
- The destination must be an existing folder. Choosing folders, rescanning,
  changing included photos, or switching schedules automatically refreshes
  scratch and output estimates and required and available bytes per volume.
  **Start processing** checks the planned output filenames. If files already exist,
  it lists the affected paths and offers **Stop processing**, **Skip existing files**
  and **Overwrite existing files**. Stop is the default and Escape action.
  Start revalidates paths, free space and pipeline readiness, then
  freezes the pipeline and JPEG quality, chroma and progressive settings.
  If validation changes the pipeline definition, the job stays queued for review.
- The processing modal shows frozen settings, skipped operations, per-photo attempts and
  warnings, and committed progress. Staged progress shows work units and per-photo
  completed stages. Skipped, failed and canceled photos count toward units accounted for.
  The elapsed clock accumulates active processing time observed for the current
  job in this window and freezes when the worker stops. Each start or continue
  estimates time remaining from newly accounted photos or staged work units.
  Files skipped at creation and checkpoints from earlier runs supply no timing samples.
  Worker errors and incomplete diagnostic delivery have their own notice.
- **Pause** and a confirmed **Break** apply to an active worker at its next
  scheduler boundary. Escape and the processing window's close control also
  ask before breaking an active run. **Continue** uses the paused batch's frozen
  settings. **Discard batch** closes it and releases its journal and checkpoints.
  Closing the application asks for confirmation, with **No** selected by default.
  Confirming stops the worker at a safe boundary and discards the batch. On
  relaunch, the first scan and preflight use the saved recursion, destination,
  processing mode and export settings. Start a fresh batch and select **Skip existing files**.
- **Export warnings and errors…** saves the messages received so far to a UTF-8
  text file. It works while processing, while paused and with final results on
  screen. The temporary report includes job, photo and operation context. Its
  coverage is independent of general log rotation, severity and table limits.
- The Qt adapter uses one background thread for scanning, preflight, freezing
  jobs, launching workers and reading journals. Application commands and
  worker transport retain their plain-Python and local process boundaries.

## Focused verification

A restart test declined application closure during a staged PNG batch, confirmed
the worker was still active, then closed the application with one confirmation.
After relaunch, the desktop indexed the nested JPEG using the restored settings.
Choosing **Skip existing files** preserved one existing PNG and exported the
remaining photo. A second restart check changed settings for all five formats
and immediately closed the workspace, then checked every restored control.

On 2026-11-19, five focused Qt integration tests passed for bulk photo
selection, sequential export, staged timing, pause/continue and worker exit.
A controlled clock with a real staged worker verified the remaining-time
countdown between checkpoints and the frozen elapsed duration during a pause.

The output-collision checks used generated photos and real local workers. Desktop
tests exercised all three dialog choices, destination selection and rescan with
occupied filenames, a mixed skip/export run with space for the remaining photo,
and a staged PNG overwrite. Headless tests covered all-skipped batches in both
modes, source hard links, late collisions, and export verification failure.
Process interruptions before and after replacement recovered the verified export.

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
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_jobs.py -v -k discard_ -k frozen_pipeline_compression_exif
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -v -k headless_import_boundary -k static_qt_import_boundary -k explicit_settings_override
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_qt_integration.py -v
```

All 12 focused tests passed. The checks cover recursive scanning, explicit file selection via launch,
unreadable headers, recursive destination rejection, a collision introduced
after preflight, JPEG settings and capture EXIF, and unchanged source bytes.
The current checks cover staged checkpoint progress, pause and continue,
cancel, discard on close, skip-existing on a fresh run, and recovery from a
worker exit within the open batch. Text export checks cover worker and desktop
events, more than 200 warnings, Critical-only general logging, Unicode, and a
failed export followed by a successful retry. Headless checks cover cleanup
after interruption and protection of other open batches and unrelated files. The setup and
completed job layouts were inspected with Qt offscreen rendering.

The desktop [pipeline editor](desktop-pipeline.md) was added in milestone 5b.
Configuration editing, thumbnails, previews, searchable diagnostics and redacted
reports remain pending.
Portrait-model visual acceptance and representative resource acceptance still
need separate validation, including CUDA hardware testing.
