# FaceMesh V2 for facial feature protection

Use the landmark network from Google's MediaPipe Face Landmarker float16 bundle
v1 with the existing YuNet detector. Its eye, brow and outer lip contours supply
the geometry needed for shine protection. The existing exposed-skin model
remains responsible for face and body skin selection. The ONNX conversion runs
through the application's existing stage-owned sessions and requires one
manually provisioned file. The full MediaPipe task bundle also contains its own
detector and expression model. Model and conversion terms, the filename, and
the SHA-256 are recorded in the [stage catalog](../stages.md#protect-facial-features).

## Local verification on 2026-11-19

- The installed ONNX file matched the published SHA-256. The prepared Windows
  environment (Python 3.14.7, ONNX Runtime DirectML 1.24.4) on the Radeon 780M ran YuNet and FaceMesh with CPU
  fallback disabled. On seven 4,000 × 6,000 study JPEGs YuNet supplied one
  detection each. Six produced five protected polygons each. One triggered the
  conservative whole-box exclusion. The seven preview overlays are available
  locally in `.test-work/facial-feature-review.jpg` for visual review.
- On one of the six portraits, 41,206 pixels lay inside the protection polygons.
  Shine reduction at strength 0.5 changed 1,660 of those pixels with protection
  off and zero with it on, measured before JPEG export using the affected-pixel
  preview. The protected run still edited 216,717 pixels across the photo.
- The four-stage pipeline completed one sequential job and one staged job on
  that portrait. Pausing after the protection checkpoint and resuming restored
  its contours without a second FaceMesh inference. Both verified JPEG exports
  decoded to identical RGB samples. The input SHA-256 matched after the runs.
- Focused tests cover multiple detections, missing faces, fallback regions,
  stale coordinates, missing or corrupt checkpoints, manual re-enabling,
  the desktop card, legacy shine checkpoint identity and lossless mask replay.

These checks establish execution and pixel exclusions. Visual acceptance on
profiles, glasses, groups and small faces, minimum 6 GB NVIDIA hardware, peak
device memory and the 500-photo workload remain open.
