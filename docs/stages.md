# Initial stage catalog and image state

Auto ReTouch, authored by Neyrome, uses an ordered pipeline whose stages can change
pixels, add or update analysis context, or do both. The following operation set and order
are proposals; models, algorithms, and numeric preset defaults require evaluation.
Analysis outputs must be visible and reusable rather than hidden in a retouching
operation's private state.

## Proposed first catalog

| Operation | Outputs | Main controls and dependencies |
| --- | --- | --- |
| Image statistics | Context: histogram/luminance statistics and clipping warnings; pixels unchanged | Analysis scope and thresholds. Measurements identify the pixel revision they describe. Useful as an initial CPU reference stage and diagnostic overlay. |
| White balance and color-cast correction | Corrected pixels; optional correction measurements | Temperature/tint correction limits and strength. Conservative CPU baseline first; preserve intentional studio lighting when disabled. Uses applicable statistics/context when available. |
| Exposure and auto contrast | Corrected pixels; clipping/tonal diagnostics | Exposure limit, black/white point limits, highlight protection, and strength. Recompute cheap current-image measurements when prior statistics are stale. |
| Person and exposed-skin analysis | Context: person regions/instances and exposed-skin masks across the whole photo; pixels unchanged | Validated body/skin segmentation, confidence and overlap handling, association of visible skin with each person. Required for the confirmed all-skin/multiple-person scope; face parsing alone is insufficient. |
| Portrait detection | Context: face boxes, landmarks, eye/mouth locations and per-face IDs; pixels unchanged | Confidence threshold and minimum face size. Associate faces with person regions when available. Backend/model chosen after evaluation. |
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
operation list. Read and retain source EXIF before processing. Transfer capture
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
provenance record with processing time, tool version, and a compact pipeline identifier/hash using
validated metadata fields, proposed as XMP history. Preserve prior history and
retain a previous Software value in provenance rather than silently discarding it.
Keep full pipeline settings and checkpoint details in job history/diagnostics;
do not embed face coordinates, dense masks, or machine-local paths by default.

Select the metadata adapter only after Python 3.14 and JPEG round-trip validation.
Check capture tags after writing the final file. Required EXIF loss or an inability
to write the processing marker fails export validation rather than silently
producing an apparently successful metadata-stripped result. Report malformed or
unsupported metadata with useful context; handle proprietary MakerNotes and their
offsets explicitly rather than claiming that arbitrary tag reserialization is safe.
Source files without EXIF remain valid inputs; do not invent missing capture data.

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
