# Local skin tone evening verification

On 2026-11-19 the prepared Windows 11 environment ran the registered
`retouch.local_skin_tone_evening` v1 operation after the installed SelfieMulticlass
float32 v1 ONNX opset 17 checkpoint. Mask inference used ONNX Runtime DirectML
1.24.4 on the Radeon 780M. Local color correction used NumPy 2.5.5 on the
Ryzen 7 7840HS CPU under Python 3.14.7.

All seven authorized 4,000 × 6,000 studio JPEGs produced full-resolution
before/after stage comparisons at the initial settings. Each preview had
changed 8-bit display samples. Together they took 30.35 seconds. One original
also completed both batch schedules. Sequential creation, processing and export
verification took 2.75 seconds and staged took 6.08 seconds, including reopening
and hashing the decoded JPEG pixels in each measurement. The exports had
identical decoded samples and dimensions. All seven source file hashes matched
after testing. There were zero failures. Peak process working set across these
previews and batches was 1,140 MiB. VRAM, CUDA and the 500-image workload were
not measured.

Controlled fixtures exercise luminance detail underneath a localized chromatic
patch, limits on individual channels, feathered mask handling, rejection of
strong color boundaries, invalidation, empty-mask diagnostics and frozen jobs.
Tests compare neighborhoods crossing both tile boundaries with a single-tile
reference. A visual comparison of a generated textured swatch showed reduced
localized redness and retained texture. Acceptance on representative group
portraits and the preferred Natural and Beauty settings requires user review.

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_skin_tone_evening.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_exposed_skin.py -k local_tone_evening -v
```
