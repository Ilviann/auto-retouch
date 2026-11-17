# Development and agent workflow

Development proceeds from an agreed portrait workflow to measured engine quality,
then to a reliable desktop batch application. Implementation was authorized on
2026-11-17. Runtime foundation (1a) is complete at the available-hardware level;
see [backlog](backlog.md) and [evidence](verification/runtime-foundation.md).
Pipeline contracts/reference stages (3a) are complete at the synthetic contract
level; see [pipeline evidence](verification/pipeline-contracts.md). Next portable
work is 3b precision/artifact/metadata validation; actual portrait quality still
requires representative user photos and reviewed model provisioning.

## Development stages

| Stage | Deliverable | Completion evidence |
| --- | --- | --- |
| 1. Requirements | Confirmed constraints, proposed defaults, open decisions, acceptance criteria | User corrections incorporated; unknowns explicitly recorded. |
| 1a. Runtime foundation | Python 3.14 metadata/dependency definitions, root environment/launch CMD scripts, setup-only backend menu with CUDA default, configuration and bootstrap logging, modular package skeleton | Clean/repeated preparation and backend switching; single-backend metadata; check-only behavior; launcher uses recorded backend; CLI/config precedence; startup error evidence; imports under `.venv` on Python 3.14. |
| 2. Evaluation preparation | Local image manifest, preset rubric, benchmark procedure, candidate shortlist | Representative images and permission to use them; identifiable photos stay outside public version control. |
| 3. Pipeline contract and engine feasibility | Versioned JSON, operation registry/lifecycle, conventional reference operation, neural-stage candidates, Natural/Beauty definitions | JSON round-trip and validation; user-reviewed retouching comparisons; measured RAM/VRAM and timing; dependency/weight inventory. |
| 4. Recoverable batch schedulers | Folder validation, input manifest, sequential and staged execution, lossless artifacts, export, retry, pause/cancel/recovery | Approximately 500 images in both modes; mode-equivalence, disk/memory, model release, and interruption checks. |
| 5. Desktop workflow | Toolkit-independent application commands/state/events and a PySide6 Qt Widgets adapter for configuration editing, folder controls, thumbnails, pipeline editing, previews, progress, diagnostics, and review | Complete user workflow; configuration validation/save/cancel and restart persistence; headless service tests; Qt import boundaries; last-used pipeline restoration; UI and worker validation agree. |
| 6. Distribution | Application folder, dependency lock, configuration template, CMD scripts, and model provisioning instructions | Clean setup using preinstalled Python 3.14; offline run after provisioning; minimum-hardware validation; operating instructions. |

Avoid building a polished interface around an engine whose retouching quality has
not been established. Keep development evaluations repeatable by recording exact
model, settings, input identities, hardware, and software versions.
Begin pipeline verification with context-only and pixel-edit reference stages,
then context persistence and EXIF round trips, before introducing detector/region
models and targeted retouching. The [Initial stage catalog](stages.md) describes
the proposed operations and validation priorities.
Python 3.14 is fixed. Check GUI/image/neural package compatibility early and
record unsupported candidates rather than silently using an older Python version.
Runtime foundation work begins with application implementation authorization, not
as an implicit side effect of editing these requirements.

Representative user testing photos and retouched references are not available yet;
the user plans to provide them later. Continue foundation and functional checks
with generated/synthetic fixtures, but keep visual-quality and representative
model acceptance pending. Do not present synthetic orchestration tests as evidence
of good full-body or multi-person retouching. Confirm actual checkpoint commercial
usage terms for paid photography before adoption.

## Validation on the available machine

The user reports an AMD Ryzen 7 7840HS APU with Radeon 780M Graphics, DirectX 12
Ultimate capability, and no NVIDIA CUDA GPU. Use three
validation levels, recording unavailable coverage rather than disguising it:
Prepare the appropriate backend through setup for each model-testing environment;
the run script does not provide backend switching.

1. **CPU functional coverage:** verify pipeline JSON, config, CLI, path checks,
   UI behavior, both schedules, lossless artifacts, cancellation/recovery, and
   failure handling. A deterministic reference operation can expose prepare/run/
   release events and injected errors to exercise orchestration meaningfully.
2. **CPU/AMD model coverage:** run selected real models on CPU and, where compatible,
   DirectML. Check masks, visual quality, precision, artifact round trips, numerical
   tolerance, stage resource lifecycle, and batch stability. Mock stages and a
   provider appearing in a runtime list are insufficient evidence of real GPU
   inference. Record actual provider use and CPU partitions.
3. **NVIDIA acceptance:** on an authorized local NVIDIA machine, run the same
   contracts and actual models on CUDA, including a 500-photo batch on the minimum
   6 GB GPU. Measure peak memory, stage release, timings, and recovery. Do not
   extrapolate these results from AMD timings or an artificial memory setting.

Until level 3 is available, complete portable development work and mark CUDA
acceptance as pending.

On this machine, ordinary application development uses the prepared DirectML
environment. Setup testing and backend changes are reserved for tasks that improve
or fix setup scripts (user instruction, 2026-11-17). Do not rerun preparation or
switch backends as a routine application check; headless core tests can run in
the existing DirectML environment without invoking neural inference.

## Agent responsibilities

These are responsibilities, not a requirement to run multiple agents. One agent
can perform them sequentially. Spawn additional agents only when the user
explicitly requests delegation; no standing delegation authorization is implied.

- **Coordinator:** maintains requirements, decisions, milestones, and bounded work
  items. Distinguishes confirmed constraints from proposals and benchmark results.
- **Implementer:** inspects relevant code, changes one coherent area, and supplies
  verification evidence against the task's acceptance criteria.
- **Reviewer:** checks behavior, regressions, failure paths, resource assumptions,
  and whether visual or performance claims have supporting evidence.
- **User:** defines the acceptable appearance of Natural and Beauty. Automated
  metrics assist review but do not replace aesthetic judgment.

## Per-task loop

1. Read repository instructions and relevant requirements; inspect current files
   and user changes before editing.
2. State the task outcome, dependencies, acceptance criteria, and expected files.
3. Implement the smallest complete change within the authorized stage.
4. Run checks appropriate to the behavior. Pipeline/state changes need meaningful
   automated checks; model or color changes also need visual comparisons. For new
   operations or error paths, verify useful warning/error codes, correlated context,
   and cause/traceback capture at normal verbosity.
   Application/service changes also need headless checks; Qt changes need focused
   GUI integration checks. Keep Qt out of shared contracts and worker messages.
   Stage-availability changes need missing-asset disable/error, skip/dependency,
   and recovery checks against both the headless service and Qt card behavior.
5. Review the result against acceptance criteria and update affected requirements
   or decisions when the actual design changes.
6. Report changed behavior, verification, and material limitations. Never describe
   an unrun GPU benchmark or unreviewed image result as validated.

For delegated work, assign independent scopes and clear file ownership, exchange
interface contracts before editing shared components, and integrate/review changes
before treating a milestone as complete. One GPU benchmark should own the GPU at
a time so measurements remain interpretable.

## Decision and progress records

- Keep confirmed product constraints in `docs/requirements.md`.
- Keep the current architecture and stack status in `docs/architecture.md`.
- Keep pipeline serialization, preview meaning, scheduling, and artifact semantics
  in `docs/pipeline.md`. Changes to these contracts need persistence/recovery and
  mode-equivalence checks where relevant.
- Keep the stage catalog, context dependencies/coordinate validity, and EXIF
  preservation/provenance in `docs/stages.md`. New operations must declare pixel
  and context effects and have meaningful targeting/invalidation verification.
- Keep environment preparation, launcher options, configuration resolution, and
  module extension expectations in `docs/runtime.md` and `docs/architecture.md`.
- Keep diagnostic fields, logging failure behavior, retention, and local bug-report
  export in `docs/diagnostics.md`. Bug investigations should start from the report's
  app/pipeline/model/backend versions and correlated failure events; explicitly
  identify any missing evidence or reproduction differences.
- Record consequential model/runtime choices with the options, rationale,
  measured evidence, and relevant distribution constraints in `docs/decisions/`
  when decisions are made.
- Add a task backlog when implementation begins; each task should point to its
  requirement and completion evidence.
- Keep photos, exported portraits, model binaries, large caches, and machine-local
  logs out of source control by default. Document how to provision local assets.

Ask for clarification when an unresolved choice changes scope or acceptable
appearance. Resolve routine reversible implementation choices within the current
authorization. Do not repeatedly request approval for already authorized work.
