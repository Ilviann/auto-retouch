# Single-screen desktop workspace

The workspace places source and destination controls above the pipeline editor
and photo grid. Source selection, recursion changes, and restored sources trigger
a header scan. **Scan sources** refreshes the current index. Clicking a tile
selects it for viewing. Its checkbox controls batch inclusion. Thumbnails
decode locally one at a time, and the grid releases offscreen icons.

The batch summary shows indexed temporary and JPEG output estimates before a
destination is set. Changing inclusion or schedule recalculates the estimates.
An unreadable included header makes the estimate incomplete. **Check disk space**
validates paths and collisions and adds the required and available space per
volume. The workspace uses binary units for both estimates and free space.

Operation cards describe image and context inputs and effects from the operation
descriptor. **View original** can open an indexed image without a destination.
**Preview pipeline** runs enabled stages in order. **Preview operation** runs
the selected available stage in isolation, including a manually disabled stage.
A required upstream analysis dependency produces an actionable preview error.
Each processing preview validates the destination and uses the shared JPEG
decoder and stage executor at full working resolution. The picture window
displays a fitted original/result comparison and available luminance measures.
Previews produce no exported files or saved jobs.

Processing previews are available while the batch worker is idle. Full-resolution
detail inspection, cumulative previews, queued GPU previews, and neural-stage
visual acceptance are pending.

## Focused verification

On 2026-11-18, verification used the prepared DirectML environment on Windows 11,
with Python 3.14.7 and PySide6 Essentials 6.12.0. The preview tests used generated
JPEGs and CPU reference operations. The desktop batch tests also exercised real
local worker subprocesses.

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -p test_desktop_batch.py -v
.venv\Scripts\python.exe -m unittest discover -s tests -p test_qt_integration.py -v
.venv\Scripts\python.exe -m unittest discover -s tests -p test_preview.py -v
.venv\Scripts\python.exe -m unittest discover -s tests -p test_batch.py -v -k budget
.venv\Scripts\python.exe -m unittest discover -s tests -p test_jpeg.py -v
.venv\Scripts\python.exe -m unittest discover -s tests -p test_foundation.py -v -k static_qt_import_boundary -k headless_import_boundary
```

Checks covered automatic scans, thumbnail loading, estimate refresh, before/after
display, discarded stale results, stage card data, JPEG orientation and metadata,
destination and dependency errors, preview diagnostics, and saved-job
pause/resume/cancel. Offscreen renders were reviewed with generated photos.
