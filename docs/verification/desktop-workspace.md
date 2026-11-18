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
The thumbnail toolbar's **Preview pipeline** runs enabled stages in order.
Stage cards offer the two modal comparisons described below.
Processing previews run without a destination and use the shared JPEG decoder
and stage executor at full working resolution. The picture window
displays a fitted original/result comparison and available luminance measures.
Preview images are held in memory and discarded on close. Previews produce no
exported files, scratch artifacts, or saved jobs.

Processing previews are available while the batch worker is idle. Full-resolution
detail inspection, queued GPU previews, and neural-stage
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

## Modal stage comparisons

Select one thumbnail to preview, independently of its batch inclusion checkbox.
**Preview** compares the source with the selected stage applied. The stage card's
**Preview pipeline** uses the output of preceding enabled stages as the input to
the comparison. A manually disabled stage can be inspected with both actions.
The modal window keeps draft settings, automatically refreshes exposure, and
uses **Preview changes** for parameters that request manual refresh. Save updates
the selected stage and last-used settings. Discard leaves the existing values in
place, and Cancel keeps the draft and comparison open.

Focused checks in the prepared DirectML environment used generated JPEGs and CPU
reference operations. They covered both baselines and upstream context, skipped
and downstream stages, cached baseline reuse and source revalidation, modality,
thumbnail selection, rendering without a destination, and release of image data
and the cached stage input on close,
automatic and manual refresh, stale result rejection, save/discard/cancel, and
recovery from a failed save. A test operation supplies the manual parameter.
An offscreen comparison render was inspected with a generated JPEG.

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -p test_preview.py -v
.venv\Scripts\python.exe -m unittest discover -s tests -p test_desktop_batch.py -v -k preview -k modal_stage_comparison
```
