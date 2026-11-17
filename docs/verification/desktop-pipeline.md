# Desktop pipeline editor

Milestone 5b adds a registry-driven operation picker and Move up, Move down
and Remove controls to the Qt Pipeline tab. Added instances start with unique
IDs and descriptor defaults and pass through the shared asset and context
checks. The cards retain their toggles, parameters, asset details and recovery
actions. The batch setup summary reflects editor changes.

**Open JSON** validates a portable pipeline before replacing the editor state.
**Save JSON** writes the editor's current order, enabled flags and parameters.
Successful edits and opens update the separate last-used pipeline. The editor
restores it on startup. Malformed imports and failed writes display an error and
retain the preceding valid state. Explicitly opened files receive changes only
through a subsequent save to that path.

## Focused verification

On 2026-11-18, verification used Python 3.14.7, PySide6 Essentials 6.12.0 and the
prepared DirectML environment on Windows 11.

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_qt_integration.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_pipeline.py -v -k add_registered -k nonempty_startup -k invalid_import -k json_roundtrip -k headless_registry
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_desktop_batch.py -v -k pipeline_change_during_freeze
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -v -k static_qt_import_boundary -k headless_import_boundary
```

The focused checks cover duplicate instances, boundary controls, parameter and
toggle retention across moves, and dependency revalidation with a synthetic
model fixture. They also cover JSON on Unicode paths, canceling Open, rejecting
a malformed import, simulated atomic-save denial with its diagnostic traceback,
and startup restoration. The batch integration check verifies that a changed
definition at freeze time leaves the saved job queued for review. Fresh-process
checks verify the Qt import boundary.

Qt checks used offscreen rendering and simulated the filenames returned by the
OS file dialogs. Portrait-model acceptance and CUDA hardware coverage remain pending.
