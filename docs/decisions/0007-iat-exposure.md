# IAT Exposure for adaptive exposure correction

The optional stage uses the `eleasm/IAT-ONNX` conversion of the authors’
`best_Epoch_exposure.pth`. It estimates spatially varying exposure changes.
The existing Auto exposure and tone stage provides a whole-image tone curve
for comparison. Running IAT on a bounded, aspect-preserving guide gives the
global branch the entire composition and limits the size of neural activations.
The 512-pixel default is an evaluation setting. The native-resolution transfer
of the estimated correction is an Auto ReTouch algorithm.
[Original project](https://github.com/cuiziteng/Illumination-Adaptive-Transformer) · [Stage behavior](../stages.md#adaptive-exposure-correction-iat)

## Checkpoint and rights

The ONNX model publisher declares Apache-2.0 on the model card covering these
converted weights. The original project also publishes its code under Apache-2.0.
Installation is manual. Any future redistribution must meet the license and
notice obligations. The pinned revision is `6a2bfefd823bbe167fbbfcba13296899ea398afc`.
[Pinned model card](https://huggingface.co/eleasm/IAT-ONNX/blob/6a2bfefd823bbe167fbbfcba13296899ea398afc/README.md) · [Published file revision](https://huggingface.co/eleasm/IAT-ONNX/commit/6a2bfefd823bbe167fbbfcba13296899ea398afc)

| File under `iat-exposure/6a2bfef/` | SHA-256 |
| --- | --- |
| `iat_exposure.onnx` | `bd16c959a336b99ff15bef071ad6e9b081017241e5575d9e391653b676f2a5c6` |
| `iat_exposure.onnx.data` | `f92c0ab92266a096d117d3747fbbce244ca91e8a3f28f70db5b2048f478418a8` |

## Development verification

On 2026-11-26 both local files matched the pinned hashes. Verification used
Python 3.14.7, ONNX Runtime 1.24.4 DirectML and the Radeon 780M. The stage opens
one session for a concrete batch, height and width, reuses it at that analysis
size and releases it before a size change. The dynamic graph required CPU
partitioning on DirectML until these dimensions were specified. A profile of
the specialized graph recorded only `DmlExecutionProvider` node executions.
CPU and CUDA use the same implementation. Actual IAT runs on CPU and on the
6 GB NVIDIA target remain pending.

- Three 4,000 × 6,000 study portraits completed standalone previews at the
  default 341 × 512 analysis size and full Normal blend, with zero failures.
  IAT previews took 4.0–5.1 seconds each. The process peaked at 1,097 MiB
  working set during the comparison run. GPU memory was not measured.
- At thumbnail size, median 8-bit luminance fell by 39–50 levels in the three
  IAT results and by 11–12 levels with default Auto exposure and tone.
  The comparison covers three original frames from one series. Intentional
  side lighting, suitability for paid work and preferred blend need visual review.
- For a controlled exposure check, one study portrait was reduced to 341 × 512
  and shifted in linear light. With −2 EV, IAT reduced the mean absolute sRGB
  error against the unshifted portrait from 0.222 to 0.098. With +1 EV, the
  error fell from 0.134 to 0.085. On the unshifted frame IAT introduced a
  mean error of 0.118. Values are on a 0–1 scale.
- Separate sequential and staged jobs each exported one 4,000 × 6,000
  portrait with zero failures. Decoded JPEG samples and EXIF agreed. In the
  export comparison run, sequential took 4.6 seconds and staged took 5.1 seconds.
  The shared process peaked at 1,022 MiB. The 500-photo workload remains pending.
- Focused tests cover both-file validation, recovery with retained settings,
  the Qt stage card, float color conversion and blending, signed spatial
  corrections, session reuse across shapes and correlated inference failures.
