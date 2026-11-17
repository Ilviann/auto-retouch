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
Qt currently renders loaded stages, parameters, toggles, rechecks and model-page
links. Add/remove/reorder widgets and file dialogs belong to milestone 5.

Pipeline preflight checks operation, assets and context. Batch and preview
execution still require destination, path and disk validation.
No neural operation/model is registered in the production catalog. Model-state
acceptance currently uses synthetic local bytes and injected test operations.

The 3b storage slice persists reference states with lossless float32 TIFF,
strict versioned manifests, typed context and cache identity/checksums. The
headless JPEG adapter initializes oriented image states and exports verified sRGB
JPEGs. XMP and IPTC-IIM join EXIF and ICC as immutable source metadata. Version-1
artifact manifests can carry optional checksummed XMP and IPTC blob references;
earlier manifests still load. Include the decoder/color policy and relevant
dependency versions in scheduler cache identity. Both headless schedules use
the JPEG adapter and the same stage sessions as reference execution. Worker
transport and desktop job controls remain pending. See
[artifact evidence](verification/lossless-artifacts.md),
[JPEG evidence](verification/jpeg-boundary.md),
[sequential job evidence](verification/sequential-jobs.md), and
[staged job evidence](verification/staged-jobs.md).

## Editor and JSON settings

The editor provides add, remove, reorder, enable/disable, and a parameter panel
for each operation instance. Multiple instances of the same operation are allowed.
Natural and Beauty are bundled editable pipeline presets.

An illustrative portable JSON file is:

```json
{
  "schema_version": 1,
  "name": "Natural portrait",
  "stages": [
    {
      "id": "contrast-1",
      "operation": "core.auto_contrast",
      "operation_version": 1,
      "enabled": true,
      "parameters": { "strength": 0.3 }
    },
    {
      "id": "faces-1",
      "operation": "analysis.portrait_detection",
      "operation_version": 1,
      "enabled": true,
      "model_ref": "portrait-detector-candidate",
      "parameters": {}
    },
    {
      "id": "regions-1",
      "operation": "analysis.face_regions",
      "operation_version": 1,
      "enabled": true,
      "model_ref": "face-parser-candidate",
      "parameters": {}
    },
    {
      "id": "skin-1",
      "operation": "portrait.skin_texture",
      "operation_version": 1,
      "enabled": true,
      "model_ref": "skin-texture-candidate",
      "parameters": { "strength": 0.25 }
    }
  ]
}
```

- Array order determines execution order. IDs identify instances, not operation
  types, and must be unique within the pipeline.
- Each registered operation supplies a versioned parameter schema, defaults, and
  required assets. Define a migration explicitly when a setting's meaning changes.
- Declare required/optional/provided context keys, compatible context revisions,
  and invalidation effects in the operation registry. Stage results preserve source
  metadata and carry image/context forward according to those declarations.
- `model_ref` resolves to a local asset manifest containing version, checksum, and
  runtime requirements under the configured checkpoint folder; the example names
  no actual selected checkpoint. Runtime backends must support Python 3.14.
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

## Stage controls and model availability

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

Use Open model page to visit the publisher. The model catalog provides the
publisher URL, version and expected file identities. Download the required files
and place them at the paths shown on the card under the configured model folder
(`models/` by default). Select Recheck to verify the installed files locally.

## Preview meaning

| Preview | Input and execution | Purpose |
| --- | --- | --- |
| Original | Decode/orient/color-manage the selected source; no retouching | Inspect source without a destination. |
| Whole pipeline | Run all enabled stages on the selected source | Inspect the final configured result. |
| Operation only | Run the selected operation on the normalized original image | Isolate one operation and tune its parameters. |
| Through selected stage, proposed | Run the enabled prefix through the selected stage | Inspect that operation in its actual upstream context. |
| Context analysis | Run selected analysis and display region/mask overlays or measurements | Inspect output even when pixels are unchanged. |

An operation-only preview explicitly requests a ready operation, even if manually
disabled in the batch pipeline. An unavailable operation cannot be previewed;
display its recovery actions. Whole/prefix previews honor enable flags. If an operation
needs an artifact normally produced upstream, explain the dependency and offer a
prefix preview; do not imply that an isolated result represents the full pipeline.
Use optional cached context only when its coordinate and pixel dependencies are
compatible with the preview input. Do not apply a later-stage mask to an original
image merely because their dimensions match.

All processing previews require valid destination settings, as requested for any
processing. Store preview artifacts in the application's temporary/cache area;
only an explicit export creates a final destination image. Distinguish a quick
downscaled preview from full-resolution detail so texture assessment is meaningful.
Queue GPU previews behind an active sequential image or staged stage pass, showing
the pending state. They must not load a second inference stack alongside a batch.

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

The final export pass uses the verified JPEG receipt protocol shared with
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
- Journal individual image-stage commits. On restart, validate committed artifacts
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
