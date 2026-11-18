# YuNet for Alibaba face targeting

Use OpenCV YuNet March 2023 FP32 to supply the existing oriented face-region
contract. The ONNX adapter uses the installed runtime and does not introduce
an OpenCV or landmark-processing dependency. The publisher explicitly licenses
all files in the model directory under MIT. [Publisher's model card and license](https://huggingface.co/opencv/face_detection_yunet#license).

Install `face_detection_yunet_2023mar.onnx` at the root of the configured
checkpoint folder, by default `models/face_detection_yunet_2023mar.onnx`.
The expected SHA-256 is
`8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.
An existing unavailable card needs **Check required model files** followed by
an explicit enable. [Publisher's file and checksum](https://huggingface.co/opencv/face_detection_yunet/blob/main/face_detection_yunet_2023mar.onnx).

## Local verification

- Verified the installed weight hash and exercised the real DirectML session.
  The stage preview returned one box on each of seven 4000 × 6000 studio JPEGs
  at confidence 0.80. At 0.85 it returned boxes on three. These measurements
  informed the provisional default. The comparison sheet stays outside source
  control in `.test-work/yunet-study-preview.jpg`.
- A local 800 × 600 composite made from two study portraits produced two boxes.
  Both crops changed in the real Alibaba CPU preview, and every displayed RGB
  pixel outside their union retained its input value. Sequential and staged
  worker processes exported identical decoded pixels for the white balance,
  detection and retouch pipeline.
- Automatic white balance declined to apply a correction to all seven study
  photos. Generated neutral ramps with warm, cool and green casts tested
  automatic correction. A local before/after swatch comparison showed the cast
  removed from neutral patches. Separate tests covered ambiguous backgrounds,
  sampled corrections, immutable metadata, highlight protection and blending.
  Both workers reproduced the same fixed correction and preserved capture EXIF.
- Focused headless and Qt tests covered sampling via the input pane, saving
  settings, missing and incorrect model files, explicit re-enable, confidence
  filtering, overlap suppression, exclusion of padding, empty detections,
  region serialization, and preserved inference failures.
- A 24-pixel margin on a local study portrait matched the full-resolution box
  coordinates in preview. With face targeting enabled, Alibaba changed pixels
  only inside the expanded box. Clearing the checkbox also produced edits
  outside that box. Sequential and staged workers produced identical decoded
  exports for each checkbox setting. Regression checks cover edge clipping,
  overlapping expanded boxes, zero detections, oversized whole-image consent,
  and the saved settings and checkpoint identities of earlier pipelines.

Real group scenes with occlusion, visual acceptance of retouched faces,
exposed-skin coverage, and NVIDIA hardware acceptance remain open.
