# High Pass and Low Pass on the Radeon 780M

On 2026-11-26, a benchmark-only DirectML implementation and the original NumPy
stages processed the same local 4,000 × 6,000 studio JPEG (SHA-256 prefix
`8bfb36b135872930`). The app decoded the photo once to oriented float32
linear sRGB. Each filter read that same immutable input in Normal mode at
full blending. All 24 full-photo stage runs completed successfully.

## Full-stage times

Each cell gives the median of three elapsed times in seconds, followed by
the observed range. The timer covered stage construction, preparation,
pixel processing, freezing and validating the output, and release. Each
DirectML run created a fresh session. Timings include host-side tile packing,
GPU transfers and readback. Python startup, JPEG decoding, export and the
subsequent pixel comparison were outside the timer.

| Filter | Radius | Original NumPy CPU, s | DirectML prototype, s | Speedup |
| --- | ---: | ---: | ---: | ---: |
| Low Pass | 3 px | 1.252 (1.241–1.302) | 0.758 (0.742–1.007) | 1.65× |
| High Pass | 3 px | 1.565 (1.545–1.599) | 0.783 (0.777–0.820) | 2.00× |
| Low Pass | 32 px | 8.834 (8.750–8.842) | 1.528 (1.526–1.539) | 5.78× |
| High Pass | 32 px | 9.060 (9.056–9.139) | 1.559 (1.552–1.584) | 5.81× |

## Output agreement

Every run compared all 72,000,000 RGB channel samples, including photo and
tile boundaries. The absolute tolerance was 0.00001 on the 0–1 scale. Zero
samples exceeded it. The error measurements below were identical across all
three repetitions. Source metadata, dimensions, precision and revision
checks also passed.

| Filter | Radius | Maximum absolute error | Mean absolute error | Channel samples differing after 8-bit sRGB conversion |
| --- | ---: | ---: | ---: | ---: |
| Low Pass | 3 px | 3.58 × 10⁻⁷ | 1.88 × 10⁻⁸ | 240 |
| High Pass | 3 px | 3.28 × 10⁻⁷ | 1.95 × 10⁻⁸ | 245 |
| Low Pass | 32 px | 9.54 × 10⁻⁷ | 5.52 × 10⁻⁸ | 766 |
| High Pass | 32 px | 9.54 × 10⁻⁷ | 5.52 × 10⁻⁸ | 657 |

Every differing 8-bit channel sample differed by exactly one code value.
The comparison used the application's sRGB conversion before any lossy encoding.

## Implementation and hardware

- The CPU ran the registered v1 NumPy stages, which accumulate in float64
  with 256 × 256 output tiles. The prototype used two separable float32
  depthwise ONNX convolutions with 1,024 × 512 output tiles. Both used
  identical Gaussian support and replicated photo borders. High Pass
  subtracted the blur, added 0.5 and clipped on the device.
- Profiling a tile of each of the four graphs reported one fused kernel on
  `DmlExecutionProvider` and zero CPU kernels. CPU fallback was disabled for
  profiling and timing. Full-photo timings used profiling disabled.
- Windows 11 build 26300, AMD Ryzen 7 7840HS, Radeon 780M driver
  32.0.32015.7029, Python 3.14.7, NumPy 2.5.5 and ONNX Runtime DirectML
  1.24.4. The generated graphs used ONNX opset 13 with fixed Gaussian weights.
- The benchmark process peaked at 1,279 MiB of resident memory, including the
  decoded source and paired outputs for comparison. Separate GPU memory usage
  was unmeasured. These timings cover a single photo on this machine.

## Integrated stages

The registered v1 filters now run their generated Gaussian graphs on the
setup-selected ONNX Runtime provider. The implementation expresses each Gaussian
pass as a correction to the center sample using weighted adjacent differences.
This keeps constant neighborhoods exact in float32 and preserves the shared
blend modes' treatment of unchanged pixels. A stage pass reuses one session,
including for photos with different dimensions.

One additional full-stage run per case on the same photo and hardware compared
the integrated DirectML stage with the original NumPy CPU stage. All eight
runs succeeded. The process peaked at 1,279 MiB resident including reference
outputs. Separate tile profiles of all four production graphs reported only
`DmlExecutionProvider`. CPU fallback was disabled.

| Filter | Radius | NumPy CPU | Integrated DirectML | Maximum absolute linear RGB error |
| --- | ---: | ---: | ---: | ---: |
| Low Pass | 3 px | 1.369 s | 0.892 s | 1.79 × 10⁻⁷ |
| High Pass | 3 px | 1.617 s | 0.943 s | 1.35 × 10⁻⁷ |
| Low Pass | 32 px | 9.376 s | 1.665 s | 5.97 × 10⁻⁷ |
| High Pass | 32 px | 9.203 s | 1.695 s | 5.17 × 10⁻⁷ |

ONNX Runtime CPU execution was checked with generated fixtures, including a
fractional radius, an impulse across tile boundaries, a one-pixel frame,
clipping and stage blending. DirectML stage and pipeline previews and both
worker schedules were exercised with generated JPEGs. The schedules produced
identical decoded exports. CUDA routing was checked with a simulated session.
CUDA hardware execution and its numerical accuracy remain pending.
