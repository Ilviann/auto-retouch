# Product requirements

Auto ReTouch, authored by Neyrome, automates repetitive color correction and skin
retouching for large studio portrait batches. The intended workflow is to configure an ordered
pipeline, inspect previews, select source and destination folders, and let the
computer finish the batch unattended. Natural and Beauty are reusable pipeline
presets rather than fixed processing paths.

## Confirmed user requirements

| ID | Requirement |
| --- | --- |
| U01 | Windows 11 desktop application; Windows 10 support is outside the initial scope. |
| U02 | Photo processing is entirely local; no cloud processing. |
| U03 | Primary workload is studio portraits, approximately 500 photos per batch. |
| U04 | JPEG input; vendor-specific RAW formats are outside the initial scope. |
| U05 | Automatic overall color correction and human skin retouching. |
| U06 | Separate Natural and Beauty presets. |
| U07 | Minimum GPU class is consumer RTX 20-series with at least 6 GB VRAM. |
| U08 | A couple of hours of unattended background work is acceptable. No hard processing-time limit is specified; the computer need not serve other tasks during processing. |
| U09 | An editable ordered list of operations with add, remove, reorder, and separate parameter panels. |
| U10 | Operations can use conventional image processing or local neural-network checkpoints. Auto contrast, red-eye correction, and skin-texture correction are example operations. |
| U11 | Save and load pipeline settings as JSON files. |
| U12 | Automatically restore the last-used pipeline at application startup. |
| U13 | Select a source folder and optionally search for photos recursively. |
| U14 | Require a destination folder before batch processing and reject a destination equal to the source folder. |
| U15 | When recursive search is enabled, also reject a destination inside the source folder. |
| U16 | Display discovered source files in a thumbnail grid and allow preview of any image. |
| U17 | Select one thumbnail for preview. Preview the entire pipeline, or use a stage card's **Preview** or **Preview in pipeline** action to compare the selected stage's input and output side by side in a modal window. The input is the source for **Preview** and the result of preceding enabled stages for **Preview in pipeline**. Edit that stage's parameters in the window, with automatic refresh or **Preview changes** as appropriate. Ask whether to save changed parameters when the window closes. |
| U18 | Support a temporary folder for processing artifacts. |
| U19 | Sequential mode processes one image through every stage, exports it, then moves to the next image. |
| U20 | Staged mode processes every image through one stage, persists intermediate results, releases that stage's model resources, then advances to the next stage. |
| U21 | Implement the application in Python 3.14; assume Python 3.14 is already installed and available system-wide. |
| U22 | Use a Python virtual environment containing all required application packages. |
| U23 | Decompose the application into modules so new pipeline operations are easy to implement and add. |
| U24 | Provide a root-folder CMD script that checks or prepares the virtual environment. |
| U25 | Provide a root-folder CMD script that runs the application with its virtual environment, with options to specify photo sources and load a specific pipeline instead of the last-used pipeline. |
| U26 | Application configuration specifies the AI checkpoint folder and temporary-artifact folder. |
| U27 | Only setup asks for devices. It prompts independently for ONNX Runtime (CUDA, DirectML, CPU) and PyTorch (CUDA, CPU). CUDA is the default at each prompt. |
| U28 | Setup installs or updates the selected build of each runtime and their dependencies in the Python 3.14 virtual environment. |
| U29 | Launch uses the two devices recorded by setup. The development machine uses ONNX Runtime DirectML and PyTorch CPU. |
| U30 | Provide detailed logging, especially useful warning/error feedback, to support tool improvement and bug investigation. |
| U31 | Use PySide6 with Qt Widgets for the initial desktop GUI. |
| U32 | Keep the GUI separate behind an application boundary so another toolkit can replace it if problems arise. |
| U33 | Stages return image state: they may change pixels, add/update contextual analysis data, or do both. Later operations can use optional context such as face/eye/teeth/person regions. |
| U34 | Transfer source EXIF metadata, including camera and capture information, to the final image, and add information identifying processing by this tool. |
| U35 | App name: Auto ReTouch (exact capitalization). |
| U36 | Every editable pipeline stage can be skipped by turning it off. |
| U37 | A stage missing a required AI model file must be turned off and show an error status on its own UI card. |
| U38 | Ask the user to download required AI model files and place them in the configured model folder (`models/` by default). Provide the model version, publisher link, required filenames and destination paths. |
| U39 | Retouch all exposed human skin, including multiple people in one photo. Face/neck-only coverage is insufficient for the first version. |
| U40 | Provide export settings. Initial export support is JPEG with compression controls. |
| U41 | Plan WebP, JPEG XL, and AVIF export support for later versions. |
| U42 | The tool is intended for paid photography work; selected model/checkpoint licenses must permit that commercial use. |
| U43 | Minimum system RAM is 16 GB. |
| U44 | After source indexing, reserve temporary space of at least the largest uncompressed source image plus 200 MB in sequential mode, or all source images uncompressed plus 200 MB in staged mode. |
| U45 | Reserve output free space estimated as twice the total compressed size of the selected source files. |
| U46 | Development hardware is an AMD Ryzen 7 7840HS APU with Radeon 780M Graphics and reported DirectX 12 Ultimate support; NVIDIA CUDA hardware is unavailable here. |
| U47 | App author: Neyrome. |
| U48 | Provide an in-app UI for editing application configuration. |
| U49 | Give batch photo retouching higher implementation priority than logging and application maintenance. |
| U50 | Previews run without a destination folder. Keep preview images in memory and discard them when the preview window closes. |
| U51 | The Alibaba skin-retouching stage accepts the incoming image or one crop per face when an upstream stage supplies face rectangles or masks. Its input limit is 5000 pixels on the longest side. Warn before reducing an oversized input. If the user continues, preserve aspect ratio, reduce its longest side to 5000 pixels, process it, and scale the result back to its exact input dimensions. |
| U52 | Application settings provide independent methods for downscaling and upscaling. Every stage that scales images can override either method on its own stage card. |
| U53 | Each stage card shows the algorithm name, implementation version, enable checkbox, tagged In and Out rows, stage-specific controls, both stage previews, and move, remove and settings actions. Required and optional inputs are visually distinct. The Alibaba settings menu opens the publisher page and checks required model files. |
| U54 | Stages that return changed pixels offer a blending slider from 0.0 to 1.0 to mix their input pixels with their result. |
| U55 | Automatic stage preview updates use a fixed-size progress bar that stays visible while idle. Explicit updates use a modal or overlay progress panel. Showing progress keeps the comparison panes and settings in place. |
| U56 | Update batch disk status automatically when the user chooses a source folder, chooses a destination folder, or rescans the source. |
| U57 | **Start processing** opens a modal window showing progress, **Pause**, **Continue**, and **Break**. Ask for confirmation before breaking processing. |

### Feature priorities

1. **Primary workflow.** Import and preview photos, apply automatic color
   correction and retouch all exposed skin across multiple people, tune the
   Natural and Beauty presets, and run recoverable batches with verified JPEG
   export. Validate the appearance and resource use on representative batches.
2. **Lower priority.** Detailed logging, diagnostics tools, and application
   maintenance features. Keep actionable processing errors and basic job status
   available as part of the batch workflow.

The [implementation backlog](backlog.md#implementation-priorities) applies this
order to the remaining work.

The following sections are the proposed MVP baseline, rather than additional
user-confirmed requirements. Typical image dimensions, retouching examples, CPU
requirements, and model compatibility/quality remain to be established. The user
has now provided a local testing folder with seven JPEG study shots (2026-11-17).
Representative coverage and visual acceptance remain unreviewed.

## Proposed functional baseline

| ID | Behavior |
| --- | --- |
| F01 | Index JPEG files from the selected source folder using the recursion setting; show a thumbnail grid, file count, and original-image preview. |
| F02 | Supply editable Natural and Beauty pipeline presets. Stage parameters, including color and skin intensity where applicable, belong to the individual operations. |
| F03 | Correct exposure, white balance, tonal balance, and unwanted color casts conservatively. Allow color correction to be disabled for intentional lighting or grading. |
| F04 | Reduce temporary blemishes and uneven skin tone, with adjustable smoothing and protection of skin detail. Exact operations depend on the engine evaluation. |
| F05 | Provide before/after previews for the whole pipeline and the stage comparisons in U17. Full-resolution detail inspection is proposed. |
| F06 | Allow per-image settings and selected-image reprocessing. |
| F07 | Reuse a common preset for a studio setup while estimating corrections per image. Avoid normalizing distinct natural skin tones to one target tone. |
| F08 | Show queued, running, completed, failed, and canceled work, with progress, estimated remaining time, and actionable errors. Review flags are separate from execution status. |
| F09 | Pause after the current image-stage unit, resume, cancel remaining work, and recover interrupted jobs in either scheduling mode. |
| F10 | Continue past an isolated corrupt image or processing failure and provide a batch summary. |
| F11 | Initial export settings provide JPEG compression/quality controls, original pixel dimensions by default, destination, and names. Preserve originals and handle output-name collisions explicitly. Keep export adapters separate for later WebP, JPEG XL, and AVIF support. |
| F12 | Preserve source capture EXIF in exports and add tool/version processing provenance. Normalize orientation, dimensions, and profile-related fields to match the actual output; keep original capture time and author/copyright information. |
| F13 | Every editable stage has an on/off control. Turning it off preserves its position and parameters; allow multiple instances of the same operation with distinct parameters. |
| F14 | Validate pipeline structure/parameters separately from asset availability. Load structurally valid pipelines with missing checkpoints, turn affected stages off, and show their errors. Unknown schema/operations and invalid parameters remain explicit import errors; enabled stages with unresolved required context block processing. |
| F15 | Preserve relative source subdirectories in destination by default, preventing same-name inputs from different folders from colliding. Existing-output policy is explicit; no silent overwrite. |
| F16 | Snapshot inputs and pipeline settings at batch start. Editing the pipeline affects previews and future jobs, not the running job. |
| F17 | In staged mode, use lossless intermediate data with defined precision and color metadata, retaining required masks and other artifacts. Encode the final JPEG only at export. |
| F18 | Let the user configure the temporary root; create a dedicated job subdirectory, check free space, and provide safe cleanup of application-owned artifacts. Preserve required artifacts for resumable jobs. |
| F19 | Proposed script names are `prepare_env.cmd` and `run.cmd`. Preparation creates or verifies a project-local `.venv` using Python 3.14 and installs reproducible application dependencies. A check-only mode does not create or modify the environment. |
| F20 | The launcher invokes `.venv\Scripts\python.exe` explicitly. It forwards repeatable `--source` paths and optional `--pipeline` and `--config` paths. It opens the application without automatically starting photo processing. |
| F21 | Keep machine-local configuration separate from portable pipeline JSON. Resolve relative checkpoint/temporary paths against the configuration file's directory, and validate the effective folders before processing. |
| F22 | Setup records both selected backends in environment-owned metadata after both pass execution checks. Switching removes conflicting runtime builds. |
| F23 | Each neural operation declares its inference runtime, supported devices and model formats. Launch uses the two recorded devices. Missing metadata, unavailable hardware, or incompatible stages produce actionable errors. Backend selection is setup-only and automatic fallback is disabled. |
| F24 | Persist structured logs with readable console/UI messages and correlated session/job/image/stage context. Include actionable warnings, exception tracebacks, retry outcomes, effective pipeline/model/backend versions, and stage timings. |
| F25 | Provide searchable/filterable diagnostics, links to affected images/stages, copyable error details, and per-batch warning/error summaries. Tracebacks are available at normal logging verbosity. |
| F26 | Log setup, launch, worker, and native-runtime failures where observable. Support configurable verbosity and bounded log rotation/retention outside processing scratch; report logging failures explicitly. |
| F27 | Export a local diagnostics report containing relevant logs, configuration/pipeline and runtime details with consistent path redaction. Exclude photos and model weights by default; no automatic upload. |
| F28 | Expose toolkit-independent application commands, state snapshots, results, and events. GUI adapters call these contracts; pipeline, operations, jobs, model backends, configuration, storage, and diagnostics do not depend on Qt. |
| F29 | Confine PySide6 widgets, Qt item models, signals, image-display objects, and UI-thread dispatch to the Qt adapter. A future toolkit switch replaces that adapter and startup wiring while retaining application and processing services. |
| F30 | Represent analysis as typed, versioned context with producer/confidence/coordinate information. Operations declare required, optional, provided, preserved, and invalidated context. Reject missing/stale required targeting data rather than making global edits. |
| F31 | Context-only stages preserve pixel data and offer overlay/measurement previews. Persist context and source metadata in staged artifacts and include dependency versions in cache validation. |
| F32 | Keep EXIF preservation and final processing markers in mandatory export infrastructure. Validate required metadata after writing; report metadata loss/marker failure as export failure. |
| F33 | Distinguish manually skipped stages, ready stages, and unavailable/error stages. Missing-model cards show the required files, expected local locations, and a recovery action with text/icon feedback. Keep parameters editable and prevent activation until all required assets are valid. |
| F34 | Recheck assets after pipeline/configuration changes, on explicit recheck, and before execution. When restored, clear the asset error but leave an automatically disabled stage off until the user enables it. Log the transition and revalidate downstream context dependencies. |
| F35 | The stage card shows the required files and paths and provides Open model page using the publisher URL. The user downloads and places the files, then selects Recheck to validate them locally. |
| F36 | Estimate scratch/output reservations after indexing and whenever the mode, folders, input list, or relevant processing settings change. Show the assumptions and required/available space per storage volume; prevent start when the required reservation cannot be met. |
| F37 | Provide a settings editor for machine-local configuration, including checkpoint, temporary-artifact, and log folders plus logging options. Show effective values and the active configuration file; validate through shared application services, save atomically, and show actionable validation/write errors. Cancel discards unsaved edits. Keep backend selection setup-only and preserve active job snapshots. |

## Disk space budgeting

For the indexed, selected inputs, define `S` as their summed compressed file sizes,
`Umax` as the largest uncompressed working-image size, and `Usum` as the summed
uncompressed working-image sizes. The confirmed minimum reservations are:

| Reservation | Minimum free space |
| --- | --- |
| Sequential temporary storage | `Umax + 200 MB` |
| Staged temporary storage | `Usum + 200 MB` |
| Output storage | `2 * S` |

Compute uncompressed bytes from image dimensions, channels, and the actual working/
intermediate sample precision, without assuming JPEG file size equals decoded
size. Inspect metadata/index entries incrementally; do not decode and retain the
whole batch to estimate it. Identify unreadable inputs and incomplete estimates
explicitly rather than treating unknown sizes as zero.

The formulas are baseline minimums, not a guarantee that arbitrary stages fit
within them. The proposed implementation adds known mask/context artifact storage,
file-format overhead, and transactional-write peak space when needed, exposing
each addition in the estimate. In particular, writing a new staged image before
reclaiming its predecessor can need an additional image-sized allowance. Prefer
rolling reclamation to retaining two complete batch generations.

If temporary and destination folders share a volume, sum their reservations before
comparing with that volume's free space. For separate volumes, check each one.
Existing source files already consume disk space and are not reserved a second
time. Resume estimates account for valid job artifacts already present.

`2 * S` is the agreed output estimate, not an enforced file-size ceiling. Monitor
free space during processing and preserve recoverable state on exhaustion; never
silently reduce JPEG quality or image resolution to fit the estimate. Checkpoint
provisioning and any other known storage needs are accounted for separately.

The [Runtime and launch contract](runtime.md) defines proposed script behavior,
launch options, configuration format, and dependency validation. These contracts
now include implemented runtime scripts and startup modules; processing contracts
remain partly planned. Pipeline/reference contracts, headless JPEG decode/export,
source indexing, disk preflight and both batch schedules are implemented. Both
schedules run through a toolkit-independent worker service. The Qt desktop
workspace supports source discovery, thumbnails, pipeline editing, fit-to-window
previews, batch controls, and saved-job monitoring. See
[desktop workspace evidence](verification/desktop-workspace.md),
[runtime evidence](verification/runtime-foundation.md),
[pipeline evidence](verification/pipeline-contracts.md),
[JPEG evidence](verification/jpeg-boundary.md) and
[batch preflight evidence](verification/batch-preflight.md),
[sequential job evidence](verification/sequential-jobs.md),
[staged job evidence](verification/staged-jobs.md), and
[worker evidence](verification/worker-transport.md).
The [Logging and diagnostics contract](diagnostics.md) defines event context,
failure presentation, retention, and local bug-report evidence.
The [Initial stage catalog](stages.md) proposes image analysis, global correction,
portrait detection/parsing, and targeted retouching operations with metadata rules.

## Pipeline and folder behavior

The first editor is an ordered list with parameter panels. JSON portability is
required; graph branching and ComfyUI file-format compatibility are not part of
the initial specification. Pipeline settings and preview behavior are detailed in
[Pipeline contract](pipeline.md).

Validate the source, recursion setting, destination, temporary directory, and
planned output paths before batch processing. Selecting another source folder or
enabling recursion immediately revalidates the batch destination. Preview validates
the indexed source and the stages it runs. It uses neither the batch destination
nor the temporary directory.

Compare resolved filesystem locations rather than raw path strings. On Windows,
account for equivalent case, separators, trailing separators, and junction or
symbolic-link aliases; use directory identity where needed. Descendant checks
must compare directory boundaries, so `C:\photos2` is not inside `C:\photos`.
Reject paths whose required relationship cannot be established safely.

A destination inside the source is allowed when recursion is off, subject to
normal writeability and output-collision checks. Freeze the discovered input list
before processing and never overwrite any original input. Do not follow directory
junctions or symbolic links during recursive search by default; this avoids loops
and traversal into unintended directories.

The example operation catalog is not a claim that particular implementations or
checkpoint packages are already selected. Each operation must pass its own
quality and resource validation.

## Preset intent

**Natural** uses restrained cleanup and tone evening, preserving visible texture,
freckles, moles, facial hair, makeup, facial structure, and the subject's appearance.

**Beauty** permits stronger smoothing and tone evening while retaining believable
detail and the subject's appearance. It does not imply face or body reshaping.

Both presets expose intensity controls. Do not promise that a model can always
distinguish a temporary blemish from a permanent feature. Evaluate that distinction
on representative examples, favor conservative changes when uncertain, and allow
the user to inspect or bypass the result.

Coverage includes visible face, neck, arms, hands, legs, and other exposed skin
across multiple people. Skin-region analysis must exclude clothing/background and
preserve per-person associations and natural tone differences. Models covering
only faces cannot by themselves satisfy this scope.

## Reliability and resource requirements

- Process the reference batch without retaining all decoded images in RAM or VRAM.
- Validate bounded CPU image buffers, caches, queued results, and mask artifacts
  against the 16 GB system RAM target. Disk reservations do not establish RAM or
  VRAM sufficiency; measure those independently.
- Operate within the actual available memory of the minimum GPU, including memory
  used by Windows and the display. Validate supported image sizes experimentally.
- Never silently reduce exported resolution to work around memory limits.
- Keep the interface responsive while work runs in a separate processing process.
- Save job state, settings, and model versions locally so work can be resumed.
- Journal progress per image and stage. Completed staged results must remain
  recoverable after interruption without restarting every preceding stage.
- Sequential and staged execution implement the same ordered pipeline. Their
  output must agree within a documented numerical tolerance for the same inputs,
  parameters, checkpoints, precision, and seed where relevant.
- Stage-major execution must not keep all neural checkpoints resident. Validate
  resource release at stage boundaries on the minimum GPU.
- Develop and verify core UI, JSON, path rules, scheduling, artifacts, and recovery
  on CPU without requiring CUDA. Use compatible real models for AMD GPU integration
  tests; synthetic reference stages verify orchestration, not retouching quality.
- AMD/CPU validation does not satisfy NVIDIA minimum-hardware acceptance. CUDA
  compatibility, 6 GB memory behavior, model release, full-batch stability, and
  performance remain unverified until exercised on actual target hardware.
- Mark work complete only after the exported file has been validated and finalized.
- Run imports, previews, processing, review, exports, and recovery without internet
  access once the application and model assets are installed.
- Prepare the project-local environment against the user's installed Python 3.14.
  Do not bundle or install another Python interpreter or silently select another
  Python minor version to accommodate a dependency.
- Validate all selected packages and neural runtimes on Windows with Python 3.14
  before committing to them. Record model checksums and redistribution terms;
  provision checkpoint assets separately in the configured checkpoint folder.
- Establish commercial-use rights for the actual selected checkpoints and their
  dependencies before adopting them for paid photography. A code repository's
  license alone does not establish the pretrained weights' usage terms.

## Acceptance evidence

1. **Visual quality:** compare Natural and Beauty on an agreed local evaluation
   set, including different skin tones, ages, portrait distances, lighting,
   makeup, facial hair, and visible skin features. Record user judgments at whole
   image and 100 percent detail scales; establish the required acceptance rate
   before choosing the production engine.
2. **Batch operation:** exercise both sequential and staged modes with
   approximately 500 representative JPEGs on the minimum hardware. Record
   dimensions, GPU, CPU, RAM, driver/runtime/model
   versions, total duration, per-image latency distribution, peak RAM/VRAM,
   failures, and review rate. A couple of hours is an acceptable reference,
   not a pass/fail deadline.
3. **Recovery:** interrupt a batch and restart. Preserve completed outputs and
   retry interrupted work without silently duplicating or overwriting files.
4. **Failure handling:** include corrupt inputs, unavailable output destinations,
   insufficient disk space, and GPU memory exhaustion. No input is silently lost
   from the final job accounting.
5. **Offline operation:** validate the installed application with networking
   unavailable through a complete import-to-export workflow.
6. **Image integrity:** verify originals are unchanged, output files decode,
   dimensions and orientation are correct, and color profiles are handled as
   configured.
7. **Pipeline persistence:** round-trip pipelines with reordered, disabled, and
   repeated stages through JSON without changing settings. Verify last-used
   restoration and clear errors for invalid JSON, unknown versions/operations,
   invalid parameters. Missing checkpoints must load into disabled/error stages
   without losing their settings or silently falling back to another pipeline.
8. **Folder validation:** check missing destination, equal locations and aliases,
   recursive descendant rejection, non-recursive descendant allowance, sibling
   directories with matching name prefixes, source/recursion changes, and duplicate
   input filenames in different subfolders.
9. **Previews:** verify whole-pipeline and stage previews use the configured
   operation implementations. Check each stage comparison's baseline, modal
   settings edits, refresh behavior, save/discard, and rejection of outdated results.
10. **Staged artifacts:** verify intermediate round-trip precision and metadata,
    mode equivalence, model release, per-stage recovery, low-disk handling, and
    cleanup limited to the job's own files.
11. **Environment and launcher:** exercise preparation, repeated preparation,
    check-only mode, missing/wrong Python, invalid existing environment, dependency
    failures, quoted paths with spaces/non-ASCII characters, and invocation from
    another working directory. Verify the application and workers use Python 3.14
    from `.venv`, with errors returned as nonzero exit codes.
12. **Launch settings and configuration:** check explicit-pipeline precedence over
    last-used state, repeatable source paths, unavailable inputs, relative-path
    resolution, invalid configuration, and configured checkpoint/temporary folders.
    Explicit invalid options must fail clearly without silently loading another
    pipeline or starting a job.
13. **Setup backends:** verify the two interactive menus and their CUDA defaults,
    repeated preparation, backend switching without conflicting packages, ready
    metadata, failed-update handling, and check-only validation without prompting.
    Verify launch uses the recorded device for each runtime, reports unavailable hardware
    or incompatible stages, and cannot override it through CLI/config or silently
    switch backends. Compare CPU/DirectML/CUDA model outputs in separately prepared
    setups on the same inputs where available, with documented tolerances and
    cache invalidation across providers/precision changes.
14. **Diagnostics:** verify warning/error context, exception capture at INFO,
    multi-process correlation, retry/failure summaries, setup/startup logging,
    worker termination, rotation, and logging-storage failures. Export a local
    report and check useful failure evidence, path redaction, and exclusion of
    photo/model content. Detailed cases are in `docs/diagnostics.md`.
15. **GUI separation:** import and exercise application/processing services without
    importing PySide6 or creating a GUI event loop. Verify command/event flows
    with a headless consumer, enforce import boundaries, and separately smoke-test
    the Qt adapter's display, user actions, progress, and error presentation.
16. **Context and metadata:** verify unchanged pixels in context-only stages,
    region coordinates/masks across orientation and scale, optional/required/stale
    context behavior, and context persistence in both modes. Verify EXIF capture
    values and author/copyright survive export while output technical tags and
    tool/version provenance are correct. See `docs/stages.md` for detailed cases.
17. **Stage availability UI:** verify manual skipping, missing one of several
    required model files, disabled/error cards on import and last-used restore,
    expected filenames/paths, activation prevention, recheck/recovery without
    auto-enabling, and dependency messages after a producer is disabled. Confirm
    manual model installation and no silent stage omission from an active job.
18. **Export and resource planning:** verify JPEG settings reach the exporter,
    metadata survives, and output size assumptions remain visible. Check disk
    formulas on empty/single/mixed-resolution batches, shared/separate volumes,
    unknown dimensions, pipeline changes, resume, and low-disk transitions. Measure
    CPU memory against 16 GB and include multi-person/full-body skin examples in
    subsequent visual validation.
19. **Configuration editor:** edit and persist machine-local settings in the UI,
    reopen the application, and verify the saved values and configuration-relative
    paths. Check cancel, invalid values, failed writes without losing the last
    valid configuration, checkpoint revalidation, and unchanged active job
    snapshots. Verify the editor cannot change the setup-owned backend.
20. **Product identity:** verify the app name is exactly `Auto ReTouch` and the
    app author is `Neyrome` in application identity and distribution metadata.
    Export software provenance uses the app name/version without replacing the
    source photographer's author/copyright metadata.

## Initial exclusions and open decisions

Proposed exclusions: RAW processing, manual brush editing, face/body reshaping,
background replacement, cloud processing, and training a model from scratch.

Open decisions: representative image dimensions and maximum supported size;
examples of acceptable Natural and Beauty output (photos to be provided later);
CPU requirements; actual working-image precision and
intermediate format; distribution audience; checkpoint commercial-use evidence
and compatibility/quality; later export-codec implementation and metadata support.
These can be resolved during planning and engine evaluation without repeatedly
asking for approval of routine implementation choices.
