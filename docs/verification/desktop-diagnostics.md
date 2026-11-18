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
| Report scope and contents | Session export included related rotated events and excluded unrelated sessions. Job export captured the frozen settings after the editor changed, committed status, and a simulated stderr trace. The Qt job export included a real worker warning and the job creation record while the search table remained filtered. |
| Redaction | The report replaced Windows drive and UNC paths, account and Unicode photo names in the tested fields and traceback text. The same photo path received the same alias across settings and events. Correlation IDs, exception type, and version URLs remained readable. ZIP entries contained only summary, settings, and events. Report generation succeeded after the generated source JPEG was removed. |
| Partial reports and publication | Invalid records, an unavailable journal, a simulated event size limit, and worker exit evidence appeared in the coverage notes. A simulated write failure and cancellation left no partial ZIP. An existing file kept its contents. The write failure retained its cause and output path in local diagnostics. |
| Boundaries | Headless and static Qt import checks and the existing log rotation and retention check passed. |

The dialog was also inspected with the Qt offscreen platform using generated
diagnostic entries.

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_diagnostic_history.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_diagnostic_reports.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_desktop_batch.py -k diagnostics_search -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -k import_boundary -k log_rotation -v
```
