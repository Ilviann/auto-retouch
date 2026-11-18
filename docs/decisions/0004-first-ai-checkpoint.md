# First AI checkpoint and session ownership

Register **Person foreground (AI)** as an evaluation operation for the local AI
lifecycle and a potential upstream context producer for skin analysis. The
OpenCV Zoo release publishes a compact FP32 ONNX model for the prepared runtime
and explicitly licenses the files, including weights, under Apache-2.0. A
direct exposed-skin model would be more useful for targeting retouching. The
reviewed Google multiclass candidate publishes a MediaPipe model, which would
need a separate runtime or a validated conversion. The person foreground output
has one foreground class across the image. Person association and exposed-skin
classification remain separate catalog work.

- [OpenCV Zoo PP-HumanSeg model card and weight license](https://huggingface.co/opencv/human_segmentation_pphumanseg#license)
- [OpenCV Zoo PP-HumanSeg model preprocessing and output](https://huggingface.co/opencv/human_segmentation_pphumanseg/blob/main/pphumanseg.py)
- [Google MediaPipe multiclass segmentation model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Multiclass%20Segmentation.pdf)

Download `human_segmentation_pphumanseg_2023mar.onnx`, the **March 2023 FP32**
variant, from the [publisher file page](https://huggingface.co/opencv/human_segmentation_pphumanseg/blob/main/human_segmentation_pphumanseg_2023mar.onnx).
Place it at `models/human_segmentation_pphumanseg_2023mar.onnx` or at the same
relative path under the configured checkpoint folder. The file SHA-256 is
`552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24`.
Use **Recheck** on an existing unavailable card, then enable the operation.
If distributing the asset later, retain the publisher's license terms and
notices. The application does not bundle or download this checkpoint.

## Lifecycle and memory

The operation owns one ONNX Runtime session between `prepare` and `release`.
Sequential execution creates and releases a session for the current image and
stage before advancing. Staged execution creates one session for each pass,
uses it serially for one image at a time, and releases it before switching
stages, exporting, or returning to a paused or canceled job. A resumed pass
loads a fresh session and uses the saved image-stage cursor and artifacts. An
AI preview takes the same workspace runner lock so it cannot add a model
session while a batch is processing in that workspace.

ONNX Runtime opens only the provider selected by setup. GPU sessions disable
CPU execution fallback, and DirectML uses sequential execution with memory
patterns disabled. The CPU arena is disabled and the CUDA arena grows by the
requested size. These settings bound allocation behavior within each session.
The complete GPU working set and driver allocations require hardware
measurements. See the [DirectML configuration](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html#configuration-options)
and [CUDA provider options](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#arena_extend_strategy).

Inference uses one `1 × 3 × 192 × 192` FP32 input. Preprocessing samples only
model-sized arrays from the oriented image and keeps its full-resolution
pixel buffer. The single binary mask takes 36,864 bytes in memory and 49,152
base64 characters in a staged manifest. This fits the existing manifest disk
reservation. Model inference failures leave the affected image failed with a
correlated cause and allow the next image to run. The operation has a fixed
input shape, so it cannot lower its inference size to retry an allocation.

See the [DirectML batch and GPU memory verification](../verification/person-foreground.md).
