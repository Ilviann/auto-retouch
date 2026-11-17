# Logging and diagnostics contract

Logging must make warnings, failures, and unexpected processing results useful
for product improvement and bug investigation. Bootstrap logging and batch worker
delivery are implemented. The full diagnostics panel, batch summaries, and report
export remain planned.

Milestone 3a emits pipeline command failures, asset-unavailable/recovered and
context-blocked events. Stage sessions emit preparation, completion, release and
failure events plus explicit skips, measurements and clipping warnings, correlated
with job/image/stage instance, operation/version, backend and attempt. Technical
exceptions retain tracebacks and chained causes at INFO settings; unreadable model
issues include their captured technical cause. The initial Qt cards expose
readiness and command errors and retain a diagnostics-storage warning after a
command. Full diagnostic search and export remain pending.

The 3b artifact store adds `ARTIFACT_COMMITTED`, `ARTIFACT_VALIDATED`,
`ARTIFACT_WRITE_FAILED`, `ARTIFACT_INVALID`, `ARTIFACT_STORE_FAILED` and explicit
retirement/collection outcomes. Write/read failures preserve technical causes and
tracebacks; image-stage writes carry the existing runtime correlation. Storage
creation/collection use job/store IDs. No pixel or metadata blob bytes enter these
records. See [artifact failure evidence](verification/lossless-artifacts.md).

JPEG events include job, image, attempt and preview request IDs where supplied.
Processing preview preflight and stage events carry the preview request ID for
correlation with picture failures. Thumbnail failures identify the indexed photo
and appear on its tile. Display and processing errors preserve their technical
causes in the local logs.
`INPUT_JPEG_DECODED` records oriented dimensions and color handling.
`INPUT_EXIF_THUMBNAIL_INVALID` and `INPUT_SRGB_ASSUMED` expose recoverable input
issues. Input metadata/decode errors, unsupported export metadata, write errors,
collisions and `OUTPUT_VALIDATION_FAILED` carry causes or tracebacks.
`OUTPUT_JPEG_VERIFIED` records the pipeline hash and compression options after
publication. Failed cleanup has its own event. See
[JPEG verification](verification/jpeg-boundary.md).

Batch preparation records header and scan failures with the index ID, source path
and image ID where applicable. It reports invalid or changed paths, conflicts and
write-probe failures with tracebacks, and includes the job ID when supplied.
`BATCH_SPACE_AVAILABLE` and `BATCH_SPACE_INSUFFICIENT` report paths and byte totals
per volume. See [batch preflight evidence](verification/batch-preflight.md).

Sequential jobs emit creation, skipped-stage, image result, bounded replay and
verified export recovery events. The SQLite journal holds durable per-image
attempts, warning counts and failures. Run summaries report status counts, and
stage/JPEG events retain their existing image and attempt correlation. A failed
journal write interrupts the run so uncommitted work can be recovered. See
[sequential job evidence](verification/sequential-jobs.md).

Staged jobs add per-image stage cursors and correlated artifact events.
`JOB_IMAGE_REPLAYED` marks an interrupted unit retry. `JOB_ARTIFACT_REPLAY`
marks a rewind to the frozen source when a checkpoint is unreadable.
`JOB_UNIT_INTERRUPTED` links a storage or journal error to the affected image
and stage. The status snapshot continues to show the last committed cursor.
See [staged job evidence](verification/staged-jobs.md).

The batch worker sends bounded JSON records with the application session ID,
worker run ID, originating process ID, and job/image/stage context. The parent
drains events in the background and writes them through the session's existing
log writer. The worker queue reserves space for warnings and errors. Overflow
reports dropped counts and sends warnings/errors to stderr as a fallback.
The parent captures the last 8 KiB of stderr and indicates truncation.
`WORKER_EXITED` reports an unexpected exit code and the last observed image
and stage where available. `WorkerOutcome` exposes incomplete diagnostic delivery
alongside execution errors. See [worker evidence](verification/worker-transport.md).

The desktop Job tab shows committed per-image outcomes, warning counts, stage
progress and worker errors. It surfaces incomplete worker diagnostic delivery
and keeps session log-storage failures visible across tabs. Saved-job discovery
reports unreadable journal entries with `JOB_DISCOVERY_SKIPPED`, the job ID, and
the technical cause. An unexpected desktop command exception is recorded as
`DESKTOP_BATCH_COMMAND_FAILED`. Full log search and redacted reports are pending.
See [desktop batch evidence](verification/desktop-batch.md).

Milestone 1a writes schema-version-1 UTF-8 JSONL and console messages through
`diagnostics/service.py`, with UTC millisecond times, session/process identity,
codes, structured context, and exception chains at INFO settings. Setup child
processes have distinct log files and `parent_session_id` correlation. Output from
their package manager/provider checks is captured by setup, with bounded tails
and credential redaction. Individual records are limited to 64 KiB; truncation is
marked, and memory fallback retains the latest 100 events.

Bootstrap/config-error fallback is `<app-root>/logs`. If this directory fails,
use the console and memory with an explicit `LOG_WRITE_FAILED`; no successful
persistence is claimed. A failed configured log path is also shown in startup
notices. Retention acts only on owned UUID-named JSONL files; retention failures
are distinct from write failures. Bootstrap logging is synchronous. Worker log
delivery and persistence run in background threads.

Before Python or the installed package can run, CMD scripts record fixed codes in
`logs/bootstrap-errors.log` and print remediation. This minimal fallback uses
local shell timestamps and rotates at 64 KiB with one backup; configurable age
retention applies to JSONL, not this early shell fallback. Failed fallback writes
are reported in the console. The batch worker enables Python fault diagnostics
and captures stderr where the failing process can still produce it.

## Outputs and presentation

- Write UTF-8 structured JSON Lines logs with a versioned event schema. Show
  readable messages in the console and an in-app diagnostics panel from the same
  events. Persist logs outside disposable processing artifacts.
- Provide INFO, WARNING, ERROR, CRITICAL, and DEBUG levels. Default to INFO;
  configurable DEBUG adds diagnostic detail without changing processing behavior.
- The diagnostics panel filters by severity, job, image, and stage; supports search,
  copying technical details, and opening the log folder. Image/stage warnings link
  to the affected item instead of repeatedly interrupting a 500-photo batch.
- Show a concise explanation of what happened, whether processing continued or
  an output was produced, and a concrete suggested action where known. Keep the
  full technical cause and traceback available behind the message.
- End each batch with counts of successes, images with warnings, failures,
  canceled/unprocessed inputs, retries, and grouped warning/error reasons. Expose
  affected files and distinguish completion with warnings or partial failure from
  clean success. Retain these summaries with job history.

## Event context

Each event includes a UTC timestamp with milliseconds, severity, stable event
code, human-readable message, module/component, session ID, and process identity.
Show times in the user's local timezone in the UI. Use a monotonic clock for
elapsed durations rather than subtracting wall-clock timestamps.

Attach applicable job, image, stage-instance, operation/version, attempt, and
preview-request IDs. Preserve correlation through worker processes, retries,
resumes, setup, and launch; fields that do not apply are omitted. A repeated
operation in one pipeline must remain distinguishable from other instances.

Record at the appropriate scope:

- Application/build version, Python version and executable, Windows version,
  dependency-lock identity, important package versions, installed backend,
  actual provider/device and driver/runtime versions when obtainable.
- The job's effective pipeline snapshot/hash, stage parameters, model asset
  identifiers/checksums, precision, source identity, image dimensions and color
  handling, scheduling mode, and artifact/output locations.
- Stage prepare/process/release, cache hit/miss/invalidation, model load/unload,
  timings, retry decisions, cancellation, resume, and export validation outcomes.
- Peak or sampled memory and available disk space where supported, especially
  around failures. Mark unavailable measurements explicitly; never invent values
  or force GPU synchronization on every stage solely for routine logging.
- Exception type, cause chain, traceback, underlying runtime/OS error, and operation
  context for failures. Do not require DEBUG mode to obtain an error traceback.

Avoid duplicating the full environment and pipeline snapshot on every event; use
references that remain resolvable in an exported report. Store normal image-stage
boundaries/timings at INFO and high-volume internals at DEBUG, with bounded payloads.

## Warnings and failures

Use stable searchable codes, for example `MODEL_OUT_OF_MEMORY`,
`CHECKPOINT_MISSING`, `INPUT_DECODE_FAILED`, `OUTPUT_WRITE_FAILED`,
`ARTIFACT_INVALID`, and `WORKER_EXITED`. These are illustrative codes until the
operation/error catalog is implemented.

Each code has a defined scope and disposition: informational warning, declared
no-op, bounded retry, failed image, failed stage pass, or aborted job. A stage
returning unchanged content due to absent detected regions must explicitly declare
that behavior and raise a visible warning when appropriate; an actual exception
must not be converted into silent pass-through success. Visual-quality flags are
identified as heuristics rather than guarantees of acceptable retouching.

Missing checkpoints disable the affected editor stage and set its card error
state; emit a correlated event naming every missing asset and expected location.
Distinguish this preflight unavailability from a failed operation inside an active
job. Log recovery/rechecks, subsequent user enable/skip actions, and dependency
blocking. Job summaries list unavailable/skipped stages so a successful reduced
pipeline is not reported as if every configured operation ran.

Log each retry's reason and outcome. Preserve the original cause when retries are
exhausted. Report the causal exception once with correlation to subsequent job/UI
events rather than repeating identical tracebacks at every call boundary.
Rate-limit repetitive runtime warnings with visible occurrence counts and first/
last occurrence times; the final summary must still account for every affected
image. Always retain the first diagnostic example and newly distinct causes.

Capture Python warnings and relevant backend messages. Monitor main-thread,
background-thread, and worker failures; collect worker exit codes and stderr even
when no normal result arrives. Use Python fault diagnostics where supported for
native failures. An abrupt kill, driver failure, or power loss cannot guarantee
a complete traceback: record the last known image/stage and classify unexpected
termination accurately instead of assuming a retouching error.

## Setup and launcher diagnostics

Start bootstrap diagnostics before `.venv`, configuration, or the GUI is ready.
Preparation records the selected backend, interpreter discovery, dependency
installation/check results, package-manager output on failure, validation outcome,
and exit code. Launch records the prepared backend and startup failures. Mask
credentials that may appear in package-index URLs or subprocess output.

If configuration is unreadable, use a documented local fallback log location and
print it to the console. `prepare_env.cmd --check` may produce diagnostics while
remaining read-only with respect to the environment and backend metadata.

## Implementation and retention

Use Python's standard logging facilities behind a shared diagnostics service.
Give each module a named logger and pass structured context from the execution
layer; individual operations do not configure handlers or write their own files.

Route worker events to one writer using a dedicated logging channel so processes
do not compete to rotate the same file. Keep disk writes and UI delivery off the
inference/UI critical paths. Preserve structured exception details across the
process boundary. Avoid routing a logging transport's own diagnostics recursively
back into that transport.

Bound buffers and record sizes. Under overload, discard/rate-limit DEBUG detail
first and emit a dropped-record summary; warnings/errors must have an explicit
fallback if the normal writer cannot accept them. Flush promptly on errors and
drain on orderly shutdown with a bounded wait. A logging failure must not deadlock
the app or recursively flood another logger.

Proposed configuration: `paths.logs`, `logging.level`, `logging.max_file_mb`,
`logging.max_total_mb`, and `logging.retention_days`. Defaults are INFO, 10 MB per
file, 200 MB total retained logs, and 14 days. Apply size/age rotation to owned logs
and bootstrap files, and report retention failures. Preserve durable job summaries
separately from rotated event logs. Cleanup never targets images, checkpoints,
pipeline JSON, or job-recovery metadata.

If logs cannot be persisted because of permissions or disk exhaustion, show a
persistent diagnostics warning and use console plus a bounded in-memory fallback.
Do not claim that missing records were saved. Job/output storage failures follow
their own error policies; log failure alone must not corrupt their state.

## Local bug report export

Provide an Export diagnostics action for a selected session or job. Create a local
ZIP with a readable summary, relevant logs and available crash stderr, environment/
backend details, effective pipeline/settings, model identifiers/checksums, and
failure context. Mark rotated/unavailable details as missing rather than implying
the report is complete. Logs alone do not guarantee a reproducible model result.

Exclude photos, thumbnails, model weights, and other processing artifacts by
default. Replace user-specific paths, usernames, and photo names consistently in
the exported copy, including free-text exceptions where possible; keep correlation
IDs intact. List the contents for inspection. Preserve detailed local originals;
export never uploads or automatically sends the report anywhere.

## Acceptance evidence

Exercise corrupt JPEGs, missing/wrong checkpoints, invalid folders, simulated
memory exhaustion, failed export, worker termination, invalid configuration,
retries, and resume. Verify useful context and correct UI/job summaries at INFO,
including tracebacks for exceptions. Label simulated failures as such in tests.

Check multi-process correlation, Unicode paths/messages, repeated-warning counts,
rotation/retention, invalid log paths, disk-full/write failure, buffer overload,
shutdown flushing, and loss reporting. Test a local diagnostics export for relevant
records, consistent path redaction, and exclusion of image/model content. Add a
diagnostic event and failure-path check with each new operation or significant
failure-handling change.

## Technical references

- [Python 3.14 logging cookbook](https://docs.python.org/3.14/howto/logging-cookbook.html)
  covers module loggers, structured logging patterns, and multi-process logging.
- [Python logging handlers](https://docs.python.org/3.14/library/logging.handlers.html)
  describes stream/file, rotation, and queue-based handlers.
- [Python fault diagnostics](https://docs.python.org/3.14/library/faulthandler.html)
  documents traceback capture for supported faults and its platform limitations.
