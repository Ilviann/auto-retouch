# Initial stage catalog and image state

Auto ReTouch, authored by Neyrome, uses an ordered pipeline whose stages can change
pixels, add or update analysis context, or do both. The following operation set and order
are proposals; models, algorithms, and numeric preset defaults require evaluation.
Analysis outputs must be visible and reusable rather than hidden in a retouching
operation's private state.

Milestone 3a introduced CPU image-statistics and manual-exposure references.
See [verification](verification/pipeline-contracts.md). The in-memory working
representation is normalized H×W×3 linear-sRGB float32 (12 pixel bytes per pixel),
with bytes-backed read-only arrays and immutable source EXIF/profile bytes.
Statistics carries source identity, producer/settings hash, operation/context
versions, pixel/geometry revisions, dimensions and explicit oriented coordinates.
Required stale/wrong-source/wrong-version context fails; stale optional context
is absent. Pixel edits invalidate appearance measurements; spatial context can
survive only an explicitly declared preservation, and geometry changes invalidate
it. Source metadata bytes are retained across stages.

The 3b artifact slice now persists this reference representation and current typed
statistics/measurement context in float32 TIFF plus strict JSON manifests, retaining
EXIF, ICC, XMP and IPTC-IIM bytes exactly and verifying source/revision/coordinate
provenance. The JPEG adapter applies input orientation and converts to working
linear sRGB. A bounded person foreground grid now has its own artifact codec.
Exposed-skin masks and per-person association remain pending.
See [artifact evidence](verification/lossless-artifacts.md) and
[JPEG evidence](verification/jpeg-boundary.md).

The first AI analysis operation, `analysis.person_foreground`, runs the OpenCV
Zoo PP-HumanSeg March 2023 FP32 checkpoint. It adds `person.foreground` version 1
without editing pixels. The 192 × 192 binary grid spans the full oriented image
rectangle and is shown with nearest-neighbor sampling in the stage comparison.
It represents pooled person foreground. Exposed-skin classification and person
instances require further operations. See the
[checkpoint decision](decisions/0004-first-ai-checkpoint.md).

**Auto exposure and tone** (`core.auto_exposure` version 1) is a CPU operation
that measures the median of usable linear-sRGB luminance samples. It considers
samples in the range 0.025–0.85 and leaves the pixels as they were if fewer than
5% fall in that range or the measured median is already within 0.14–0.30. For
medians outside that band it estimates a shift toward the nearest end of the
band, limits it by **Maximum correction (stops)** (0–2, default 1), and scales
it by **Strength** (0–1, default 0.5). Both settings support live preview.

The stage applies a monotone curve in linear light, using a common multiplier
for the three channels to preserve channel ratios. The curve fixes black and
pixels with a maximum channel of 1 and rolls into highlights without hard
clipping. It keeps the original metadata, dimensions, and geometry revision.
Place it before image statistics or AI analysis so those stages measure the
corrected pixels. The target band and defaults are provisional and may need
adjustment for intentional lighting or grading. See [verification](verification/auto-exposure.md).

## White balance and color cast

`core.white_balance` v1 operates on full-resolution linear-sRGB pixels using the
CPU. Place it before exposure and analysis. **Automatic** estimates red and blue
gains relative to green from a grid of up to 192 × 192 samples. It needs
consistent near-neutral pixels in at least two brightness bands. An inconclusive
estimate leaves pixels unchanged and shows a warning in the stage preview.
This heuristic can mistake a lightly colored surface for neutral.

For a set shot under the same lights, open the stage preview on a reference
shot, choose **Pick neutral from input**, and click a gray card or known neutral
midtone in the input pane. The picker measures a 5 × 5 patch in the original
linear stage input, including when zoomed or previewing after upstream stages.
Clipped, very dark, or excessively saturated samples can be rejected.
Sampling selects **Fixed** and sets red and blue adjustments in stops relative
to green. Saving the draft stores these values in the pipeline. Subsequent
photos use the same gains regardless of composition. **Blending** sets the
strength. Both modes retain the neutral reference's luminance at midtones and
roll off the correction at highlights. Numeric defaults remain provisional.

## Face detection with YuNet

`analysis.face_detection` v1 evaluates the local OpenCV YuNet March 2023 FP32
model on the prepared ONNX Runtime backend. It fits the oriented stage input
into a 640 × 640 BGR tensor, pads unused pixels, and suppresses overlapping
detections. Preview shows boxes and a count at the original image coordinates.
**Minimum confidence** defaults to 0.80. A higher value rejects more candidate
faces. Inspect the boxes before using this stage on a new lighting setup or group.

**Expand each side (px)** adds a margin to every retained rectangle in
full-resolution, oriented photo pixels. A value of 20 adds 20 pixels on each
side, up to the photo edges. The default is 0. Expansion happens after duplicate
detections are suppressed. The preview and downstream stages receive the same boxes.
**Downscaling** and **Upscaling** choose how the detector samples its input when
the longest side is respectively above or below 640 pixels. The photo keeps its
original resolution.

Place the stage before **Blemish cleanup (Alibaba)** or **Skin tone smoothing (Alibaba)**. The detector
leaves pixels unchanged and publishes `face.regions` v1, including an empty
set when detection succeeds with zero faces. With **Use face rects from context**
checked, Alibaba passes through an image with zero detected faces. If no upstream
stage supplied face regions, Alibaba uses the whole image.
[Checkpoint, rights and verification](decisions/0005-yunet-face-detection.md).

## Proposed first catalog

| Operation | Outputs | Main controls and dependencies |
| --- | --- | --- |
| Image statistics | Context: histogram/luminance statistics and clipping warnings; pixels unchanged | Analysis scope and thresholds. Measurements identify the pixel revision they describe. Useful as an initial CPU reference stage and diagnostic overlay. |
| White balance and color-cast correction | Corrected pixels | Implemented automatic and fixed CPU correction, neutral picker and blending. |
| Auto exposure and tone | Corrected pixels and correction diagnostics | Implemented baseline with strength, stop limit, and highlight protection. It measures the current pixels directly. |
| Person and exposed-skin analysis | Context: person regions/instances and exposed-skin masks across the whole photo; pixels unchanged | Validated body/skin segmentation, confidence and overlap handling, association of visible skin with each person. Required for the confirmed all-skin/multiple-person scope; face parsing alone is insufficient. |
| Portrait detection | Context: oriented face boxes; pixels unchanged | YuNet face boxes and minimum confidence implemented. Landmark output and person association remain proposals. |
| Face-region parsing | Context: precise skin, lips, eyes, and teeth masks where supported; pixels unchanged | Per-region confidence, edge treatment, and analysis resolution. Requires faces or a declared self-contained detection implementation. Teeth must have a validated teeth mask; a mouth box is insufficient. |
| Blemish cleanup | Local pixel edits within valid skin regions; optional candidate/debug context | Strength, maximum spot size, protected regions, and model-specific controls. Optional and quality-gated: distinguishing temporary blemishes from permanent features is uncertain. |
| Skin-tone evening | Local color edits within valid skin regions | Strength and maximum local tone/color adjustment. Reduce blotchiness while preserving natural skin color, lighting gradients, and protected features; independent from texture smoothing. |
| Skin texture correction | Skin-local pixel edits | Strength, detail preservation, and blending. Uses valid skin masks and protects eyes, lips, hair, and facial features. Conventional baseline or validated neural adapter; do not equate face restoration with retouching. |
| Teeth whitening | Pixel edits confined to a valid teeth mask | Strength, yellow reduction, brightness limit, and edge feathering. Preserve shading and tooth detail. Optional and disabled by default until visual review. |

Proposed default order: white balance -> exposure/contrast -> person/skin analysis -> portrait detection ->
face-region parsing -> optional blemish cleanup -> skin-tone evening -> skin
texture -> optional teeth whitening. Insert image-statistics stages where measurements or comparison are
useful; each measurement remains tied to its originating pixel state.

Natural and Beauty use the same catalog with different enabled stages and
intensities. Natural emphasizes restrained texture-preserving edits; Beauty allows
stronger skin treatment within the same feature protections. Keep blemish cleanup
and teeth whitening opt-in until reference examples establish acceptable behavior.
Mask overlays must be available in preview so users can inspect analysis stages.

Retouching covers all exposed skin of multiple people, with face-specific masks
refining protection around facial features. Validate arms/hands/legs, overlapping
people, and visible skin without a detectable face. Preserve skin-tone differences
between people. This expands analysis beyond the earlier face-only candidate set;
body/skin model selection is still pending.

Red-eye correction is a useful next optional operation, with pupil/candidate masks
and bounded color correction. Eye whitening, denoising,
and sharpening follow demonstrated need. Do not make every possible enhancement
part of the default studio portrait pipeline.

## Alibaba skin-retouching stage

Choose either operation in the Add menu. Both run on PyTorch CPU. When using both,
put cleanup before smoothing to apply the tone adjustment to the repaired pixels.

| Operation | Effect | Required file |
| --- | --- | --- |
| **Blemish cleanup (Alibaba)** (`retouch.alibaba_blemish_cleanup` v1) | Detects and repairs localized blemishes in each input region. | `joint_20210926.pth` |
| **Skin tone smoothing (Alibaba)** (`retouch.alibaba_skin_tone_smoothing` v1) | Applies a feathered RGB tone adjustment to the incoming image or face crop. | `pytorch_model.pt` |

Each card has its own enable switch and settings.

| Setting | Default | Behavior |
| --- | --- | --- |
| Strength | 1.0 | Mixes the edit with the model input in linear RGB, from 0 (pass through) to 1 (full edit). |
| Use face rects from context | On | Applies the operation to upstream face regions when available. Turning it off selects the whole image. |
| Downscaling / Upscaling | Use app setting | Independently select the app setting, bilinear, or nearest neighbor in each direction. |
| Blending | 1.0 | Mixes the finished stage output with its full-resolution input, from 0 to 1. |

The working pipeline, imported presets and launch overrides skip the retired
`retouch.alibaba_skin` v1 entry and preserve the order of the other stages.
Each new stage must be added explicitly. Previously frozen jobs retain their
original execution contract. Portrait quality and preset strengths await user review.

For local evaluation, place the following two source checkpoints under
`alibaba-skin-retouching/16c8351a/` in the configured checkpoint folder.
Anonymous byte-range requests succeeded for both international publisher links
on 2026-11-18. The publisher's file listing reports revision
`16c8351a01c90746eb702e6e6c1e6a28d0e5ff74` for both files. Use the listed
SHA-256 values to verify the downloads since the master links can change.
The international listing did not expose the v1.0.4 tag.

Both local source files matched the publisher checksums on 2026-11-18.
With Python 3.14.7, PyTorch 2.15.0+cpu, ModelScope 1.40.3 and addict 2.4.0
in the development environment, the original network definitions loaded these
weights strictly and ran in float32. Generated inputs exercised the 512 × 512
smoothing network, 768 × 768 blemish detector, and 576 × 576 repair window.
The same process then ran PP-HumanSeg through ONNX Runtime DirectML 1.24.4.
The runtime package pins still validated and `pip check` passed.
Repeat the native CPU check with
`.venv\Scripts\python.exe -B tools\verify_alibaba_pytorch.py`.
The app includes the required Apache-2.0 network definitions from ModelScope
1.40.3 and the associated license. Each operation loads its checkpoint with strict
state-dictionary checks and weights-only loading. ModelScope sign-in and the
ModelScope Python package are unnecessary for this stage. The publisher lists
the model repository under Apache License 2.0 in its
[repository metadata](https://www.modelscope.ai/api/v1/models/iic/cv_unet_skin_retouching_torch)
and [README](https://www.modelscope.ai/models/iic/cv_unet_skin_retouching_torch/resolve/master/README.md).

Both operations encode linear pixels to sRGB for inference. Cleanup detects blemishes at
768 × 768 and runs the repair network one 512-pixel tile at a time with a
32-pixel halo. Smoothing uses a 512 × 512 generator to predict a feathered tone
adjustment for its input. Output returns to linear float32. The separate upstream
RetinaFace and TensorFlow whitening models are outside this implementation.

| Checkpoint | Bytes | SHA-256 |
| --- | ---: | --- |
| [pytorch_model.pt](https://www.modelscope.ai/models/iic/cv_unet_skin_retouching_torch/resolve/master/pytorch_model.pt) | 53,646,276 | `760d0c0b95c4e0a692165677fe2b82dbdf8fbbd4a3780ca966599c627b082993` |
| [joint_20210926.pth](https://www.modelscope.ai/models/iic/cv_unet_skin_retouching_torch/resolve/master/joint_20210926.pth) | 227,618,450 | `c9e1388b19d9334c90db08308c86de208f535920ee381ae8a9cb63484a78351a` |

**Use face rects from context** is checked by default, including in older saved
pipelines. Clear it to retouch the whole incoming image, even when the detector
supplies rectangles or reports zero faces. The 5000-pixel resize rule then
applies to the whole image.

Apply the 5000-pixel limit to each actual stage input. With the option checked and upstream face
rectangles, crop the current oriented image at those rectangles. With per-face
masks, use their bounds and retain the masks for compositing. Validate the
regions against the image and geometry revision. Process the crops individually
and place each result back at its original coordinates. Pixels outside the
selected regions retain their incoming values. Without upstream face context,
the whole image is the stage input. A valid detector result with zero faces
produces a warning and passes the image through. The optional `face.regions`
v1 payload carries oriented half-open rectangles and optional packed binary mask
grids. The staged artifact codec preserves these across passes and recovery.
The **Face detection (YuNet)** stage produces these rectangles.

A 7200 × 4800 image with two 900 × 900 face crops needs no size warning.
The same image used whole needs approval to run at 5000 × 3333 and return to
7200 × 4800. An oversized face crop triggers the same rule for that crop.
Exactly 5000 pixels on the longest side is within the limit. Restoring the
dimensions cannot recover fine detail lost during downscaling.

Preview asks for consent when an actual input exceeds the limit. Batch creation
checks the selected JPEG headers and requests conditional permission once per
stage when inputs could exceed the limit. The warning shows dimensions and
detail loss. Declining stops creation. The job stores approved stage IDs and
logs the source and processing dimensions for each actual size reduction.

One 4000 × 6000 private study portrait ran through the desktop preview service
at 3333 × 5000 and returned to its original geometry. Sequential and staged
worker subprocesses produced matching decoded exports on a generated JPEG
with the real checkpoints. Focused checks in `tests/test_alibaba_skin.py`
also cover missing files, mask persistence, repair tile edges, stale targeting,
and job-scoped resize consent. The local before-and-after comparison is in
`.test-work/alibaba-portrait-comparison.jpg`, outside source control.
The split-stage comparison in `.test-work/alibaba-split-comparison.png`
shows a 572 × 768 study face crop, each stage alone, and cleanup followed by
smoothing. Cleanup selected no pixels on this crop. Smoothing made a small
tonal adjustment. With both strengths at 1, the two-stage result matched the
combined model on this crop. The two new stages also produced matching decoded
JPEG exports in sequential and staged worker runs on a generated input.
Skin-texture retention, background effects on whole images, face-crop quality,
and coverage of exposed skin beyond the face still need visual evaluation.
See the [publisher model card](https://modelscope.cn/models/damo/cv_unet_skin_retouching_torch/summary)
and [processing scale settings](pipeline.md#processing-scale-settings).

## Image state and stage effects

A stage consumes an image state and returns a new image state plus diagnostics.
The state contains:

- Pixel data or a managed reference, its representation/profile, dimensions,
  pixel revision, and geometry revision.
- Immutable source metadata, captured before orientation/color normalization.
- A versioned context map with typed entries for detections, landmarks, masks,
  measurements, and other derived data.

A context-only stage references unchanged pixels without making an unnecessary
copy. A pixel-only stage carries metadata/context forward subject to invalidation.
A stage can add or deliberately replace context entries using declared keys. It
does not mutate an upstream cached state in place.

Each operation descriptor declares context it provides, requires, optionally uses,
preserves, or invalidates, as well as its pixel/geometry effects. Context entries
record their producer, schema/model version, confidence when meaningful, source
pixel/geometry revisions, and coordinate conventions. Use per-face/person IDs to
associate related regions within the image; these are not identities across photos.

Coordinates are defined against the full-resolution, orientation-normalized image.
Record transforms from detector downscales or face crops explicitly and map results
back to that coordinate frame. Dense masks may use compact resolution if their
transform and resampling rules are explicit. Regions need pixel masks for selective
edits, not only bounding rectangles. Masks are persisted as artifacts rather than
large JSON arrays inside pipeline settings.

Crop, resize, rotation, or other geometry changes transform context correctly or
invalidate it. Appearance-dependent measurements are invalid after relevant pixel
edits. Spatial masks/landmarks can survive declared, validated photometric edits
without pretending they were regenerated from the latest pixels. The producer and
consumer contracts must specify this compatibility; uncertainty invalidates the
entry. Parameter/model changes invalidate their context and downstream cache use.

If optional context is absent, use only a documented fallback, such as computing
cheap image statistics, or return a declared no-op with a visible warning. Precise
teeth/skin edits require a validated targeting method: never silently apply them
globally. If required context is absent or stale, fail validation with the missing
producer named, or use an explicitly configured self-contained operation. Do not
silently insert neural stages into an editable pipeline.

Distinguish a missing context entry from a valid empty detection result. For
example, a completed analysis that finds no visible teeth can lead to a declared
unchanged result with a warning; a missing teeth-analysis stage is a dependency
problem. Both cases are explicit in diagnostics and preview.

Standalone operation preview uses the original with only compatible cached
context; if required analysis is unavailable, explain the dependency and offer a
preview through the relevant analysis stages. Context-stage preview shows overlays
and measurements even though image pixels are unchanged.

Staged mode commits image, context references, source metadata references, and
diagnostics as one logical image-stage result. Context-only outputs can reuse the
existing pixel artifact. Include context dependencies and their versions in cache
identities; retaining pixels alone is insufficient for recovery or mode equivalence.

## Metadata preservation and editing provenance

Metadata handling is mandatory import/export infrastructure, outside the removable
operation list. BMP explicitly omits embedded photo metadata. For the other formats,
apply the following transfer rules. Read and retain source EXIF before processing. Transfer capture
metadata to the final image, including camera/lens information, capture date/time,
exposure, and GPS when present. Preserve author/copyright and existing descriptive
metadata; the tool name is not a replacement photographer/author name.

Update only fields that must describe the new file correctly: orientation after
pixel normalization, output dimensions, applicable color-space information, and
editing/software records. Preserve original capture time; a processing timestamp
is a separate value. Regenerate or explicitly remove stale embedded thumbnails
rather than retaining an unretouched thumbnail as the exported preview.

Set the output Software field to the exact product name and application version:
`Auto ReTouch <version>`. Neyrome is the application author, not a replacement for
the source photographer's author/copyright metadata. Append an editing/export
provenance record with processing time, tool version and the pipeline snapshot's
SHA-256 in XMP history. Preserve prior history and
retain a previous Software value in provenance rather than silently discarding it.
Keep full pipeline settings and checkpoint details in current-job diagnostics;
do not embed face coordinates, dense masks, or machine-local paths by default.

The adapter checks capture tags and provenance by reopening the temporary export.
It removes the old EXIF and XMP thumbnails and carries standard XMP and Photoshop
IPTC-IIM. Source files without EXIF remain valid inputs. Malformed capture EXIF
and proprietary MakerNotes with unverified offsets have explicit failure paths.
See [JPEG metadata policy](decisions/0003-jpeg-boundary.md).

## Validation priorities

1. Implement image state, a context-only statistics stage, a simple pixel stage,
   lossless context persistence, and metadata transfer/provenance first.
2. Verify detector coordinate mapping and mask overlays across image sizes,
   EXIF rotations, multiple detected faces, and crop/downscale transformations.
3. Compare skin/teeth edits with targeting masks and protected regions at 100
   percent detail; evaluate both presets before selecting models/default strengths.
4. Test missing/stale/optional context, reordered/disabled analysis stages, geometry
   changes, cache invalidation, standalone previews, and both scheduling modes.
5. Round-trip real camera EXIF, capture timestamps/offsets, GPS, author/copyright,
   existing history, output orientation/dimensions/profiles, and processing markers.
   Include no-EXIF, malformed-EXIF, and MakerNote samples; compare metadata values,
   allowing only the documented output/provenance changes.

## Metadata references

- [Exiv2 EXIF tag reference](https://exiv2.org/tags.html) describes Software,
  capture/edit timestamps, orientation, and image dimension tags.
- [Exiv2 metadata history examples](https://exiv2.org/manpage.html) demonstrate
  XMP history records with a software agent and processing time. This is a format
  reference, not a decision to use Exiv2 or a particular Python binding.
