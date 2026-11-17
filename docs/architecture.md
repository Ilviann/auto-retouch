# Architecture and implementation status

The architecture prioritizes visual quality, offline operation, recoverable
batches, editable operation pipelines, and a 6 GB GPU memory budget. Sequential
and staged modes are two schedules for the same pipeline. Python 3.14, a
project-local virtual environment, and PySide6 with Qt Widgets for the initial GUI
are fixed requirements. The GUI is a replaceable adapter around toolkit-independent
application services. Other stack choices, models, and exact package/runtime
versions remain subject to validation.
Milestone 1a now supplies setup/launch, strict config and CLI handling, bootstrap
diagnostics, headless startup services, and a minimal Qt Widgets adapter. The
[runtime decision](decisions/0001-runtime-foundation.md) pins the foundation stack;
[verification](verification/runtime-foundation.md) distinguishes real CPU/DirectML
Add execution from pending portrait-model and CUDA acceptance. Milestone 3a adds
immutable pipeline definitions, a registry, local asset readiness, typed image
state, and a prepared-stage executor with CPU contract references. See
[pipeline evidence](verification/pipeline-contracts.md). Other processing modules
below remain proposals until their milestones are implemented. Milestone 3b now
adds lossless float32 TIFF/context storage, checksummed source metadata and safe
manifest-based lifetime management; see [artifact evidence](verification/lossless-artifacts.md).
The JPEG adapter in `imaging/` decodes oriented linear-sRGB states and stages
verified JPEG exports for publication. See [JPEG evidence](verification/jpeg-boundary.md).
Milestone 4 adds headless source indexing, batch preflight and recoverable
sequential and staged execution. See [preflight evidence](verification/batch-preflight.md),
[sequential job evidence](verification/sequential-jobs.md), and
[staged job evidence](verification/staged-jobs.md).
The initial scope covers all exposed skin and multiple people, with a minimum of
16 GB system RAM on Windows 11 only. Initial export is JPEG with compression settings; later exporters
will add WebP, JPEG XL, and AVIF.

## Runtime and candidate stack

| Component | Proposal | Decision condition |
| --- | --- | --- |
| Application and pipeline | Python 3.14 with project-local `.venv`, confirmed | Use the user's existing system-wide Python 3.14 installation. |
| Desktop interface | PySide6 with Qt Widgets, confirmed | Keep Qt inside a replaceable GUI adapter; validate the pinned version, preview behavior, and distribution terms. |
| Inference | CPU reference backend, DirectML candidate for AMD development, CUDA production; use ONNX Runtime or native model adapters as compatible | Python 3.14, model format/operator compatibility, visual equivalence, memory use, and redistribution. |
| Job storage | Python standard-library SQLite | Per-image progress and verified JPEG receipts in both modes, with a lossless checkpoint cursor in staged mode. |
| JPEG decoding and color | Pillow 13.0.0 and LittleCMS, implemented | Synthetic color/orientation checks and seven local JPEG round trips; broader camera and gamut coverage pending. |
| Verification | Automated pipeline/recovery checks and a human-reviewed image set | Functional correctness plus visual evidence. |

Select and pin dependencies, CUDA libraries, cuDNN, and model runtimes compatible
with Python 3.14 on Windows. Test one compatible package set on the minimum GPU
and a clean environment. Do not downgrade Python to make a candidate work. ONNX
export is optional if it compromises quality or compatibility.

Delivery uses a source/application folder and root CMD scripts around a local
virtual environment. Recreate that environment on each target machine rather than
distributing a copied `.venv`. The [Runtime and launch contract](runtime.md)
specifies preparation, launch options, and machine-local configuration.

## Proposed module layout

```text
prepare_env.cmd
run.cmd
pyproject.toml
requirements/
    cpu.lock
    directml.lock
    cuda.lock
config.example.json
src/retouching_tool/
    __main__.py
    bootstrap.py
    cli.py
    config.py
    application/
        service.py
        contracts.py
        batch.py
        job_service.py
        events.py
    diagnostics/
        events.py
        context.py
        service.py
        reports.py
    ui/
        qt/
            main_window.py
            models.py
            image_viewer.py
            parameter_editor.py
            dialogs.py
    pipeline/
        definition.py
        serialization.py
        validation.py
        registry.py
        executor.py
        image_state.py
        context.py
    operations/
        base.py
        auto_contrast.py
        red_eye.py
        skin_texture.py
        portrait_detection.py
        face_regions.py
    models/
        catalog.py
        manager.py
        backends/
    jobs/
        scheduler.py
        sequential.py
        staged.py
        repository.py
    imaging/
        exif.py
        iptc.py
        jpeg.py
        xmp.py
    storage/
    previews/
tests/
```

This is a proposed structure, not a complete existing implementation or a commitment to
the example operation algorithms. Keep CLI/configuration, UI, pipeline contracts,
operations, model backends, schedulers, image handling, and artifact storage
separate. Operation modules depend on shared contracts and model services, not UI
widgets or either batch scheduler. Both schedulers and previews use one common
operation executor.

Implemented 3a files use `pipeline/definition.py`, `registry.py`, `assets.py`,
`validation.py`, `image_state.py`, and `executor.py`, with lifecycle protocol in
`operations/base.py` and CPU references in `operations/reference.py`.
`application/pipeline_service.py` owns transactional edits and readiness; the Qt
shell renders `stage_card.py`. NumPy is imported only when processing arrays,
and no inference runtime is imported by these services. The initial parameter
schema supports finite numeric ranges; extend it when an operation needs other
types. No neural operation or production model manifest is bundled.

Adding an operation consists of implementing its descriptor, parameter schema,
and processing lifecycle in a dedicated module, registering it, and providing
focused verification. Generate ordinary parameter controls from the schema;
support specialized controls through a UI adapter only when needed. Do not require
editing scheduler logic, JSON serialization, or the main window for every new
operation. Defer heavy neural-runtime imports/model loads until needed.
The [Initial stage catalog](stages.md) specifies contextual analysis and targeted
retouching proposals. The module names above remain illustrative, not an exhaustive
or validated operation list.

## GUI boundary

Dependency direction is `Qt adapter -> application services -> processing/storage
services`. The startup composition root wires implementations together. Keep Qt
imports out of application contracts, pipeline definitions, operations, schedulers,
model adapters, configuration, image processing, storage, and diagnostic events.
These modules must import and run without a QApplication or Qt event loop.

The application layer exposes typed plain-Python commands and results for source
indexing, folder validation, configuration editing/validation/save, pipeline
edits/load/save, previews, batch start/pause/cancel/resume, history, and diagnostics.
Use dataclasses and small protocols where
they clarify contracts; do not build a generic abstraction for every widget.
Validate actions in application services so rules are consistent for any GUI.
Stage snapshots include enabled state, asset availability, missing/invalid asset
details, dependency errors, and recovery links/actions. Shared application/model
services compute these states; the Qt adapter renders the toggle/error card and
invokes recheck or explicit model-page navigation. Keep filesystem/model checks
outside widget logic so another GUI observes the same eligibility rules.

Return immutable state snapshots and structured events for discovered images,
pipeline revisions, preview readiness, job progress, warnings, and failures.
Carry stable IDs and request/revision IDs so a stale asynchronous result cannot
replace newer state. Diagnostics and validation return codes/data rather than
displaying dialogs from processing code.

The Qt adapter owns widgets, item models, delegates, Qt signals, dialogs, and
UI-thread dispatch. It maps application state/events into Qt models and user
actions into application commands. Toolkit-specific presenters are allowed in
this adapter; shared validation and orchestration stay in application services.
Communicate with workers through toolkit-independent messages, not Qt objects.

Image services return image data or managed artifact references with dimensions,
color representation/profile metadata, and preview revision information. Convert
to QImage/QPixmap only in the Qt adapter with explicit ownership/lifetime rules.
No Qt display object enters an operation, artifact manifest, or worker message.
Keep color correction/profile decisions in reusable image services so replacing
the viewer does not redefine processing output.

Operation parameter schemas use plain types, ranges, choices, and labels. Qt
generates controls from those schemas. An operation-specific custom editor lives
in the Qt adapter and never introduces Qt imports into the operation itself.

Import the Qt adapter at the GUI composition boundary, after bootstrap logging and
CLI parsing. CLI help and headless application tests do not initialize Qt. Keep
thumbnail decoding and inference off the UI thread; the adapter delivers completed
results on the GUI thread.

Implement only the PySide6 adapter initially. A future switch requires replacing
views, display conversion, toolkit-specific event dispatch, startup wiring, and
the GUI dependency. Preserve pipeline JSON, jobs, model management, logging, and
application behavior. This boundary localizes the rewrite; it does not imply that
another toolkit can reuse Qt widgets or that no GUI work will be needed.

Validate separation with import-boundary checks and headless command/event tests;
verify Qt integration independently. Avoid a second production GUI solely to
demonstrate the abstraction.

## Processing components

1. **Qt GUI adapter:** application configuration editor, source/destination/temporary
   folder controls, recursion toggle, thumbnail grid, ordered stage list, parameter
   panels, preview controls, and job
   progress. It calls toolkit-independent application services and does not execute
   inference on the UI thread.
2. **Pipeline definition and registry:** versioned JSON settings plus operation
   descriptors, parameter schemas, model dependencies, and implementation versions.
3. **Scheduler:** validates paths, snapshots inputs/configuration, persists
   image-stage state, and runs sequential or staged schedules through a worker.
4. **Worker and model manager:** prepare, execute, and release operations; manage
   CPU/GPU resources and cancellation boundaries independently of the interface.
5. **Artifact store:** persists lossless image states and necessary auxiliary
   artifacts with metadata and validated manifests for recovery and cache reuse.
6. **Local storage:** job database, last-used pipeline, JSON presets, model
   manifests, thumbnail/preview caches, output files, and diagnostic logs. Use
   bounded cache retention.
7. **Diagnostics:** shared structured events, centralized multi-process log
   writing/rotation, readable console/UI output, warning/error summaries, and local
   report export. Establish bootstrap logging before app configuration or GUI
   initialization. See the [Diagnostics contract](diagnostics.md).

Each job snapshots its pipeline, execution mode, and engine configuration. Each
item records its input identity, settings, model identifiers, output destination,
execution status, review flags, attempt count, timing, and errors. Settings changes
create a new processing revision rather than silently reusing an incompatible
output. Proposed per-image overrides change operation parameters, retaining the
stage order and checkpoint identities for that batch. Different checkpoint or
stage configurations belong in separate jobs so a staged pass can reuse its models.

Record the effective backend/device and numerical precision in each job and cache
identity. A result tested on AMD or CPU does not establish CUDA correctness or
resource use. Keep backend/device handling inside model adapters and services so
UI, pipeline JSON, and schedulers can be tested without NVIDIA hardware. Runtime
package sets and setup-only selection rules are defined in the
[Runtime contract](runtime.md). Setup asks interactively with CUDA as default,
installs only the selected backend support plus shared dependencies, and records
its identity in `.venv`. Launch uses that recorded backend without a selection
flag, config override, autodetection, or automatic backend fallback.

## Pipeline and operation contracts

The user controls stage ordering; there is no hard-coded color-then-skin path.
Natural and Beauty are default pipeline definitions. Decode, EXIF orientation,
working-color normalization, and final encoding are infrastructure around the
editable operation sequence.

An operation descriptor defines a stable type/version, display name, parameter
schema, checkpoint requirements, compatible inference backends, accepted image
representation, and artifact dependencies. The worker calls a lifecycle equivalent to `prepare`, `process`,
and `release`. CPU-only operations use the same contract without model allocation.
The execution layer supplies logging context for each operation instance and
attempt. Operations emit meaningful codes/messages and propagate technical causes;
the shared diagnostics service handles persistence, presentation, and throttling.

Stage output is an image state containing pixels or their artifact reference,
immutable source metadata, and typed/versioned context with auxiliary artifacts
such as masks. A stage may change pixels, context, or both. Context-only stages
reuse unchanged pixels. Its color space, precision, shape, coordinate system,
producer, confidence where meaningful, and dependency revisions are explicit.
Operations declare provided/required/optional context and preservation/invalidation
effects. Validate incompatible dependencies after stage reordering; transform or
invalidate artifacts when geometry or relevant image content changes. Never reuse
a stale mask only because its dimensions still match. Full rules and proposed
initial operations are in the [Stage catalog and image state](stages.md).

Import captures original EXIF before normalization. Export transfers capture and
descriptive metadata, updates output-specific technical fields, and records the
product name/version and processing provenance. Keep this infrastructure outside
the removable stage list. The JPEG adapter reads and verifies the temporary
export before publication. Its supported metadata and explicit failure policy
are recorded in the [JPEG decision](decisions/0003-jpeg-boundary.md).

Initially, operations act on one image independently of other batch items. They
must not learn or update state according to traversal order. Where randomness is
used, derive reproducible seeds per input and stage. Future cross-image fitting
would need an explicit preparation contract and separate acceptance criteria.

Model-specific analysis and blending are subject to evaluation. Keep eyes, lips,
hair, clothing, and background outside skin operations where appropriate. Do not
substitute general face restoration for validated retouching.

The [Pipeline contract](pipeline.md) specifies JSON, previews, scheduling, and
artifact semantics in more detail.

## Scheduling modes

**Sequential:** decode one source image, run its enabled stages in order, export,
then advance to the next image. Intermediate states normally stay in bounded RAM;
durable image-stage checkpoints may be used when justified. On interruption, the
current image may restart if no durable checkpoint exists. Keep only model
resources that fit the memory budget; loading/unloading between stages can recur
for every image.

**Staged:** prepare the first enabled stage once, process every eligible input and
persist each intermediate state, then release the stage. Prepare the next stage
and consume the preceding committed states. A final pass reads the last staged
results and exports them to the destination after the stage releases. Keep one
stage's model resources resident at a time. Future internal tiling retries
reuse the prepared stage; a worker crash may require loading it again.

Journal commits per `(job, input, stage)`, not just per batch. Failed items do not
advance with stale or original content disguised as a successful stage result.
Continue unaffected items. Retry failed items through the dependencies they still
need; a failed item must not prevent every other item from reaching the next stage.

Pause/cancel boundaries are individual image-stage units. Preserve dependencies
needed to resume; reclaim earlier states only after downstream results are
committed. Bounded rolling storage can keep predecessor/current stage frontiers
instead of every historical intermediate.

Both headless schedules run through `ApplicationService.job_service`.
It stores one versioned SQLite journal per job under `.state/jobs`, with a workspace
runner lock. It freezes source hashes, pipeline, compression, backend/package
versions and checkpoint manifests before processing. Each sequential image is a
restart unit. The runner prepares and releases one stage at a time, checks free
space before starting the next image, and records a receipt after JPEG verification
and before the no-overwrite rename. Recovery validates a published or pending
file against that receipt. Staged jobs journal a checked TIFF/context reference
and the next stage cursor after each image-stage unit. Each image has a dedicated
checkpoint store, so a committed successor allows immediate reclamation of its
predecessor's unreferenced files. An unavailable checkpoint can replay the
affected image from its frozen source. Pause/cancel requests take effect between
images in sequential mode and between image-stage or export units in staged mode.

## Previews and configuration persistence

The desktop UI must provide application configuration editing. The proposed
settings editor displays the active configuration file and effective folder/logging
values, with validation and atomic persistence in shared application services.
Keep machine-local settings separate from portable pipeline parameters and retain
setup-only backend selection. See the [Runtime contract](runtime.md#machine-local-configuration).

Previews must use the same settings and compatible image/color processing as final
exports. Provide full-resolution detail crops where a downscaled preview would
hide texture or blending artifacts.

Support whole-pipeline and standalone-operation previews; cumulative preview
through a stage is an additional proposed convenience. Run previews using the
same operation registry and worker infrastructure. Serialize GPU preview work
with batch inference instead of loading another neural stack alongside it.
By default, defer GPU preview requests until an image boundary in sequential mode
or a stage-pass boundary in staged mode. Indicate that the request is queued;
do not interrupt an active staged pass by repeatedly swapping its checkpoints.
Selection/settings changes invalidate obsolete preview requests and cached
downstream results. Editing a pipeline never mutates an active job's snapshot.

Persist explicit JSON saves and the separate last-used JSON atomically. Restore
the last valid pipeline at startup without automatically starting a job. Keep
machine-local folder/session state outside portable pipeline settings. Validate
schema versions, operations, parameters, and local model availability. Structural
errors do not silently select another pipeline. A structurally valid pipeline with
missing model files opens with those stages off and their card errors visible.
Revalidate required context dependencies before running the remaining enabled
sequence. The [Pipeline contract](pipeline.md) defines recovery and job snapshots.

## Folder and artifact validation

`ApplicationService.batch_service` uses `application/batch.py`,
`storage/batch_paths.py`, and the bounded JPEG header reader. It returns an immutable
index and processes a selected subset into an immutable export plan. Folder inputs
supply relative paths in selection order. Explicit files also contribute their
containing directory to path validation. Unreadable JPEG headers remain visible in
the index and require correction or deselection before planning. File IDs, sizes
and modification times detect ordinary edits between indexing and preflight. Job
content hashing and execution-time verification belong to the scheduler.

Use a shared path-validation service for UI feedback and worker preflight.
Resolve directory aliases and compare filesystem locations case-insensitively as
appropriate for the Windows volume. Reject destination equal to source, and reject
a descendant destination when recursion is enabled. Revalidate at job start;
an earlier UI check is not enough if paths changed. Source indexing freezes the
input manifest and skips directory links by default.

Preserve relative input paths under destination. Validate each planned export
against original inputs and output collisions. A writable destination is required
for processing previews as well as batch runs; original viewing only needs source.
Batch preflight requires an existing destination root. It validates nested export
parents and blocks descendant reparse points, case-insensitive planned collisions
and existing output names. Temporary write probes use the existing parent of each
required directory. The execution layer creates export subdirectories and a
missing scratch root as needed.

The temporary root holds dedicated application-owned job directories. Place job
scratch outside the scanned source tree so it cannot become an input. Validate
its relationship to source and destination; do not repurpose a folder containing
user photos as a disposable job directory. Cleanup acts only on owned artifacts.

Choose a lossless intermediate representation that round-trips the working image
precision, range, color metadata, and auxiliary artifacts. Repeated JPEG encoding
is excluded. The reference-state store now uses uncompressed float32 RGB TIFF plus
strict JSON manifests and opaque source EXIF/profile blobs; see the
[storage decision](decisions/0002-lossless-tiff-artifacts.md). It supports current
statistics/measurement context, not dense masks yet. Benchmark disk space and I/O as well as GPU work: even one raw float32
RGB state for a hypothetical 24-megapixel image is about 288 MB, or 144 GB for
500 images before auxiliary artifacts and compression. This is a size calculation,
not an estimate of the user's actual inputs or a measured storage requirement.

The [Requirements disk budgeting section](requirements.md#disk-space-budgeting)
defines reservations: largest uncompressed input plus 200 MB for sequential
scratch, all inputs uncompressed plus 200 MB for staged scratch, and twice the
compressed source total for outputs. Compute against the actual intermediate
representation, adding known masks, file overhead, and transactional peak space
explicitly. Reclaim predecessors per committed image to avoid retaining two full
batch generations. Aggregate reservations when folders share a storage volume.
The initial budget uses 12 bytes per working pixel, the artifact TIFF and manifest
limits, source header bytes as an upper bound on retained metadata, and one
transactional TIFF and manifest replacement. Callers can supply known extra
scratch bytes. Preflight reports required and available space by filesystem
device without allocating the estimated space. The pending JPEG is renamed at
publication and contributes to the output estimate as one file.

Check free space after indexing/configuration changes and before a job; monitor it
while running. Handle exhaustion without corrupting committed states, reducing
quality, or changing settings silently. Record the chosen artifact format only
after precision and mode-equivalence validation, with CPU buffering bounded for
16 GB RAM independently of the scratch disk estimate.

Export settings and codec adapters are independent of pipeline operations and Qt.
Implement JPEG first, passing explicit compression options and retaining the
metadata/provenance contract. Later WebP, JPEG XL, and AVIF adapters must validate
their own encoding options, Python 3.14 packages, metadata capabilities, and output
validation; do not claim those codecs are implemented by a shared interface alone.

## Resource management and recovery

- Begin with one GPU worker and bounded CPU decode/prefetch. Increase concurrency
  only when measurements justify it.
- Keep the current stage's model sessions resident when useful, but measure total
  process and GPU memory rather than relying only on an inference allocator setting.
- Evaluate mixed precision and crop/tile processing against full-precision
  reference results. Verify boundaries, texture, and skin-tone consistency.
- On GPU memory exhaustion, use a bounded retry with smaller validated internal
  work units when supported. Otherwise report the failure and continue. Never
  retry indefinitely or silently select a weaker quality mode.
- Export to a temporary file in the destination directory. Validate and finalize
  it before recording success. On restart, reconcile outputs with persisted item
  revisions so interruption between finalization and database commit is safe.
- Verify actual model-resource release at staged boundaries. If the runtime keeps
  GPU allocations alive, destroy its sessions or recycle the worker before loading
  the next stage, retaining persisted job and artifact state.
- Preparation installs the required packages into `.venv`; launching does not
  silently install or upgrade packages. The worker uses the same environment's
  interpreter. Missing dependencies produce an actionable setup error.
- No mandatory network dependency in any photo workflow. Models are local,
  versioned assets; missing or incompatible assets produce actionable errors.

## Engine selection gate

Evaluate existing methods before training or committing to a model. Compare
visual quality for both presets, facial and skin-detail preservation, mask
accuracy, color consistency, peak memory, unattended stability, install size,
and code/weight redistribution terms.
Commercial-use permission for actual weights and dependencies is required for the
confirmed paid photography use case; unresolved model terms remain an adoption
issue even if inference works technically.

A candidate is selected only after Python 3.14 dependency/import/inference
validation, representative visual review, and an actual minimum-hardware run.
If that hardware is unavailable, label performance and
compatibility as unverified rather than inferring them from another GPU.
AMD/CPU results can qualify development behavior and reference model output in the
meantime; retain the CUDA validation gate for production selection and release.

## Technical references

- [Python 3.14 virtual environments](https://docs.python.org/3.14/library/venv.html)
  documents isolated environments, Windows interpreter paths, and recreating
  environments rather than copying them between machines.
- [ONNX Runtime CUDA provider requirements](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)
  documents CUDA/cuDNN compatibility requirements and provider options.
- [NVIDIA GPU compute capability](https://developer.nvidia.com/cuda/gpus)
  lists RTX 2060, 2070, and 2080 devices under compute capability 7.5. This does
  not establish compatibility or memory use for any particular model package.
