# Desktop diagnostics verification

Focused checks ran on 2026-11-18 on Windows 11 with Python 3.14.7 in the
prepared DirectML environment. Tests used temporary logs, generated JPEGs,
and the CPU reference exposure operation through the real batch worker.

| Scenario | Verified outcome |
| --- | --- |
| Retained logs and rotation | Search returned bootstrap, active, rotated, and pre-settings-change records. The writer rotated its active Windows log while a search reader held it open. |
| Filtering and technical details | A case-insensitive Unicode path search combined with a severity threshold and job, photo, and stage IDs found the associated error and preserved its traceback. The newest 200 events are returned and matching memory and disk occurrences are counted once. |
| Invalid input and logging failure | Invalid UTF-8, oversized records, and an incomplete final record produced a counted warning with a technical cause. Valid neighboring records remained visible. A simulated unusable log path left the in-memory event and visible log-storage warning available. |
| Qt and worker integration | Job diagnostics opened with the frozen job's ID. Filtering found a real worker exposure warning, copying preserved technical details, and the record linked to the indexed photo, the saved job row after re-indexing, and the matching operation card. The folder action targeted the effective path and surfaced a simulated open failure. |
| Boundaries | Headless and static Qt import checks and the existing log rotation and retention check passed. |

The dialog was also inspected with the Qt offscreen platform using generated
diagnostic entries.

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_diagnostic_history.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_desktop_batch.py -k diagnostics_search -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -k import_boundary -k log_rotation -v
```
