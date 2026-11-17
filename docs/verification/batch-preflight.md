# Source indexing and initial batch preflight

Verified on 2026-11-18 on Windows 11 build 26300 with Python 3.14.7 and Pillow
13.0.0 in the prepared DirectML environment. The checks used generated JPEGs and
ran headlessly. No model, study photo or GPU inference was needed.

## Implemented behavior

- `ApplicationService.batch_service.index()` accepts folders and explicit JPEG
  files. Folder discovery preserves relative paths, supports optional recursion
  and skips reparse points. Overlapping selections deduplicate canonical paths.
  The first selected folder containing a discovered file supplies its relative
  path. Standalone JPEGs use their filename.
- The index snapshots header dimensions, compressed byte count, filesystem
  identity and modification time. Header reads allocate no decoded pixel buffer.
  Corrupt or unreadable headers remain visible with per-image errors. Fix and
  re-index those inputs or explicitly deselect them to get a complete budget.
- `preflight()` rechecks the selected inputs and source roots, validates the
  existing destination and configured scratch root, and maps outputs under the
  destination. Existing target names, case-insensitive target conflicts,
  conflicting parent components and descendant reparse points block the plan.
  A missing scratch root or output subdirectory is checked through a temporary
  write probe at its existing ancestor. Setup of actual job folders is deferred
  to execution.
- Sequential scratch uses the largest RGB float32 image plus 200,000,000 bytes.
  Staged scratch uses the sum of those images plus the same base, TIFF and
  manifest limits per image, the bounded source header bytes for metadata, and
  one largest TIFF and manifest replacement. Both modes include caller-supplied
  extra scratch bytes and use twice the selected compressed total for output.
  A shared device requires the combined total. Each volume entry exposes free
  and required bytes and a shortfall makes the plan unready.

## Checks

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_batch.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_jpeg.py -v
```

All 18 batch checks and 12 JPEG regression checks passed. The checks covered
Unicode and mixed-case names, a bounded header read with pixel loading disabled,
explicit and overlapping selections, invalid and changed inputs, source/destination
boundaries, simulated alias and reparse handling, collisions, simulated scan and
write denial, and diagnostic correlation with error causes and tracebacks.

A separate check indexed 500 generated 40 × 30 RGB JPEGs in five subfolders with
pixel loading disabled. Preflight produced 500 distinct paths in each schedule
and estimated 200,014,400 sequential scratch bytes and 765,690,012 staged scratch
bytes. A read-only check queried distinct local `C:` and `D:` volumes and confirmed
separate budget rows. Folder probes on the workspace volume left no exports or
scratch folder. Temporary fixtures and their logs were removed.

## Remaining validation

Real junction and network path acceptance remain pending. Indexing validates JPEG
headers. Full stream decode and capture metadata validation occur at execution.
Index file stamps detect ordinary changes. Recovery needs content hashes and fresh
path checks. The plan estimates free space at the time of preflight. Physical
allocation, ongoing monitoring, resume budgets, scheduler integration and desktop
controls remain pending. The 500-file check exercised inventory and planning only.
