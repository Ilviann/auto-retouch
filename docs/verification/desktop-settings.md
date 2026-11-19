# Desktop settings verification

On 2026-11-18, focused checks ran on Windows 11 in the prepared DirectML
environment with Python 3.14.7. The checks used temporary configuration files,
generated JPEGs and a synthetic model-file manifest.

| Scenario | Verified outcome |
| --- | --- |
| Save and Cancel | Cancel from the default settings leaves the configuration file absent. Save creates it. A custom configuration file retains its relative folder values and resolves them identically when reloaded. |
| Invalid input and write failure | Overlapping scratch/log paths, a file in place of a directory and an invalid logging budget are rejected. A simulated configuration write denial preserves the previous file, stage flags, autosave, scratch root and session writer. The diagnostic contains the configuration path and technical cause. |
| Model folder change | A missing fixture disables its producer and blocks its dependent stage. Restoring the folder makes the producer available. Explicit activation clears the dependency error and enables batch start. |
| Scratch folder change | An indexed selection automatically repeats its disk check. Selecting the source as scratch blocks start with a path-specific error. Correcting the folder recomputes the plan. |
| Session diagnostics | Changing folder and severity routes newly enabled DEBUG events to the selected log. Log rotation and retention checks pass. |
| Worker isolation | A live staged worker reaches a checkpoint, settings change, and the job pauses and resumes to two verified exports using its original scratch root. Worker events arrive in the newly selected log folder. New job snapshots record the new scratch and checkpoint paths. |

The settings dialog was rendered and inspected using Qt's offscreen platform.
The headless import checks also passed.

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_settings.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_desktop_batch.py -k test_settings -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_qt_integration.py -k model_card -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -k config -k qt_import_boundary -k headless_import_boundary -k log_rotation -k log_storage -v
```

## Persistent desktop state

Upgrade tests import the legacy pipeline edits, preset reference and source
selection into the application-root `state.json`. Concurrent state updates
preserve independent fields. Offscreen Qt restart tests exercise independent
sizes for the main window, photo and stage previews, and batch processing.
They also cover a malformed size and restoration of a maximized main window.
A subprocess test runs a frozen job in its original configured temporary
folder after Settings selects another folder. A cleanup test retires a journal
from the previous `.state/jobs` layout.
