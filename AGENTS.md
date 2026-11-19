# Repository instructions

## Product constraints

Read `docs/requirements.md`, `docs/architecture.md`, `docs/pipeline.md`, and
`docs/stages.md`, `docs/runtime.md`, `docs/diagnostics.md`, and `docs/workflow.md`
before substantial
work. Preserve the distinction between
confirmed user requirements, proposed defaults, and unvalidated assumptions.

- Windows 11 desktop only; photo processing is fully local.
- App name: Auto ReTouch. App author: Neyrome.
- Stages may modify pixels, typed context, or both. Preserve immutable source EXIF;
  validate context provenance/coordinates and required/optional dependencies.
- Except for metadata-free BMP, exports preserve source capture metadata and add tool/version provenance. Update
  technical fields to match the output; verify transfer and never silently drop EXIF.
- Initial GUI: PySide6 with Qt Widgets. Isolate toolkit code in `ui/qt/`; shared
  application services expose plain-Python commands/state/events and must run
  without Qt. Only the composition root wires the GUI to those services.
- Python 3.14 using a project-local virtual environment; assume system-wide Python
  3.14 is installed. Never silently downgrade Python for package compatibility.
- Root CMD scripts check/prepare the environment and launch via its interpreter;
  launch supports photo sources and an explicit pipeline override.
- Machine-local configuration sets checkpoint and temporary-artifact folders.
- Provide an in-app UI for editing application configuration; backend selection
  remains setup-only.
- Detailed local logging must support warning/error diagnosis and bug fixes. Follow
  `docs/diagnostics.md` for structured context, tracebacks, retention, and reports.
- Keep operations modular behind shared descriptors/lifecycle contracts so adding
  a stage does not require changing the UI shell or schedulers.
- Reference workload: approximately 500 JPEG studio portraits.
- Retouch all exposed skin, with multiple people supported in a single photo.
- Support JPEG, JPEG XL, PNG, TIFF and BMP exports with format-specific controls. BMP omits EXIF. WebP and AVIF come later.
- Minimum system RAM: 16 GB. Keep image/mask buffers and queues bounded.
- Paid photography use requires commercial-use rights for selected checkpoints
  and dependencies; do not infer weights' terms from code licensing alone.
- Disk reservations after indexing: sequential scratch is largest uncompressed
  input plus 200 MB; staged scratch is all inputs uncompressed plus 200 MB; output
  is twice the compressed source total. Account for actual representation and
  additional artifact/transactional overhead; sum budgets on shared volumes.
- Required presets: Natural and Beauty.
- Minimum GPU: consumer RTX 20-series with at least 6 GB VRAM.
- Development hardware: AMD Ryzen 7 7840HS APU with Radeon 780M Graphics,
  reported DirectX 12 Ultimate capability, and no NVIDIA CUDA GPU.
  Keep CPU-testable core modules and evaluate DirectML for compatible neural
  stages. Actual CUDA acceptance requires NVIDIA hardware; mark it pending here.
- Quality takes priority over throughput; a couple of hours unattended is
  acceptable, with no specified hard processing deadline.
- RAW support is outside the initial scope.
- Ordered operation list with add/remove/reorder and parameter panels; JSON
  save/load and last-used pipeline restoration.
- Every editable stage can be turned off. Missing required AI model files force
  that stage off with an error on its card. Preserve settings, revalidate context
  dependencies, and leave it off after recovery until the user enables it. Offer
  model-page links and exact required file paths for manual installation.
- Source folder with optional recursion; batch processing requires a destination
  distinct from source and outside source when recursive search is enabled.
  Previews run in memory and discard their images when the preview window closes.
- Thumbnail grid and selected-image previews of the whole pipeline or one operation.
- Sequential and staged batch modes share operation semantics. Staged execution
  persists lossless intermediates and releases models between stage passes.

## Current phase

The user authorized implementation on 2026-11-17. Runtime foundation (milestone 1a)
is implemented; see `docs/verification/runtime-foundation.md` and `docs/backlog.md`.
Python 3.14 and virtual-environment delivery are fixed. PySide6 Essentials 6.12.0
(Qt Widgets), ONNX Runtime 1.24.4 CPU/DirectML, and their pinned dependencies have
foundation-level import/inference checks on Windows 11/Python 3.14.7. CUDA hardware
acceptance and actual portrait-model compatibility/quality remain unvalidated.
When an AI model is needed, ask the user to download the exact files from the
publisher and place them under the configured model folder (`models/` by default).
Provide the model version, publisher link, expected filenames and destination
paths. The user performs the download and placement. Verify local files after
the user supplies them.
The user supplied a local testing folder with seven JPEG study shots on 2026-11-17.
All seven passed JPEG round-trip checks. Representative coverage and retouching
visual acceptance are pending. Keep these images outside source control.
Pipeline contracts/reference stages (3a) are now implemented; see
`docs/verification/pipeline-contracts.md`. The 3b lossless TIFF artifact slice is
implemented; see `docs/verification/lossless-artifacts.md`. Headless JPEG decode,
ICC/orientation normalization and verified export are implemented; see
`docs/verification/jpeg-boundary.md`. Headless source indexing, path validation
and initial disk preflight are implemented. See `docs/verification/batch-preflight.md`.
Headless sequential and staged runners persist frozen job snapshots and verified
JPEG receipts. Staged jobs also recover lossless image-stage checkpoints. See
`docs/verification/sequential-jobs.md` and `docs/verification/staged-jobs.md`.
Toolkit-free worker transport now runs and controls saved jobs from the prepared
interpreter. See `docs/verification/worker-transport.md`. The desktop batch workflow
now supports source selection, preflight, export settings, progress, pause, cancel,
and continue within the open batch. The progress dialog exports warnings and
errors to text. Discard finished and unfinished jobs when the dialog or app
closes. Later runs can skip existing exports. See `docs/verification/desktop-batch.md`.
No production neural stage or checkpoint has been selected. Synthetic reference
tests do not establish portrait quality.

## Working rules

- Preserve user edits and keep changes scoped to the active task.
- Keep Qt types, signals, dialogs, image-display objects, and item models out of
  processing/application contracts and worker messages. Verify headless operation
  and import boundaries. Build one GUI adapter initially, with no generic widget
  framework or second production GUI required for abstraction alone.
- Never send photos or derived portrait data to external processing services.
- Preserve original images. Use separate validated exports and recoverable jobs.
- Keep real portrait data and large model assets out of source control by default.
- Keep `.venv` and machine-local config/state out of source control; commit templates
  and reproducible dependency definitions. Launch must not install/upgrade packages.
- Only setup selects the ONNX Runtime and PyTorch backends, using independent
  prompts with CUDA as the default for each. Install the selected runtime builds
  and shared dependencies. Launch uses both choices recorded in `.venv`, without
  backend flags, config overrides, or automatic
  backend fallback. See `docs/runtime.md`; avoid conflicting runtime packages.
- Record model/code redistribution terms before choosing distributable assets.
- Do not silently lower output resolution or select a weaker retouching mode.
- Use bounded retries and account for every input, including failures.
- New operations and failure paths must produce actionable diagnostic events with
  job/image/stage context and preserved technical causes. Never silently swallow
  exceptions. Keep photos/model weights out of default reports and never upload logs
  automatically. Expose logging failures rather than claiming records were saved.
- Snapshot active jobs; editor changes must not mutate a running pipeline. Validate
  real path relationships, output collisions, JSON, and checkpoints before work.
- Preserve intermediate precision and metadata; encode the final output only at
  export. Limit temporary cleanup to application-owned job artifacts.
- Test behavior and recovery appropriately. Model changes also require visual
  review; automated scores alone do not establish acceptable retouching.
- Report hardware benchmarks only for hardware actually tested. Record image
  dimensions, runtime/model versions, peak memory, timing, and failure counts.
- Update relevant planning documents when scope or implementation decisions change.
- Use a single agent by default. Do not spawn sub-agents unless the user explicitly
  requests delegation. Workflow roles do not themselves authorize spawning.
- Follow `docs/workflow.md` for task completion and review evidence.
- On this development machine, use ONNX Runtime DirectML and PyTorch CPU for main
  application development. Run setup tests or change computing backends only
  when the task concerns setup-script improvements or bug fixes; ordinary app
  development must not switch backends or rerun preparation.
