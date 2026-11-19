# Single-screen desktop workspace

The workspace places source and destination controls above the pipeline editor
and photo grid. Source selection, recursion changes, and restored sources trigger
a header scan. **Rescan source folder** refreshes the current index. Clicking a tile
selects it for viewing. Its checkbox controls batch inclusion. The grid generates
thumbnails for all readable headers in scan order, with one local decode in
flight. It keeps each finished 108 × 88 icon in RAM until the scan changes or
the window closes. A failed decode shows a warning until the next scan.
Offscreen Qt tests count one decode attempt per photo across repeated scrolling
and exercise a source change while an old decode is still running.

The batch summary shows indexed temporary and JPEG output estimates before a
destination is set. Changing inclusion or schedule recalculates the estimates.
An unreadable included header makes the estimate incomplete. When a destination
and included photos are available, the workspace automatically checks folders and
displays required and available space per volume. **Start processing** checks for
existing outputs and offers to stop, skip the affected photos, or overwrite. The
workspace uses binary units for both estimates and free space.

Operation cards describe image and context inputs and effects from the operation
descriptor. Double-clicking a thumbnail or pressing Enter on it opens its source
without a destination. **Run pipeline** runs enabled stages in order and updates
the same window. Whole-pipeline and stage comparisons open in **Side-by-side**.
**Overlay** shows the result at full width and reveals the input while Shift is held.
Zoom and pan work from either pane and carry over when switching views.
Stage cards offer the two modal comparisons described below.
Processing previews run without a destination and use the shared JPEG decoder
and stage executor at full working resolution. The picture window
offers Fit and 100% views and displays available luminance measures.
Preview images are held in memory and discarded on close. Previews produce no
exported files, scratch artifacts, or saved jobs.

Processing previews are available while the batch worker is idle. Queued GPU
previews and neural-stage visual acceptance are pending.

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
**Preview in pipeline** uses the output of preceding enabled stages as the input to
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

On 2026-11-19, three focused offscreen Qt checks passed for stage preview
progress. A mixed-parameter reference stage exercised both the window-modal
progress panel and the fixed footer bar. The checks compared pane, progress bar
and control geometry across renders, verified that the manual panel yields to
resize approval, and closed a running preview through its progress panel.

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -p test_preview.py -v
.venv\Scripts\python.exe -m unittest discover -s tests -p test_desktop_batch.py -v -k preview -k modal_stage_comparison
```

On 2026-11-19, all four focused Qt preview checks in the second command passed
with generated JPEGs. Double-click and Enter opened the source. **Run pipeline**
processed the opened photo in the same dialog, including after the grid selection
changed. Tests verified side-by-side and full-width views in the photo preview
and both stage previews. They checked Shift press and release, restoration of
the result on window deactivation, and Space with a button or numeric editor focused.
They also exercised declining and retrying a resize and closing a photo preview
while a render was outstanding.

A focused offscreen Qt test uses a generated two-axis gradient JPEG to check
the pixels shown at the center and at several offsets. It covers wheel zoom at
the pointer, dragging the result pane, synchronized crops after a pipeline run,
switching to Overlay and holding Shift, zoom buttons, Fit, and retaining
the inspected detail when a stage parameter changes.

The **100%** checks used JPEGs larger than the 1,600-pixel overview. A headless
test verified the rotated source pixel bytes, native dimensions for both stage
scopes and the pipeline, and the thumbnail bound. An offscreen Qt test compared
a patch of adjacent physical screen pixels against the source and processed
samples in all four preview paths, including after a drag and holding Shift in Overlay.
It passed with `QT_SCALE_FACTOR` set to 1, 1.25, 1.5, and 2.

The main photo preview tests verified that **Run pipeline** immediately opens
a window-modal busy indicator on the first run and on a repeat run. They
covered returning keyboard focus to the comparison, closing the preview while
processing, and dismissing progress for resize approval and reopening it on retry.
