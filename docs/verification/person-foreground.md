# First AI operation verification

On 2026-11-18, the publisher SHA-256 matched the local OpenCV Zoo PP-HumanSeg
March 2023 FP32 ONNX checkpoint. Testing used Windows 11, Python 3.14.7,
ONNX Runtime DirectML 1.24.4, and the development machine's Radeon 780M.
A profiled generated-image run executed model kernels on `DmlExecutionProvider`.

## Batch and memory results

Both schedules ran in fresh local workers using the seven study JPEGs at
4,000 × 6,000 and a pipeline containing two instances of the AI operation.
Each schedule performed 14 inferences with zero failures. Reopened exports
matched in decoded pixels across schedules, and all source SHA-256 hashes
remained unchanged. Every image produced a nonempty foreground mask.

| Schedule | Sessions opened and released | Elapsed | Peak worker working set | Peak committed process memory |
| --- | ---: | ---: | ---: | ---: |
| Sequential | 14 | 23.03 s | 914 MiB | 1,417 MiB |
| Staged | 2 | 44.01 s | 868 MiB | 1,355 MiB |

Times are from the memory-instrumented runs and cover worker start through
completion. Each worker read its process peak counters with
`GetProcessMemoryInfo`. DXGI `QueryVideoMemoryInfo` sampled process usage after
session creation, after inference, and immediately before and after release.
For both schedules, local GPU usage was about 39–40 MiB before release and
7–8 MiB after, with zero non-local usage at those sample points. Once the
sequential run reached 8 MiB after release, its remaining releases stayed at
that level. These boundary samples do not measure transient in-kernel peaks.
Verification used separate temporary destinations and removed its local outputs.

## Focused regression checks

The scaling settings change also ran the verified local PP-HumanSeg model with
a generated 401 × 96 RGB input in the prepared DirectML environment. Both
bilinear and nearest-neighbor preprocessing produced finite 1 × 2 × 192 × 192
outputs, and changing filters changed the output scores. Headless and Qt checks
exercise inheritance, overrides, portable JSON, preview and frozen job settings.
The synthetic Alibaba crop checks in `tests/test_retouch_scaling.py` exercise
geometry and approval separately from neural inference.

Generated fixtures covered missing checkpoint disable and explicit recovery,
preview overlay coordinates, the workspace lock, one live session at a time,
staged pause and resume from saved masks, and an inference allocation failure
that leaves the next image runnable. Additional checks covered Qt stage cards
and preview dialogs, lossless artifact compatibility, and headless imports.

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_person_foreground.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_preview.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_desktop_batch.py -v -k preview -k modal_stage_comparison
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_qt_integration.py -v -k model_card_disable
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_artifacts.py -v -k context -k collect
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_jobs.py -v -k staged_passes_match -k staged_cancel_releases -k stage_failure_releases -k runner_lock
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_pipeline.py -v -k prepare_process_release -k static_pixel_and_geometry_context
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -v -k static_qt_import_boundary -k headless_import_boundary
```

Representative visual review, exposed-skin targeting, a 500-photo workload,
and 6 GB NVIDIA CUDA acceptance remain pending.
