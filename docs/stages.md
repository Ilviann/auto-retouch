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
Pixel edits retain each output and its original measurement revision. Required
context must match the requested version, source and coordinate frame. A geometry
change requires analysis in the new frame. Source metadata bytes are retained across stages.

The 3b artifact slice now persists this reference representation and current typed
statistics/measurement context in float32 TIFF plus strict JSON manifests, retaining
EXIF, ICC, XMP and IPTC-IIM bytes exactly and verifying source/revision/coordinate
provenance. The JPEG adapter applies input orientation and converts to working
linear sRGB. A bounded person foreground grid now has its own artifact codec.
The exposed skin stage has a separate, lossless face/body label grid. Per-person association remains pending.
See [artifact evidence](verification/lossless-artifacts.md) and
[JPEG evidence](verification/jpeg-boundary.md).

The first AI analysis operation, `analysis.person_foreground`, runs the OpenCV
Zoo PP-HumanSeg March 2023 FP32 checkpoint. It adds `person.foreground` version 1
without editing pixels. The 192 × 192 binary grid spans the full oriented image
rectangle and is shown with nearest-neighbor sampling in the stage comparison.
It represents pooled person foreground. Exposed-skin classification and person
instances require further operations. See the
[checkpoint decision](decisions/0004-first-ai-checkpoint.md).

**Auto exposure and tone** (`core.auto_exposure` version 1) measures the median
of usable linear-sRGB luminance samples on the CPU. It considers
samples in the range 0.025–0.85 and leaves the pixels as they were if fewer than
5% fall in that range or the measured median is already within 0.14–0.30. For
medians outside that band it estimates a shift toward the nearest end of the
band, limits it by **Maximum correction (stops)** (0–2, default 1), and scales
it by **Strength** (0–1, default 0.5). Both settings support live preview.

When correction is needed, the stage applies a monotone curve in linear light
on the setup-selected ONNX Runtime backend (CPU, DirectML or CUDA), using a common multiplier
for the three channels to preserve channel ratios. The curve fixes black and
pixels with a maximum channel of 1 and rolls into highlights without hard
clipping. It keeps the original metadata, dimensions, and geometry revision.
Place it before image statistics or AI analysis so those stages measure the
corrected pixels. The target band and defaults are provisional and may need
adjustment for intentional lighting or grading. See [verification](verification/auto-exposure.md).

## Levels

**Levels** (`core.levels` v1) adjusts the whole image or one color channel.
Choose **RGB** to apply the same curve to red, green and blue, or choose a
single channel to correct a cast. Add another Levels stage to combine channel
adjustments with independent settings.

**Input black** and **Input white** use the familiar 0–255 sRGB scale. Values
at or below input black map to **Output black**. Values at or above input
white map to **Output white**. Between those points the stage normalizes the
input, raises it to the power `1 / gamma`, and scales it into the output range.
Increasing **Midtone gamma** above 1 lightens midtones with the default output
range. Lowering it below 1 darkens them. Raising output black lifts shadows,
lowering output white caps highlights, and reversing the output points inverts
the selected channels. Input black must be lower than input white.

Drag the black, gray or white triangle below the input histogram, or enter an
exact value in the matching field. Moving the gray triangle left lightens
midtones. The two handles under **Output Levels** can cross for an inverted
range. On the stage card the histogram comes from the selected source thumbnail.
**Preview** shows the full-resolution source histogram. **Preview in pipeline**
measures the full-resolution input after earlier enabled stages. Selecting
**RGB** combines the counts of the three sRGB channels.

In either preview, choose **Pick black** or **Pick white** and click the input
photo. For a single channel the picker reads that channel's value. In **RGB**,
black uses the largest of the three values and white uses the smallest so all
channels of the sampled pixel reach the chosen endpoint. Clamping follows the
stage's SDR mapping.

The default endpoints (0 and 255) and gamma (1.0) preserve the input exactly.
The stage operates on continuous float32 values between sRGB code points and
returns linear-sRGB pixels for pipeline blending. It keeps the image dimensions,
source metadata and compatible upstream context. All controls support live preview.
The curve runs locally on the setup-selected ONNX Runtime backend and requires
no checkpoint. [Levels verification](verification/levels.md).

## Auto Levels

**Auto Levels** (`core.auto_levels` v1) measures the image arriving at this
stage, including any edits earlier in the pipeline. It maps the measured black
point to 0 and white point to 255, with gamma fixed at 1. The saved pipeline
contains the measurement settings. Each preview and each batch photo gets
freshly calculated endpoints, displayed in the stage result caption and
recorded in that photo's diagnostics.

**Shadow clipping** and **Highlight clipping** each default to 0.1% and can
be set independently from 0% to 5%. For each channel the stage counts all
pixels in a 256-bin sRGB histogram, discards the specified number of samples
at each end (rounded down), and finds the remaining extrema. Setting both
percentages to zero uses the observed minimum and maximum.

- **Linked RGB** (default) takes the lowest of the three measured black points
  and the highest white point, then applies one curve across all channels.
- **Per channel** stretches red, green and blue independently. This can remove
  a color cast, including one that was intentional. A channel spanning fewer
  than eight sRGB codes retains its original samples.

If all three measured spans are below eight codes, the stage passes the photo
through and reports low contrast. An already full-range correction also passes
through. Measurement uses bounded CPU buffers. The stretch operates on
continuous sRGB values, returns linear float32 pixels, and uses the prepared
ONNX Runtime backend with per-image inputs. Stage blending, source metadata,
geometry and compatible context follow the same contracts as manual Levels.
[Verification](verification/levels.md#auto-levels).

## High Pass and Low Pass

**Low Pass** (`core.low_pass` v1) softens fine texture by applying a Gaussian
blur to each linear-sRGB channel. **High Pass** (`core.high_pass` v1) extracts
the detail removed by the same blur. In Normal blend mode, uniform areas appear
gray. Choose Overlay to sharpen the stage input and use Blending to control
the strength.

Both stages take **Radius (px)** as the Gaussian standard deviation in
full-resolution pixels, from 0.5 to 32 with a default of 3. Larger values
include broader details. The kernel extends three standard deviations on each
side, rounded up, and repeats edge pixels at the photo boundary. High Pass
returns `0.5 + input − blur`, clipped to the working range [0, 1]. The 0.5
midpoint is in linear light so that it is neutral for the pipeline's Overlay
mode. Both stages filter the entire frame using the ONNX Runtime backend
selected during setup (CPU, DirectML or CUDA). They generate the Gaussian graph
from the radius and reuse its session across photos in a staged pass. No
checkpoint or mask is required. They preserve geometry, metadata and upstream
context. The CPU and Radeon 780M DirectML paths have been exercised.
CUDA hardware verification is pending. [Verification](verification/frequency-filter-directml.md#integrated-stages).

## White balance and color cast

`core.white_balance` v1 operates on full-resolution linear-sRGB pixels.
Place it before exposure and analysis. **Automatic** runs on the CPU and estimates red and blue
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
photos apply the same gains on the setup-selected ONNX Runtime backend regardless of composition. **Blending** sets the
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
| White balance and color-cast correction | Corrected pixels | Automatic CPU correction, fixed ONNX Runtime correction, neutral picker and blending. |
| Auto exposure and tone | Corrected pixels and correction diagnostics | Implemented baseline with strength, stop limit, and highlight protection. It measures the current pixels directly. |
| Person and exposed-skin analysis | Whole-frame person foreground and exposed face/body skin grids | Implemented as independent stages. Instance association, overlapping-person quality and representative skin-tone coverage await evaluation. |
| Portrait detection | Context: oriented face boxes; pixels unchanged | YuNet face boxes and minimum confidence implemented. Landmark output and person association remain proposals. |
| Face-region parsing | Context: precise skin, lips, eyes, and teeth masks where supported; pixels unchanged | Per-region confidence, edge treatment, and analysis resolution. Requires faces or a declared self-contained detection implementation. Teeth must have a validated teeth mask; a mouth box is insufficient. |
| Blemish cleanup | Local pixel edits within valid skin regions; optional candidate/debug context | Strength, maximum spot size, protected regions, and model-specific controls. Optional and quality-gated: distinguishing temporary blemishes from permanent features is uncertain. |
| Local skin tone evening | Local color edits within the current exposed-skin mask | Implemented native-resolution CPU correction with strength, neighborhood radius and a per-pixel color shift limit. [Stage details](#local-skin-tone-evening). |
| Skin shine reduction | Local highlight edits within the current exposed-skin mask | Adjustable correction using the prepared ONNX Runtime backend, with a preview of affected pixels. [Stage details](#skin-shine-reduction). |
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
between people. SelfieMulticlass is available for evaluating this coverage.

Red-eye correction is a useful next optional operation, with pupil/candidate masks
and bounded color correction. Eye whitening, denoising,
and sharpening follow demonstrated need. Do not make every possible enhancement
part of the default studio portrait pipeline.

## Exposed Skin Mask

Add **Exposed Skin Mask** anywhere before **Skin tone smoothing (Alibaba)** and
select **Target exposed skin** on the smoothing card. The same mask can also guide
**Local skin tone evening** earlier or later in the pipeline. **Preview** on the analysis stage
shows body skin in green and face skin in magenta. **Preview in pipeline** on
smoothing compares the actual masked edit with its input. Zoom to 100% to inspect
hands, hairlines, clothing edges and overlapping subjects.

The stage classifies the whole oriented frame, independently of face detection.
It stretches sRGB input to the model's 256 × 256 grid and retains the winning
body-skin and face-skin classes. The other four classes receive zero coverage.
It publishes a pooled `skin.exposed` v1 mask tied to the source, current pixel
and geometry revisions, model and producer settings. A second enabled mask stage
replaces the first mask with a new analysis of its input. Staged jobs store the labels losslessly inside the bounded manifest.
Preview and smoothing bilinearly sample the mask at output resolution to feather
its edges. Smoothing evaluates the frame using its usual resize-consent rules
and blends through the union of both skin classes in linear RGB. An empty mask
skips the edit with an image-specific warning. A missing producer blocks the
selected target. Previous pipelines retain their face or whole-frame targeting.

The model is Google SelfieMulticlass float32 v1, converted to ONNX opset 17 by
senty-au. For manual installation, download `onnx/model.onnx` and place it under
`selfie-multiclass-256x256/model.onnx` in the configured checkpoint folder.
Expected SHA-256 is `35ec1ecd9ee7f85073c99c00020b7f6751b69506eeacf683bc8665f6117f85b0`.
The conversion publisher identifies the weights as Apache-2.0. The model targets
selfie framing and has coarse boundaries on small subjects. Fine facial features
and person identities have no separate labels.
[Publisher model card and weights terms](https://huggingface.co/senty-au/selfie_multiclass_256x256-ONNX)
and [local verification](verification/exposed-skin.md).

## Local skin tone evening

Place **Exposed Skin Mask** anywhere before **Local skin tone evening** and
choose **Preview in pipeline**. Inspect a small area of uneven cheek, neck or arm
color at 100%. The stage adjusts local color in face and body skin, preserving
linear luminance at each pixel so shadows, highlights and fine luminance texture
keep their shape. It runs on the CPU at the oriented input resolution and uses
the existing analysis mask. It requires no additional checkpoint.

| Control | Initial setting | Effect |
| --- | --- | --- |
| Strength | 0.5 | Fraction of the bounded correction to apply. |
| Neighborhood radius | 24 px | Distance sampled on each side of the pixel. Increase for broader patches. Range 1–96 native pixels. |
| Maximum color shift | 6% of luminance | Caps each linear RGB channel's change relative to the pixel's luminance before applying strength. |

The reference color comes from nearby, high-coverage skin. Mixed-color areas,
large color differences, deep shadows and near-clipped highlights attenuate the
correction. The mask feathers the result. Fine facial features have no separate
labels and touching subjects may share a neighborhood. Use a smaller radius
where these boundaries matter. The initial settings still need portrait review.

The **areas:skin** input uses the most recent upstream `skin.exposed` v1 mask
for the photo's geometry. For example, exposure can run between mask analysis and
tone evening. An empty mask skips the image-stage edit and reports a warning.
[Verification](verification/skin-tone-evening.md).

## Protect facial features

Add **Face detection (YuNet)** before **Protect facial features**. Use **Preview in
pipeline** on the protection stage to inspect each face. Amber covers the eyes,
eyebrows and outer lip contour (including the mouth opening). A solid amber
rectangle and a warning indicate that the whole detected face is protected.

`analysis.protect_facial_features` v1 runs the Google MediaPipe FaceMesh V2
landmark model on one 256 × 256 sRGB crop per detected face. The square crop
includes a 25% margin on each side of the longer box dimension and extends the
edge pixels where it crosses the photo boundary. Each successful result stores
five polygons in oriented source-pixel coordinates in `face.protected_features`
v1. A low face-presence score, a predicted nose outside the detection, or an
off-image or unresolved contour protects the entire detection box. The preview
and the shine operation rasterize the same polygons. Staged jobs save the
coordinates losslessly with their source, geometry and model provenance.

The checkpoint is the senty-au opset 17 ONNX conversion of the landmark model
from Google's float16 Face Landmarker bundle v1. Place `model.onnx` under
`mediapipe/face_mesh_v2/` inside the configured checkpoint directory. The
expected SHA-256 is `7d6e82dee82a1dca5fbddb282b3cc74571833a530de317fc22ae325c3358beeb`.
Google's model card and the conversion publisher identify the weights as Apache
2.0. Preserve license and applicable notices if redistributing. [Google model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf) · [Conversion and checksum](https://huggingface.co/senty-au/face_landmarks_detector-ONNX) · [Download ONNX](https://huggingface.co/senty-au/face_landmarks_detector-ONNX/resolve/main/onnx/model.onnx?download=true)

## Skin shine reduction

Place **Skin shine reduction** after **Exposed Skin Mask** and open **Preview in
pipeline**. Adjust **Strength** from 0 to 1 (initially 0.5). Select **Show affected
pixels** to highlight edited pixels in pink over the stage input. Choose
**Stage result** to inspect the retouched photo at 100%. The overlay reflects both
Strength and Blending and is available only in the stage preview.

To protect facial details, also place **Protect facial features** upstream.
**Use facial feature protection when available** is on by default. Inside a
reliably located face, the protected features contribute neither an edit nor
reference samples for estimating shine. An uncertain face box excludes all
pixels in it, including where boxes overlap. Outside matched boxes only body
skin is eligible, so missed face detections do not authorize edits to the
face-skin label. With the option off or the protection stage absent, shine
reduction uses the original exposed-skin matte.

The operation estimates reflections on the setup-selected ONNX Runtime backend at
the original resolution on both face and body skin. It builds the graph locally
and reuses it across photos with the same neighborhood radius.
It looks for patches that are brighter and less colorful than nearby skin, as
expected when neutral studio light reflects off the surface. It estimates how
much neutral light to subtract from the linear RGB channels, capped at 30% of
the local pixel luminance at full strength. The surrounding reference covers a
radius of 2.5% of the shorter image dimension, bounded to 8–96 pixels.
Feathered skin coverage, weak reference color and near-clipping reduce the edit.
An empty mask produces a warning. Zero strength and absence of eligible shine
produce a pass-through.

This estimate needs nearby diffuse skin. Broad uniform shine or colored
reflections can be missed, and makeup highlights can be selected. The skin mask
can cover edges of small facial features. Inspect these areas when tuning
strength. Fully clipped highlights contain no recoverable texture.

Choose either operation in the Add menu. Both use the PyTorch device recorded by
setup (CPU or CUDA) for preview and batch inference. When using both,
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
| Blend mode | Normal | Selects how edited pixels combine with the full-resolution input. See [stage controls](pipeline.md#stage-controls-and-model-availability). |
| Blending | 1.0 | Sets the opacity of the chosen blend mode, from 0 to 1. |

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
Run `.venv\Scripts\python.exe -B tools\verify_alibaba_pytorch.py` to check both
stages with the installed weights and synthetic inputs. It prints the actual output
device for smoothing, detection and repair and checks both stages return linear RGB.
The development machine covers CPU. NVIDIA hardware verification is pending.
Both stages produced pixel-identical CPU output to the previous implementation
on the local 572 × 768 study crop. The CUDA configuration was exercised in the
editor, preview and both batch modes with inference mocked.
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

Each operation descriptor declares context it provides, requires or optionally
uses, as well as its pixel and geometry effects. Context entries
record their producer, schema/model version, confidence when meaningful, source
pixel/geometry revisions, and coordinate conventions. Use per-face/person IDs to
associate related regions within the image; these are not identities across photos.

Coordinates are defined against the full-resolution, orientation-normalized image.
Record transforms from detector downscales or face crops explicitly and map results
back to that coordinate frame. Dense masks may use compact resolution if their
transform and resampling rules are explicit. Regions need pixel masks for selective
edits, not only bounding rectangles. Masks are persisted as artifacts rather than
large JSON arrays inside pipeline settings.

Crop, resize, rotation or other geometry changes require newly produced or remapped
context. Photometric edits retain upstream masks, regions and measurements.
For example, a luminance histogram describes the pixels at its producer. Add a
second statistics stage to measure edited pixels and replace the histogram.
Parameter or model changes invalidate the affected producer's cached results
and downstream cache use.

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
IPTC-IIM. Exports retain standard fields from the image, capture, GPS and
interoperability directories and omit all MakerNotes and other private EXIF tags.
Source files without EXIF remain valid inputs. Malformed capture EXIF produces
an explicit error.
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
