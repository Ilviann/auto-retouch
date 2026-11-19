# Exposed skin verification

On 2026-11-19 the local ONNX file matched the SHA-256 in the
[stage catalog](../stages.md#exposed-skin-mask). The prepared Windows 11 environment
used Python 3.14.7, ONNX Runtime DirectML 1.24.4 on Radeon 780M, and PyTorch CPU
2.15.0 for Alibaba smoothing. The model opened with the application's DirectML
session configuration and produced finite float32 logits of shape 1 × 256 × 256 × 6.

Seven local 4,000 × 6,000 studio JPEGs produced nonempty masks in the full-resolution
stage preview. All seven contained predicted face-skin cells and six contained
predicted body-skin cells. These counts describe predictions. Skin coverage,
small hands, hairlines, eyes, lips and person overlap need visual review.

Seven native-scale 1,024 × 1,024 detail crops straddling predicted skin
boundaries were processed independently through both real models. At display
precision, all pixels with zero matte coverage matched the input. Three crops
had a visible sample difference and four had none. All seven source hashes
matched after verification.

A separate 2,048 × 2,048 native-scale crop centered on detected facial skin had
68,480 changed display pixels. Sequential and staged jobs reopening the real
models produced identical decoded JPEG samples, with zero failures in two runs.
Together those two crop jobs took 4.07 seconds. Peak process working set,
including the preceding 24-megapixel mask preview, was 1,668 MiB. GPU memory,
the 500-photo workload and NVIDIA hardware remain unmeasured. Natural and Beauty
strengths and multi-person quality await representative review.

The focused tests exercise the editor and executor with controlled logits and
model outputs. Coverage includes distinct overlay colors, disconnected body skin
without a face, protected areas, feathering, explicit model recovery, stale or
missing context, empty-mask warnings in both schedules, diagnostic causes, and
staged pause/resume without re-running analysis. A Qt integration check covers
model installation guidance, the target control and its required input tag.

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_exposed_skin.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_qt_integration.py -k alibaba_inputs -v
```
