# Implementation backlog

Runtime implementation was authorized on 2026-11-17. A single agent performs the
implementer/reviewer responsibilities sequentially. Confirmed product requirements
remain in `requirements.md`; defaults and unvalidated engine assumptions remain
identified in their design documents.

| Task | Requirements | Status and completion evidence |
| --- | --- | --- |
| 1a. Python environment and setup-only backend selection | U21–U24, U27–U29 | Complete for available hardware: exact/hash-pinned locks, isolated Python 3.14 environment, default-CUDA menu, safe repeated setup/switches, ready metadata, read-only check. [Evidence](verification/runtime-foundation.md). CUDA hardware acceptance pending. |
| 1a. Launch/configuration and modular startup | U25, U26, U31, U32 | Complete foundation: root CMD launcher, caller-relative CLI paths, config-relative folders, explicit precedence, atomic last-used state, headless service, Qt startup window. Empty pipelines only until task 3. [Evidence](verification/runtime-foundation.md). |
| 1a. Bootstrap diagnostics | U30 | Complete foundation: UTF-8 JSONL/console, context/tracebacks, bounded records/rotation/memory, visible logging failures, shell fallback. Processing transport, diagnostic panel and reports belong to tasks 4/5. [Evidence](verification/runtime-foundation.md). |
| 2. Evaluation preparation | U03, U05–U08, U39, U42, U43, U46 | Pending representative local photos and user references. Prepare manifest, preset rubric, benchmark procedure, and commercially usable candidates without claiming synthetic fixtures establish appearance. |
| 3a. Pipeline contracts and reference stages | U09–U12, U23, U33, U36–U38 | Next independent implementation: versioned definitions, descriptors/registry/lifecycle, typed context and invalidation, CPU statistics/pixel references, missing-asset state. Use DirectML environment; no setup changes. |
| 3b. Precision, metadata, presets, engine feasibility | U05, U06, U17, U34, U35, U39, U42 | Pending: lossless state/context persistence, validated JPEG metadata round trips, Natural/Beauty definitions and user-reviewed model comparisons. Actual checkpoints require separate provisioning and rights evidence. |
| 4. Recoverable batch execution | U13–U20, U40, U43–U45 | Pending: folder/index validation, bounded schedulers/artifacts, disk budgets, JPEG export, retries, pause/cancel/recovery, warnings/failures and job accounting. |
| 5. Desktop workflow | U09, U16, U17, U30–U32, U36–U38 | Pending: folder controls, thumbnails, editor/cards, preview/revision handling, progress, searchable diagnostics and local redacted reports. Extend shared services before widget logic. |
| 6. Distribution and target-hardware acceptance | U01, U07, U21–U29, U42 | Pending: release dependency/license audit, asset provisioning instructions, offline full workflow, minimum 6 GB NVIDIA/16 GB RAM approximately 500-photo acceptance. |

Ordinary app verification uses the existing DirectML environment. Setup tests and
backend changes are allowed only for setup-script improvements or bug fixes.
