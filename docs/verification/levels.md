# Levels verification

RGB Levels exceeded the requested 1.5× speedup on both local 24 MP portraits.
The implemented stage uses a built-in ONNX graph on the provider selected during
setup. A single-channel edit sends only that channel to the provider. Neutral
settings bypass graph creation and return the original pixels.

## NumPy and ONNX Runtime DirectML

On 2026-11-26 the two implementations processed the same oriented, decoded
4,000 × 6,000 float32 images on a Ryzen 7 7840HS with Radeon 780M graphics.
Photo A has source SHA-256 prefix `a0e46bda54b7d4b2` and photo B has prefix
`8bfb36b135872930`. Python was 3.14.7, NumPy 2.5.5 and ONNX Runtime DirectML
1.24.4. The Levels v1 graph uses ONNX opset 13 and has no model weights.

Times are median seconds across three paired trials. The processing order
alternated between trials. Each trial timed a fresh stage through its first
result, then a second image-stage call using the same session. Both timings
include buffer allocation, transfers where applicable, output freezing and stage
validation. JPEG decode, result comparison, export and session teardown were
outside the timers. The NumPy reference used vectorized 32-row strips. The ONNX
version used up to 480,000 pixels per call, with CPU fallback disabled.

| Photo | Channel | Session | NumPy CPU | ONNX DirectML | Speedup |
| --- | --- | --- | ---: | ---: | ---: |
| A | RGB | Fresh | 2.724 s | 1.024 s | 2.66× |
| B | RGB | Fresh | 1.797 s | 0.474 s | 3.79× |
| A | RGB | Reused | 2.768 s | 0.865 s | 3.20× |
| B | RGB | Reused | 1.785 s | 0.447 s | 3.99× |
| A | Red | Fresh | 0.757 s | 0.517 s | 1.46× |
| B | Red | Fresh | 0.627 s | 0.320 s | 1.96× |
| A | Red | Reused | 0.762 s | 0.498 s | 1.53× |
| B | Red | Reused | 0.634 s | 0.299 s | 2.12× |

Input endpoints were 16 and 235, gamma was 1.2, and output endpoints were 8
and 246, with full Normal blending. All 48 timed executions completed. Each
paired trial compared all 72 million channel values from the reused-session
results. The maximum absolute linear RGB difference was 2.39 × 10⁻⁷ or less,
and every converted 8-bit sRGB sample matched. Peak process working set was
1,236 MiB including the source and comparison outputs. Separate GPU memory usage
was unmeasured. The fresh single-channel result on photo A fell below 1.5×.
A separate 256 × 16 synthetic check also favored NumPy because session and
transfer overhead dominated at that size. CUDA hardware acceptance and
500-photo batch timings remain pending.

The benchmark accepts a local JPEG. Run it from the project root in CMD.

```bat
set PYTHONPATH=src
.venv\Scripts\python.exe -B tools\benchmark_levels.py "D:\path\to\photo.jpg"
```

## Behavior checks

- ONNX Runtime CPU tests covered neutral and disabled settings, input clipping,
  gamma in both directions, adjusted, reversed and flat output ranges, isolated
  color channels, fractional input codes and blending across chunk boundaries.
  Geometry, immutable source metadata and compatible upstream context survived.
  Invalid input ranges were rejected transactionally. A simulated provider
  failure retained the traceback and image, job and stage identifiers.
- An offscreen Qt check found Levels by name in **Add operation**, added the
  stage and edited its channel and gamma in the stage preview controls. Generated
  JPEGs exercised the stage preview, whole-pipeline preview and both worker
  schedules on DirectML. Sequential and staged exports matched and retained
  camera EXIF.
- Visual inspection of a generated grayscale ramp and color swatches showed
  deeper shadows and brighter highlights with narrowed input points, lifted
  midtones with gamma 1.8, and a blue shadow tint with a raised blue output
  black point.

## Levels editor

The desktop integration check selected a synthetic source thumbnail, cleared
and restored its histogram, dragged the gamma handle, switched to the green
histogram, and picked input black and white points. It checked rejection of
coincident input points, crossed the output handles, and saved the preview
settings back to the stage card. The headless preview check verified full-image
bin counts before and after upstream exposure and histogram reuse across edits
and display modes. The desktop layouts were inspected offscreen with synthetic
images.
