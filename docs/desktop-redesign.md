# Desktop workspace redesign proposal

Saved on 2026-11-18 for a later pass on milestone 5. The design centers on two
photographer tasks: choosing images from a shoot and judging a change on one
portrait. The current desktop already supports indexing, thumbnails, pipeline
editing, fitted previews, preflight and saved jobs. This proposal describes how
to bring those workflows together as further capabilities arrive.
[Current workspace](verification/desktop-workspace.md)

The confirmed product constraints include PySide6 Qt Widgets on Windows 11,
local processing, Natural and Beauty presets, all exposed skin across multiple
people, whole-pipeline and individual-operation previews, and both batch
schedules. JPEG is the initial export format. WebP, JPEG XL and AVIF come later.
The layout, shortcuts and rollout below are proposals. Per-photo overrides
and queued GPU preview are proposed capabilities. Preset
strengths, production models and acceptable portrait results still need
evaluation. [Product requirements](requirements.md)

## Workspace and Jobs

**Workspace** holds the current sources, photo selection, editable pipeline and
batch settings. **Contact sheet** and **Inspect** share that state. Switching
between them preserves the viewed photo, the grid position and batch inclusion.
The pipeline inspector stays alongside either view. A persistent footer offers
**Review batch**. **Start processing** opens the processing modal. The workspace
also provides access to saved jobs, Settings and Diagnostics.

- **Contact sheet.** Give the viewed tile a clear outline and batch inclusion
  its own fixed-corner checkbox. Show counts as “10 of 11 included.” Provide
  bulk include/exclude actions and a filter for unreadable files. A photo can
  be viewed while excluded, or exported successfully and still flagged for
  visual review. Track each of those states independently. Keep thumbnail
  decoding and caching bounded for a shoot of approximately 500 JPEGs.
- **Inspect.** Double-click a tile to inspect it and run its pipeline in the
  preview window. Compare side by side or press Space to switch between
  input and result at the same zoom and position. Scroll to zoom, drag to pan
  both panes, use **100%** for full-resolution detail or **Fit** to recenter.
  Proposed navigation adds arrow keys to step through photos.
  Choose preview scope independently of the
  comparison and overlays. Whole pipeline uses enabled stages in order.
  Operation only runs the selected available stage on the original, including a
  manually skipped stage. The stage card's **Preview in pipeline** comparison runs
  earlier enabled stages to supply that stage's input and context. Explain missing
  dependencies on the affected operation. Original viewing and memory-only
  processing previews are available after indexing.
  [Preview semantics](pipeline.md#preview-meaning)
- **Analysis and detail.** Present measurements, masks and landmarks according
  to the selected stage's outputs. For a group portrait, show skin coverage on
  hands, arms and legs as well as faces, preserving each person's association.
  A valid empty detection and a missing dependency need different feedback.
  Align overlays to the oriented image at both Fit and 100%. Previews use a
  small overview for reduced views and draw full-resolution input and result
  detail at one image pixel per physical display pixel at 100%. [Stage catalog](stages.md)
- **Pipeline inspector.** Show ordered, compact rows with an enable control,
  stage name, state and a useful parameter value. Expand the selected row to
  edit parameters or inspect inputs and outputs. Keep add, remove, reorder,
  repeated instances, JSON open/save and last-used restoration available.
  Present Natural and Beauty as editable presets when their definitions are
  ready. If per-photo overrides are introduced, label the scope before editing
  and carry it through selected-image reprocessing.
- **Missing models.** Keep the error visible on the collapsed stage. Distinguish
  manually off, unavailable and blocked by a missing producer. Expand the error
  to see the publisher link, model version, exact filenames and expected local
  paths. After manual installation, **Recheck** validates the files and leaves
  the stage off until explicitly enabled. Preserve its parameters. Name any
  downstream operation whose required input is missing.
  [Stage controls and availability](pipeline.md#stage-controls-and-model-availability)
- **Batch review.** Collapse source controls to a compact summary after indexing,
  with folder/file selection, recursion and rescan available on demand. Keep the
  included count, destination, export summary and primary action visible outside
  scrolling panels. Review shows enabled and skipped operations, schedule,
  and required/free space per volume. Check output filenames at Start. Keep temporary and
  output estimates visible from indexing onward and sum them on shared volumes.
  Show a specific remedy for a blocker. Offer **Start processing** when preflight
  succeeds. Put JPEG compression controls in a format-specific Export section.
  Every future exporter must preserve capture metadata, add tool provenance and
  verify the output before counting it as complete.
- **Jobs.** Show progress and Pause, Continue and confirmed Break in the processing
  modal, along with the frozen pipeline, recoverable work, failures
  and verified exports. In sequential mode, identify the current photo. In
  staged mode, identify the operation and progress through its pass, with a
  separate exported count. Future edits apply to the editor and future jobs.
  If GPU previews can queue during a batch, show their queued state and respect
  the worker's image or stage-pass boundary.
- **Settings and diagnostics.** Show the active configuration file, effective
  model/temp/log paths and logging options, with validated Save and Cancel.
  Display the installed backend read-only. Open searchable diagnostics in the
  context of the affected job, photo or stage. Support copyable details and a
  local redacted report that excludes photos and weights by default. Keep a
  logging failure visible. [Runtime configuration](runtime.md#machine-local-configuration)
  and [diagnostics](diagnostics.md)

## Visual direction and implementation order

Use a neutral gray surround for the photograph, Segoe UI, system light/dark
colors and a restrained accent for focus, selection and the primary action.
Use spacing and short headings to group controls. Pair status colors with text
or icons. Put the Neyrome credit in About and routine folder configuration in
Settings. Keep warnings that require action beside the affected control.

1. Start with compact stage rows, distinct view/inclusion indicators and a
   persistent batch footer. Reuse the current editor, thumbnail and preflight
   services. Check that the primary action and photo navigation remain reachable
   in compact windows.
2. Introduce the shared contact sheet and inspection view. The Qt adapter can
   switch views and own the image canvas. Keep stable photo, stage-instance,
   preview-request and job identities in plain Python. Discard stale preview
   results after a photo or settings change. Generate controls from descriptors
   and render outputs by type so new operations fit the same shell. Reuse
   the preview service's full-resolution input and result for 100% inspection.
   [GUI boundary](architecture.md#gui-boundary)
3. Connect the dedicated Jobs, Settings and Diagnostics views to their
   application services. Add analysis overlays, per-photo overrides, queued
   previews and later export formats as their respective contracts and
   implementations become available.

## Walkthroughs for implementation review

1. Inspect two people in a group portrait, verify skin coverage on a hand,
   compare the same detail at 100%, then return to the same grid position
   with batch inclusion preserved. Check the controls at 125% and 150% Windows
   display scaling. Use representative portraits for visual acceptance.
2. Restore a pipeline with a missing model. Find the affected stage and its
   blocked consumer, install the named files manually, recheck and explicitly
   enable the recovered stage. Confirm the batch review reflects the resulting
   enabled sequence.
3. Open an interrupted staged job, identify its pass and completed exports,
   resume it, and trace a failed photo to its diagnostic. Return to Workspace
   with the current editor session intact.
