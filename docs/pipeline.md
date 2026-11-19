# Pipeline contract

The application edits an ordered sequence of operation instances. Both scheduling
modes execute that same sequence. Version-1 definitions, registry/lifecycle,
headless editing/persistence, and reference-stage readiness are implemented in
milestone 3a. Other behaviors remain planning contracts; the portrait example's
operation names and parameters are illustrative until selected.
Stages may edit pixels, produce/update context, or do both. The
[Initial stage catalog and image state](stages.md) defines the proposed operations,
context validity/dependencies, and required metadata preservation/provenance.

## Implemented reference boundary (3a)

`examples/pipelines/reference.json` contains registered `analysis.image_statistics`
and `core.exposure`, both operation version 1. Exposure accepts numeric `stops`
from −3 to +3, defaults to zero, scales normalized linear-sRGB float32 samples,
and warns when channel samples clip. It is a manual contract reference, not an
accepted automatic portrait correction. Statistics produces `image.luminance`
version 1 with 256 bins and reuses the input pixel buffer.

The parser rejects unknown fields, duplicate instance IDs, unsupported operation
versions, non-boolean enable flags, unknown parameters, and nonfinite/out-of-range
numbers. Missing numeric parameters receive descriptor defaults. Definitions are
frozen with tuple members; a pipeline preflight returns an independent snapshot.
Imports are bounded to 1 MiB, 64 JSON nesting levels, 256 stages, 200-character
names and 100-character IDs.

The headless service implements add/remove/reorder, enable, numeric parameter
changes, explicit save/load and autosave. Structurally invalid imports and failed
atomic autosaves leave the current editor snapshot and previous file intact.
The Qt Pipeline tab exposes those commands in milestone 5b. It adds operations
from the registry with independent stage IDs and descriptor defaults. Each card
supports moving, removing, toggling and editing the stage. Open and Save exchange
portable JSON files, and the batch setup summary follows the current editor.
See [desktop pipeline evidence](verification/desktop-pipeline.md).

Pipeline preflight checks operations, assets, and context. Batch execution also
requires destination, path, and disk validation.
The application catalog includes `core.auto_exposure` version 1. It is an
independently editable CPU stage with strength and a maximum correction in stops.
Place it before pixel-sensitive analysis stages. See the
[stage catalog](stages.md#initial-stage-catalog-and-image-state) for its tonal
behavior and provisional defaults.
The catalog includes independent person foreground and exposed-skin analysis
operations with local checkpoint manifests. Smoothing can select the versioned
exposed-skin mask as a required input. Editor validation, standalone preview,
and both batch schedulers enforce that choice. The default retains the original
target and frozen-job identity. [Exposed skin verification](verification/exposed-skin.md).

The 3b storage slice persists reference states with lossless float32 TIFF,
strict versioned manifests, typed context and cache identity/checksums. The
headless JPEG adapter initializes oriented image states and exports verified sRGB
JPEGs. XMP and IPTC-IIM join EXIF and ICC as immutable source metadata. Version-1
artifact manifests can carry optional checksummed XMP and IPTC blob references;
earlier manifests still load. Include the decoder/color policy and relevant
dependency versions in scheduler cache identity. Both headless schedules use
the JPEG adapter and the same stage sessions as reference execution. The batch
worker runs the frozen snapshot using either schedule. The desktop keeps that
snapshot for the open progress dialog. Closing the dialog or exiting discards
finished and unfinished batches. On a later run the user can skip existing
outputs. See
[artifact evidence](verification/lossless-artifacts.md),
[JPEG evidence](verification/jpeg-boundary.md),
[sequential job evidence](verification/sequential-jobs.md),
[staged job evidence](verification/staged-jobs.md), and
[worker evidence](verification/worker-transport.md) and
[desktop batch evidence](verification/desktop-batch.md).

## Editor and JSON settings

The editor provides add, remove, reorder, enable/disable, and a parameter panel
for each operation instance. Multiple instances of the same operation are allowed.
The **Preset** picker lists JSON files directly in the configured preset folder.
When presets are available and none is selected, the picker stays blank.
A new working pipeline is titled **Untitled pipeline**. Saving it as a preset
uses the filename stem as its name. Opening or restoring an older preset with
the placeholder name **Empty foundation pipeline** also uses its filename stem.
Choosing a preset or opening a JSON file asks for confirmation when the working
pipeline differs from its last explicit load or save. Cancel keeps the current
pipeline. Recovery saves preserve edits across launches. The editor remembers
the selected preset path separately so those edits still count as unsaved.

### Portrait presets

The default `pipelines` folder includes [Natural](../pipelines/Natural.json)
and [Beauty](../pipelines/Beauty.json). Both use automatic white balance with
35% blending and automatic exposure at 35% strength with a half-stop limit.
The skin stages use the exposed face/body mask. The following initial values
are editable on their stage cards.

| Skin treatment | Natural | Beauty |
| --- | --- | --- |
| Local tone evening strength | 30% | 60% |
| Neighborhood radius | 16 px | 32 px |
| Per-channel color shift limit before strength | 4% of local luminance | 8% of local luminance |
| Alibaba smoothing | Off (stage omitted) | 30% strength on exposed skin |

Both need the local SelfieMulticlass ONNX checkpoint. Beauty additionally needs
the Alibaba smoothing checkpoint and a supported PyTorch CPU setup. Its second
mask stage measures the pixels after local tone evening. The smoothing stage
requests permission before resizing a frame over 5,000 pixels on its longest
side. Model filenames and installation instructions are on the stage cards.
[Exposed Skin Mask](stages.md#exposed-skin-mask) and [Alibaba smoothing](stages.md#alibaba-skin-retouching-stage).

Blemish removal is opt-in. Review freckles, makeup and skin boundaries at 100%
before settling on strengths for a studio set. Visual acceptance is pending.
With a custom preset folder, open either shipped JSON through **Open JSON…**
and use **Save JSON…** to place an editable copy in that folder.

### Portable pipeline format

- Array order determines execution order. IDs identify instances, not operation
  types, and must be unique within the pipeline.
- Each registered operation supplies a versioned parameter schema, defaults, and
  required assets. Define a migration explicitly when a setting's meaning changes.
- Declare required/optional/provided context keys, compatible context revisions,
  and invalidation effects in the operation registry. Stage results preserve source
  metadata and carry image/context forward according to those declarations.
- `model_ref` resolves to a local asset manifest containing version, checksum, and
  runtime requirements under the configured checkpoint folder. Runtime backends
  must support Python 3.14.
- Do not embed model weights, photographs, temporary paths, or job progress in
  pipeline JSON. Source, destination, recursion, temporary root, and execution mode
  are job/session settings, stored separately from the operation sequence.
- Reject malformed data, unknown versions/operations, duplicate instance IDs, and
  invalid parameters with actionable errors. Preserve an existing valid pipeline
  when a structural import fails. Asset/context readiness is separate: missing
  model files load into disabled/error cards, and enabled stages with unresolved
  required context block execution as described below.
- Save explicit files atomically. Autosave the last valid pipeline separately and
  restore it at startup. Report a corrupt or incompatible autosave; use a known
  valid backup/default rather than silently dropping unsupported stages.
- An empty or all-disabled pipeline is a proposed valid pass-through that still
  performs the configured final export and destination validation.

ComfyUI-like JSON portability does not establish compatibility with ComfyUI's
schema. Branches, merges, and a node graph editor are outside the first version.
The [Runtime and launch contract](runtime.md) defines the machine-local checkpoint
and temporary folders, source launch options, and explicit-pipeline precedence.

## Processing scale settings

The Settings dialog has independent downscaling and upscaling defaults. Both
offer bilinear and nearest-neighbor interpolation. Bilinear retains the
original PP-HumanSeg sampler behavior. The Person foreground stage exposes
both settings on its card and in stage preview, each with a **Use app setting**
choice. When one axis shrinks and the other grows, each uses its own method.

Portable pipelines store explicit overrides in the stage parameters and omit
inherited filters. Existing pipeline files inherit both. Each preview resolves
the current choices and new jobs store the effective values in their frozen
pipelines. Existing job snapshots with omitted methods replay bilinear sampling.
The choice of image filter is independent of the model's required tensor shape.
Categorical masks need label-preserving sampling when mapped between grids.

The two Alibaba stages apply their respective effective filters when resizing
RGB inputs and predicted smoothing maps. The blemish mask uses the publisher's
nearest-neighbor mapping. Resize consent belongs to a preview or a frozen job
stage and is independent of the portable pipeline.

## Stage controls and model availability

Cards show the implementation version beside the algorithm name. **In** lists
pixels and declared context inputs. Optional inputs carry a dashed border and
an explicit label. **Out** lists edited pixels and produced context. The tooltip
on **Out** describes context preservation and invalidation.

Pixel stages expose a shared `blend` parameter. At 0.0 they return the stage
input pixels, at 1.0 they return the algorithm output, and intermediate values
interpolate in the working linear-sRGB representation. The slider steps by 0.01.
Both previews and batch schedules use the same calculation. Full blending is
the default and is implicit in pipeline JSON to preserve older checkpoint identities.
Stage-specific strength controls apply before blending. Partial blends require
aligned pixel grids and valid context for the mixed image. The current pixel
operations preserve geometry and produce no pixel-dependent measurements.

Every editable stage has an on/off control. Off stages retain their order and
parameters and pass incoming image/context through without executing or producing
new context. Mandatory decode/metadata/export infrastructure remains outside this
list. Model availability and the enabled flag are separate aspects of stage state:

| State | Control and presentation | Execution |
| --- | --- | --- |
| Ready and on | Enabled control, ready status | Runs if context requirements are met. |
| Manually skipped | Off control, skipped status; parameters retained | Does not run. |
| Model unavailable | Automatically off, error icon/text, missing-file details | Cannot be enabled or previewed until required assets validate. |
| Required context blocked | Dependency error naming the missing/disabled producer | Does not run while enabled and unresolved; fix the dependency or turn the consumer off. |

For missing model files, show a concise error on the stage card, with exact asset
names/versions and expected paths in expandable details. Explain which files are
missing when a stage needs several checkpoints. Parameters remain editable, but
the stage's activation control is unavailable until recovery. Use text/icons as
well as color, and link technical details to the diagnostics panel.

Revalidate on pipeline load, last-used restore, checkpoint-root/model changes,
explicit Recheck action, and execution preflight. A missing asset forces the
in-memory `enabled` value to false; normal saves/autosave preserve that off state.
Do not rewrite an explicitly loaded JSON file on disk until the user saves it.
Record the automatic disable reason in local stage state and diagnostics rather
than putting machine-specific availability errors into portable pipeline JSON.

After files are supplied, validate their identity/checksum and compatibility,
clear the error, and make the on/off control available again. Leave the stage off
until the user enables it. Corrupt or incompatible assets are also unavailable
with their own diagnostic reason, not a substitute checkpoint or backend.

Recompute required context availability across the enabled sequence after every
skip/availability change. A disabled producer's old cached context is not a valid
replacement for running it in the current pipeline. Compatible context from another
enabled provider or a declared safe fallback may satisfy a dependency. Otherwise
show the affected consumer as blocked and prevent starting that invalid enabled
sequence; do not silently apply a targeted operation globally or skip it as success.

Before a run, display the enabled sequence and skipped/unavailable stages clearly.
An off missing-model stage does not itself block unrelated ready stages. Record
all skipped stages and reasons in the job snapshot and summary so outputs are not
represented as having received unavailable edits. Availability changes affect the
editor and future jobs; an active job keeps its snapshot. If an asset needed by an
active job disappears, handle the actual failure explicitly rather than silently
dropping the stage for subsequent images.

Use **Settings → Open model page in browser** to visit the publisher. The model catalog provides the
publisher URL, version and expected file identities. Download the required files
and place them at the paths shown on the card under the configured model folder
(`models/` by default). Select **Settings → Check required model files** to verify the installed files locally.

## Preview meaning

| Preview | Input and execution | Purpose |
| --- | --- | --- |
| Original | Decode/orient/color-manage the selected source; no retouching | Inspect source without a destination. |
| Whole pipeline | Run all enabled stages on the selected source | Inspect the final configured result. |
| Stage card **Preview** | Compare the normalized source against the selected stage's result on that source | Isolate one operation and tune its parameters. |
| Stage card **Preview in pipeline** | Compare the result of the preceding enabled stages against the result after applying the selected stage | Inspect the operation with its upstream changes and analysis context. |
| Context analysis | Run selected analysis and display region/mask overlays or measurements | Inspect output even when pixels are unchanged. |

Double-clicking a thumbnail opens its source in a modal window. **Run pipeline**
opens a window-modal progress dialog immediately and displays the completed
comparison in the same photo window. The dialog names the running stage, shows
its position among enabled stages, and displays elapsed time since **Run pipeline**.
If a stage needs resize approval, progress yields to the prompt and reopens for
the approved retry with the same elapsed clock. Whole-pipeline
and stage comparisons start in **Side-by-side**. In **Overlay**, the result fills
the pane. Holding Shift shows the comparison input until the user releases it.
All photo and stage previews support wheel zoom around the pointer and drag
to pan. The panes share magnification and image center across mode changes
and stage refreshes. **100%** preserves the inspected center and maps an oriented
input or result pixel to one physical screen pixel. **Fit** recenters the image
and fits the visible panes.
The stage parameters are a local draft. Parameters marked `preview_on_change`
refresh after a short debounce. Other parameters enter the preview when the
user selects **Preview changes**. Automatic renders use a fixed-size footer bar
that stays visible between updates. Stages with manual parameters open a
window-modal progress panel on their initial render and on **Preview changes**.
Both bars animate while rendering because the renderer reports completion only.
Only the latest requested settings can update
the panes. **Save** on close commits the draft through the editor and last-used
pipeline persistence. **Discard** closes the comparison and **Cancel** keeps it open.

Originals and thumbnails use local orientation and color conversion. Processing
previews run at full working resolution on the desktop command thread while the
batch worker is idle. Neural previews also take the batch runner lock across
processes. The stage comparison retains one validated input in memory
for the duration of the dialog. The service supplies full-resolution sRGB bytes
for inspection and a 1,600-pixel overview for large images. Available luminance
measurements accompany the result. The person
foreground stage comparison tints the detected region in its result pane. See
[desktop workspace evidence](verification/desktop-workspace.md).

Both stage actions explicitly run the selected available operation, including
a manually disabled operation. Earlier stages in **Preview in pipeline** and all
stages in the whole-pipeline preview honor their enable flags. If an isolated
stage requires upstream analysis, the dialog directs the user to **Preview in
pipeline** and identifies the dependency. Unavailable stages show recovery actions
on their cards.
Use optional cached context only when its coordinate and pixel dependencies are
compatible with the preview input. Do not apply a later-stage mask to an original
image merely because their dimensions match.

Previews run without a destination. Preview images and cached stage inputs live
in memory and are discarded when their window closes. Saving stage settings
persists parameters. GPU preview queuing remains planned and must
serialize model work with active sequential images or staged passes.

## Equivalent schedules

For stages A, B, C and inputs 1, 2, 3:

```text
Sequential:
1: A -> B -> C -> export
2: A -> B -> C -> export
3: A -> B -> C -> export

Staged:
load A -> A(1), A(2), A(3) -> persist results -> release A
load B -> B(1), B(2), B(3) -> persist results -> release B
load C -> C(1), C(2), C(3) -> persist results -> release C
export 1, 2, 3
```

Staged mode trades intermediate disk space and I/O for fewer model loads. It does
not inherently reduce a single stage's peak memory requirement. Every operation
still needs a validated strategy for the available VRAM, such as image crops or
tiles when supported. Sequential mode may reload checkpoints at each image's stage
boundaries if they cannot coexist in memory.

Stage preparation normally occurs once per uninterrupted stage pass, with no
promise that a crash/retry can avoid a reload. A stage may manage multiple local
assets internally, but the manager must not preload every pipeline stage at once.

The final export pass uses the frozen export format and receipt protocol shared with
sequential mode. An empty or all-disabled staged job goes directly to decode and
export. Staged pause/cancel requests are checked before each image-stage or export
unit. Pausing releases the current stage and keeps its committed checkpoints.

Match image normalization, intermediate precision, operation versions, model
assets, parameters, and per-input seeds across modes. Define numerical tolerances
from validation; do not claim universal bitwise-identical GPU inference.

## Artifact identity and lifecycle

- Snapshot the source manifest and full configuration at job start.
- Identify reusable results by input content identity, pipeline prefix, operation
  and model versions/checksums, parameters, working representation, and seed.
  Changing a stage invalidates that stage and all downstream cached results.
- Store each intermediate image and its required masks/metadata as one committed
  logical state. Never mark a stage complete while its artifacts are partial.
- Journal individual image-stage commits. On worker restart within an open batch, validate committed artifacts
  and resume each image from its latest available valid dependency. If earlier
  dependencies have been reclaimed, recompute them from the source as needed.
- Keep only dependencies needed for resume and the next stage, unless retention
  was requested. Delete a predecessor only after its successor is committed.
  The staged runner keeps one journaled checkpoint per active image. If it is
  missing or corrupt, the affected image may rebuild from its frozen source up
  to twice. Other images keep their current checkpoints.
- A failed stage blocks downstream execution for that image; it does not silently
  pass through an unretouched image as a successful result.
- Cleanup is limited to manifest-owned job artifacts. Successful completion,
  canceled-job disposal, and resumable interruption have different retention
  needs. Never recursively delete a user-selected root as a cleanup shortcut.
