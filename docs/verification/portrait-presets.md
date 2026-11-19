# Natural and Beauty preset verification

On 2026-11-19 both shipped JSON files loaded through the production editor on
Windows 11 with Python 3.14.7, NumPy 2.5.5, ONNX Runtime DirectML 1.24.4 on
the Radeon 780M and PyTorch CPU 2.15.0 on the Ryzen 7 7840HS. Both used the
installed SelfieMulticlass float32 v1 opset 17 model. Beauty also used the
Alibaba smoothing checkpoint from revision `16c8351a`.

- A launch override opened Natural. Selecting Beauty and switching back in the
  Qt preset picker displayed six and four ready stages respectively.
- Each preset rendered a native-scale 1,536 × 1,536 face-centered crop from each
  of the seven authorized studio JPEGs. Both edited all seven crops. The paired
  display pixels differed on every crop. Natural also rendered one 4,000 × 6,000
  original. Beauty preflight requested resize approval for that original.
- One crop ran through sequential and staged export for each preset. All four
  jobs completed. Decoded JPEG pixels matched between schedules for each preset.
  All seven original file hashes matched after verification.
- The combined local model check took 64.78 seconds and reached a peak process
  working set of 1,609 MiB, including crop selection, previews and batch runs.
  GPU memory, CUDA and the 500-photo workload were not measured.
- Three focused tests cover discovery, parsing, required mask dependencies,
  missing-checkpoint disable and explicit recovery, editing, saving a separate
  copy, and restoration of working changes.

These checks establish functional behavior. Acceptance of skin texture,
permanent features, makeup, varied complexions and overlapping subjects remains
pending photographer review.

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_portrait_presets.py -v
```
