# Four CPU portrait stages on the Radeon 780M

On 2026-11-26, four benchmark-only ONNX Runtime DirectML implementations ran against their registered NumPy v1 stages. Fixed white balance saved the most time on both study photos. Auto exposure saved about half a second per photo, including its CPU histogram. The skin stages saved more time on the photo with more exposed skin. Local skin tone evening has an outstanding blend-mode discrepancy.

## Full-stage measurements

Both inputs were 4,000 × 6,000 studio JPEGs. Of the seven authorized shots, A had the smallest fraction of face/body labels in the 256 × 256 exposed-skin mask (0.62%, source SHA-256 prefix `a0e46bda54b7d4b2`). B had the largest (8.44%, `9631f96bedaf8053`). Each had one face with usable protection contours. Each cell gives the median of three runs in seconds followed by the observed range. Speedup is CPU median divided by DirectML median.

| Stage | Photo | NumPy CPU (s) | DirectML (s) | Speedup |
|---|---|---:|---:|---:|
| White balance, fixed | A | 1.504 (1.488–1.571) | 0.274 (0.273–0.305) | 5.49× |
| White balance, fixed | B | 1.578 (1.567–1.628) | 0.288 (0.287–0.301) | 5.47× |
| Auto exposure | A | 0.888 (0.845–0.962) | 0.358 (0.349–0.401) | 2.48× |
| Auto exposure | B | 0.894 (0.872–0.944) | 0.378 (0.376–0.391) | 2.36× |
| Skin shine reduction | A | 0.406 (0.405–0.451) | 0.326 (0.322–0.361) | 1.25× |
| Skin shine reduction | B | 1.529 (1.469–1.540) | 0.645 (0.613–0.651) | 2.37× |
| Local skin tone evening | A | 0.319 (0.315–0.349) | 0.271 (0.267–0.373) | 1.18× |
| Local skin tone evening | B | 1.283 (1.213–1.292) | 0.456 (0.444–0.470) | 2.81× |

Each pair used the same decoded, oriented float32 linear-sRGB input and the same upstream context. The timer covered stage construction, graph creation, fresh session startup, processing, transfers in both directions, output freezing and validation, and release. JPEG decode, upstream mask/landmark inference, output comparison and export were outside the timer. The skin stages included full-resolution matte preparation and empty-tile skipping on both paths. The color stages read 120-row strips. All 48 timed stage executions completed.

Fixed white balance applied +0.25 red and −0.20 blue stops. Auto exposure used its default strength and maximum correction, measured midtones on the CPU, and applied the computed correction on both photos. Skin tone evening used strength 0.5, radius 24 and a 6% maximum shift. Shine reduction used strength 0.5, the automatic 96-pixel radius and the facial protection mask. Both skin prototypes kept the 256 × 256 production tile boundaries and evaluated identical clipped box neighborhoods with separable float32 window sums. Normal blending at full strength was used for timing.

## Automatic white balance

The automatic estimator found insufficient consistent neutrals on all seven study portraits. Both paths returned the original pixels on every run, and the prototype skipped creating a GPU session. Across three runs per photo, the median full-stage time was 2.70 ms for the production path and 2.74 ms for the prototype. Fixed-mode speedups apply when the correction actually runs.

## Output agreement

Each run compared all 72 million linear RGB channel samples and their 8-bit sRGB display conversions. The same results recurred in all three repetitions. Both color stages produced identical 8-bit samples on both photos. For the skin stages, both implementations left every zero-coverage pixel untouched, including areas excluded by the shine protection matte.

| Stage | Maximum absolute linear RGB difference across A and B | Differing 8-bit channel samples on A | Differing 8-bit channel samples on B |
|---|---:|---:|---:|
| White balance, fixed | 5.96 × 10⁻⁸ | 0 | 0 |
| Auto exposure | 1.19 × 10⁻⁷ | 0 | 0 |
| Skin shine reduction | 1.19 × 10⁻⁷ | 0 | 1 |
| Local skin tone evening | 9.54 × 10⁻⁷ | 0 | 23 |

Every differing display sample in Normal mode differed by one code value. The CPU and DirectML versions of local skin tone evening disagreed on whether one pixel on photo B changed at all. The shared non-Normal blender tests that condition exactly. In a separate full-stage Screen comparison at full strength, all three channels of one pixel exceeded the 0.00001 linear tolerance. The maximum difference was 0.250 linear, or 39 display levels. A production implementation needs to resolve the changed-pixel decision before enabling this graph for those blend modes.

## Hardware and scope

Windows 11 build 26300, Ryzen 7 7840HS, Radeon 780M driver 32.0.32015.7029, Python 3.14.7, NumPy 2.5.5, ONNX 1.23.2 and ONNX Runtime DirectML 1.24.4. The graphs used float32 and opset 13. Mask preparation used the installed SelfieMulticlass float32 v1, YuNet 2023mar and FaceMesh V2 models. CPU fallback was disabled. Separate tile/strip profiles recorded only DirectML kernel events (21 for tone evening and one each for shine, white balance and exposure). Full-stage timing used profiling disabled.

Peak process working set during the main comparison was 1,355 MiB, including the decoded source and paired outputs. Separate GPU memory was unmeasured. The timing covers independent stages with fresh sessions on two portraits. Staged-session reuse performance, a full 500-image batch and CUDA hardware remain unmeasured.

## Integrated stages

Auto exposure, Fixed white balance and Skin shine reduction now generate their ONNX graphs locally and execute on the CPU, DirectML or CUDA provider selected during setup. No model files are required for these graphs. Auto exposure retains its CPU histogram. Automatic white balance retains its CPU estimator and correction. Shine reduction retains its protected matte and applies the estimated neutral subtraction at the requested strength. Local skin tone evening continues to use its original implementation.

The registered DirectML stages and the previous NumPy stages each ran once per photo under the same full-stage timing and comparison procedure as above. All 12 stage runs succeeded. Peak working set was 1,352 MiB including comparison buffers. The color graphs processed up to 480,000 pixels per call. Separate profiles of the production graphs showed only DirectML kernels with CPU fallback disabled.

| Stage | A: NumPy → DirectML (s) | B: NumPy → DirectML (s) |
|---|---:|---:|
| Auto exposure | 0.929 → 0.468 | 0.838 → 0.405 |
| Fixed white balance | 1.538 → 0.307 | 1.479 → 0.291 |
| Skin shine reduction | 0.438 → 0.358 | 1.424 → 0.608 |

Both color stages matched the original 8-bit display samples on both photos. For shine reduction, one channel sample on B differed by one display level. Both paths edited exactly the same set of pixels and preserved every pixel outside the protected matte. The largest linear RGB error for any of the three stages was 1.20 × 10⁻⁷ or less.

Generated fixtures exercised the ONNX CPU path, automatic white balance and pass-through cases, bounded pixel chunks, shine across tile boundaries, facial protection, preview and both batch schedules. NVIDIA hardware validation remains pending.
