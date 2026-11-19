# Implementation backlog

Runtime implementation was authorized on 2026-11-17. A single agent performs the
implementer/reviewer responsibilities sequentially. Confirmed product requirements
remain in `requirements.md`; defaults and unvalidated engine assumptions remain
identified in their design documents.

## Implementation priorities

1. **Batch photo retouching (U49).** Continue with portrait evaluation and
   engine feasibility. Validate automatic color correction and exposed-skin
   retouching for multiple people, then refine Natural and Beauty using local
   previews and visual review. Validate the resulting pipeline in both batch
   schedules, including recovery, verified JPEG export, and target resource use.
2. **Logging and maintenance.** Queue standalone diagnostics and maintenance
   improvements behind batch retouching work. Grouped diagnostic summaries and
   further diagnostic tooling belong here. Keep job progress, actionable
   processing errors, and recovery in the primary workflow.

## Task status and evidence

| Task | Requirements | Status and completion evidence |
| --- | --- | --- |
| 1a. Python environment and setup-only backend selection | U21–U24, U27–U29 | Python 3.14 with separate ONNX Runtime and PyTorch menus, hash-pinned packages, execution probes and read-only validation. Development pair verified on DirectML and CPU. CUDA hardware acceptance pending. [Evidence](verification/runtime-foundation.md). |
| 1a. Launch/configuration and modular startup | U25, U26, U31, U32 | Complete foundation: root CMD launcher, caller-relative CLI paths, config-relative folders, explicit precedence, atomic last-used state, headless service, Qt startup window. Empty pipelines only until task 3. [Evidence](verification/runtime-foundation.md). |
| 1a. Bootstrap diagnostics | U30 | Complete foundation: UTF-8 JSONL/console, context/tracebacks, bounded records/rotation/memory, visible logging failures, shell fallback. Processing transport, diagnostic panel and reports belong to tasks 4/5. [Evidence](verification/runtime-foundation.md). |
| 2. Evaluation preparation | U03, U05–U08, U39, U42, U43, U46 | Seven user-authorized local JPEG study shots are available (2026-11-17); coverage and retouched references remain unreviewed. [Candidate model research](model-candidates.md) records sources, checkpoint availability and licensing and hardware questions. Prepare the manifest, preset rubric and benchmark procedure. |
| 3a. Pipeline contracts and reference stages | U09–U12, U23, U33, U36–U38 | Complete at contract/reference level: immutable versioned definitions, transactional headless editing/JSON/restore, registry/lifecycle, typed context and invalidation, CPU statistics/manual exposure, local checksum availability and Qt status/toggle/parameter cards. [Evidence](verification/pipeline-contracts.md). Actual models remain unselected. |
| 3b. Precision, metadata, presets, engine feasibility | U05, U06, U17, U34, U35, U39, U40, U42 | Lossless float32 TIFF/context persistence and headless JPEG color/orientation and verified export are implemented. [Artifact evidence](verification/lossless-artifacts.md) and [JPEG evidence](verification/jpeg-boundary.md). Proprietary metadata coverage, Natural/Beauty definitions, model comparisons, actual junction and resource acceptance remain pending. Checkpoints require separate provisioning and rights evidence. |
| 3b. Automatic exposure and tone | U05, U09, U17, U23, U33 | Implemented a CPU baseline with adjustable strength, a stop limit, and highlight protection. [Evidence](verification/auto-exposure.md). Portrait appearance and defaults await review. |
| 3b. White balance and face detection | U05, U09, U17, U33, U39, U42, U51, U62 | Automatic and sampled fixed white balance, expandable YuNet face boxes, and selectable Alibaba crop targeting work in preview and both schedules. [Color behavior](stages.md#white-balance-and-color-cast) and [verification](decisions/0005-yunet-face-detection.md). Exposed-skin masks and representative portrait acceptance remain pending. |
| 3b. Stage scaling and Alibaba retouching evaluation | U17, U33, U39, U42, U51, U52, U73 | Independent blemish cleanup and tone smoothing on PyTorch CPU, each with its own verified checkpoint, optional face targeting and resize consent. The editor skips the retired combined entry in presets and restored working pipelines. Visual acceptance and GPU validation remain open. [Stage behavior](stages.md#alibaba-skin-retouching-stage) and [scaling settings](pipeline.md#processing-scale-settings). |
| 3b. First AI operation and model lifecycle | U20, U23, U33, U37–U39, U42, U43 | PP-HumanSeg person foreground analysis, local checkpoint verification, fitted mask preview, and lossless foreground context are implemented. Both worker schedules passed DirectML runs on the seven study JPEGs with native memory measured at release. [Decision](decisions/0004-first-ai-checkpoint.md) and [evidence](verification/person-foreground.md). Exposed-skin targeting, portrait quality, and minimum-hardware acceptance remain pending. |
| 4. Recoverable batch execution | U13–U20, U40, U43–U45 | Headless indexing, path validation, disk preflight, and both schedules are implemented. Staged jobs commit and recover lossless image-stage checkpoints, reclaim predecessors, and observe pause/cancel between units. Both modes recover verified exports. [Preflight](verification/batch-preflight.md), [sequential](verification/sequential-jobs.md), and [staged](verification/staged-jobs.md) evidence. Representative resource acceptance remains pending. |
| 4. Batch worker transport | U19, U20, U25, U29, U30, U32 | A local process runs either saved schedule using the prepared interpreter. The application service provides pause, cancel, resume, and exit reporting. Worker diagnostics enter the application session log. [Evidence](verification/worker-transport.md). |
| 5a. Desktop batch workflow | U13–U15, U19, U20, U30–U32, U40, U44, U45, U68, U69 | Batch setup, progress, pause, continue, cancel and text report export are implemented. Closing the progress dialog discards the batch. [Evidence](verification/desktop-batch.md). |
| 5b. Desktop pipeline editor | U09, U11, U12, U31, U32, U36–U38 | Registered operations can be added, removed, reordered, toggled, and configured in Qt. JSON open/save and last-used restoration share the headless editor's validation and persistence. [Evidence](verification/desktop-pipeline.md). |
| 5. Single-screen desktop workspace | U09, U11, U13–U17, U31, U40 | Implemented. Folder controls, pipeline editor, selectable thumbnail grid, batch and format-specific export settings share one screen. [Export verification](verification/export-formats.md). A picture window shows originals and whole-pipeline or standalone-operation previews. [Evidence](verification/desktop-workspace.md). |
| 5. Operation inputs and outputs | U09, U33, U36–U38 | Implemented. Cards derive pixel and metadata effects and typed context inputs, outputs, and retention rules from the registered operation descriptors. [Evidence](verification/desktop-workspace.md). |
| 5. Automatic disk estimates | U13, U18–U20, U44, U45 | Implemented. Estimates appear after indexing and refresh with photo inclusion and schedule before a destination is chosen. The explicit disk check shows required and free space per volume. Both use binary units. [Evidence](verification/desktop-workspace.md). |
| 5. Desktop settings editor | U26, U29–U32, U48 | Implemented. Save validates and applies local folders and logging options, rechecks affected stages and disk preflight, and preserves frozen jobs. [Settings evidence](verification/desktop-settings.md). |
| 5. Desktop diagnostic search | U30–U32 | Implemented. Search retained and recent in-memory events, filter by severity and job/photo/stage ID, get copyable technical details, and reveal matching workspace items. [Evidence](verification/desktop-diagnostics.md). |
| 5. Local diagnostics reports | U30–U32 | Implemented. Export a redacted ZIP of available session or saved-job events, runtime and settings evidence, and coverage notes. [Evidence](verification/desktop-diagnostics.md). |
| 5. Modal stage comparison and tuning | U09, U16, U17, U32, U50 | Implemented. Select one thumbnail and compare a stage against the source or its upstream input. Tune draft parameters with automatic or explicit refresh, then save or discard. Previews run in memory without a destination and discard their images on close. [Evidence](verification/desktop-workspace.md#modal-stage-comparisons). |
| 5. Remaining photo preview workflow | U17, U31, U32, U58, U59 | Full-resolution detail inspection is implemented in all photo and stage previews. Queued GPU previews remain pending. [Desktop evidence](verification/desktop-workspace.md) |
| 5. Desktop workspace redesign | U03, U06, U09–U20, U30–U41, U44–U45, U48 | Deferred proposal. Use the [workspace design and review walkthroughs](desktop-redesign.md) when planning the later desktop pass. |
| Product identity alignment | U35, U47 | Auto ReTouch and Neyrome appear in runtime/UI/CLI and source package metadata. Verified JPEG provenance records Auto ReTouch and the application version while retaining source photographer credits. |
| 6. Distribution and target-hardware acceptance | U01, U07, U21–U29, U42 | Pending: release dependency/license audit, asset provisioning instructions, offline full workflow, minimum 6 GB NVIDIA/16 GB RAM approximately 500-photo acceptance. |
| 5. Remaining diagnostics and maintenance | U30, U49 | Lower priority. Grouped batch warning summaries remain pending. |

Ordinary app verification uses the existing DirectML environment. Setup tests and
backend changes are allowed only for setup-script improvements or bug fixes.
