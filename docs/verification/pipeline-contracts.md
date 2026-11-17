# Pipeline contracts and reference stages (3a)

Implemented and reviewed by one agent on 2026-11-17, continuing the authorized
runtime foundation. This is contract/reference acceptance on the prepared Windows
11/Python 3.14.7 DirectML environment, not portrait or full batch acceptance.

## Delivered behavior

- Strict version-1 pipeline parsing and operation/version registration, numeric
  schemas/defaults, ordered repeated instances, immutable definitions, headless
  add/remove/reorder/enable/parameter commands, atomic save/load/autosave and restore.
  Invalid structural imports or failed writes retain the current valid state.
- Lightweight operation factories and prepare/process/release sessions. A prepared
  pass can process multiple images; cleanup runs after partial preparation or a
  failed unit. Cleanup failures are logged without hiding the primary exception.
- Source metadata bytes, immutable pixel storage, typed luminance context, source
  and settings identities, revisions, coordinate conventions and descriptor effects.
  Missing/stale/wrong-version/wrong-image required context fails. Optional stale
  context is absent; explicit spatial preservation and geometry invalidation are
  exercised with synthetic test operations.
- CPU statistics and manual exposure references on normalized linear-sRGB float32
  arrays. Strip-sized validation/statistics buffers avoid full-image masks. There
  are no batch buffers or models retained by these operations. Output plus input
  and construction copies still require multiple single-image buffers; no 16 GB
  batch-memory acceptance is claimed.
- Local model manifest/checksum/backend checks, safe resolved checkpoint paths,
  exact expected-file errors, automatic disable and explicit recovery. No model is
  selected, shipped or downloaded. Test fixtures contain authored synthetic bytes,
  with placeholder publisher pages intercepted during navigation tests.
- Qt stage cards reflect the same service state: disabled activation for missing
  assets, editable parameters, required-context errors, recheck and explicit page
  navigation. Recovered stages remain off. Runtime/UI/source package metadata use
  Auto ReTouch and identify application author Neyrome.

## Verification

Commands use the existing environment without preparation, package installation,
backend switching or model downloads:

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.venv\Scripts\python.exe -B tests\pipeline_visual_check.py
```

The ordinary suite has 41 tests: 17 foundation, 16 pipeline, 2 Qt and 6 existing
runtime integration checks. Pipeline checks include JSON round trips/defaults,
rejection/recovery and atomic-save denial; separate immutable snapshots; missing,
wrong-checksum, unreadable and backend-incompatible model fixtures; dependency
reorder/skip/recovery; source metadata and sample precision; clipping diagnostics;
context provenance/revision rejection, optional fallback, spatial preservation and
geometry invalidation; fresh sessions versus a shared stage pass; simulated
prepare/process/release and memory failures with tracebacks and release evidence.
Shared JSON reads also reject excessive nesting before decoding while accepting
brackets/escaped quotes inside ordinary string values. Qt parameter focus changes
preserve stored precision when the user has not changed the displayed value.

Qt integration verifies missing-file details, unavailable toggles, editable saved
parameters, publisher navigation only on click, recovery remaining off, downstream
dependency recovery, disappearing assets, and persistent diagnostics failure text.
Headless subprocess imports reject Qt and leave NumPy/ONNX Runtime unloaded until
array processing. Static import-boundary checks cover all shared modules.
Existing runtime checks include actual DirectML arithmetic-provider execution,
launcher errors/help and timed offscreen GUI startup/shutdown. They do not test
portrait-model inference or change the prepared backend.

Visual inspection of ignored `.test-work/pipeline-cards.png` confirmed readable
error/status text, retained parameters, recovery controls and no clipping of card
content at the captured 980×940 window size. The synthetic exposure comparison
`.test-work/exposure-reference.png` uses 340×220 grayscale/color ramps, displayed
with an explicit test-only sRGB transfer at original, +1 and −1 stops. Inspected
direction of brightness change and expected highlight clipping. This is a
functional visual check, not user acceptance of portrait appearance.

## Review and remaining gates

Reviewed JSON compatibility, no partial import replacement, immutable snapshots,
source buffer ownership, metadata retention, context dependency effects, bounded
strip allocations, missing-model off-state persistence, resource release and
actionable diagnostics. No real photos or derived portrait data were used.

The production model catalog is empty. Registration will require reviewed model
identity/version/checksums/backend and explicit commercial-use evidence; the
manifest field itself does not establish legal permission. Asset hashing and
autosaves are currently synchronous in the reference shell. Move substantial
readiness work/persistence behind a bounded worker with revision-aware results
before enabling production model assets, thumbnails or inference in milestone 5.

Pipeline preflight alone does not validate destinations, source relationships or
disk reservations. There is no photo-processing start/preview/export action yet.
Decode/ICC handling, lossless persistence and artifact identity, JPEG EXIF
round trips, Natural/Beauty appearance, all-skin/multi-person models, recoverable
schedulers and approximately 500-photo memory/disk acceptance are still pending.
The small shared-pass test establishes reference operation semantics only; it is
not staged persistence/recovery or scheduler equivalence acceptance. CUDA
acceptance remains pending on NVIDIA hardware. No performance benchmark is claimed.
