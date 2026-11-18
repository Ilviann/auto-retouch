# Runtime foundation dependencies

Date: 2026-11-17. Status: adopted for foundation development, with CUDA and actual
portrait-model acceptance pending. User authorized implementation on this date.

## Choice and alternatives

Use standard x86-64 CPython 3.14 (tested 3.14.7), with a project-local editable
application installation. Use PySide6 Essentials 6.12.0 and shiboken6 6.12.0 for
Qt Widgets. Essentials provides the required PySide6 namespace and Widgets module
without installing unrelated Qt Addons/WebEngine/PDF components. The full PySide6
metapackage was considered but adds unused modules to this initial adapter.

Use ONNX Runtime 1.24.4 as the foundation inference candidate in three mutually
exclusive profiles: `onnxruntime`, `onnxruntime-directml`, and `onnxruntime-gpu`.
All have standard CPython 3.14 Windows AMD64 wheels. Keeping one version across
profiles simplifies provider comparisons. This does not select an ONNX portrait
engine or require future models to use ONNX. Native model adapters remain subject
to the model-format, quality, commercial-use, and hardware gates.

Shared and profile locks under `requirements/` pin the complete resolved dependency
set, including build tools/pip, with the SHA-256 of each selected Windows/universal
wheel. Setup uses `--require-hashes --only-binary=:all: --no-deps`, followed by an
editable local installation with no dependency resolution/build isolation. Setup
checks every locked version and runs `pip check`; launch checks locked versions
without installing anything. See the locks for exact transitive versions.

Setup records its profile, application version, preparation time, Python version,
and a hash of the shared/profile lock files in `.venv/retouching-backend.json`.
It removes readiness before every update and removes conflicting ORT variants
before installing the selected one. There is no application/backend override.

The provider probe constructs an original, weight-free ONNX graph in memory:
float32 vectors `[1,2] + [3,4] -> [4,6]`, IR 8/opset 13. Profiling must show the
selected provider executing the node; GPU probes disable CPU EP fallback. DirectML
uses sequential execution and disables memory patterns. Sessions are local to the
probe and receive one call. No photos, portrait data, or checkpoints are used.

## Evidence and limits

On 2026-11-18 the user confirmed Python 3.14 and independent setup selections for
ONNX Runtime and PyTorch. The development pair is ONNX Runtime 1.24.4 DirectML
and PyTorch 2.15.0 CPU. The CUDA PyTorch lock selects the Windows CPython 3.14
CUDA 12.6 build of 2.14.1 for the CUDA 12 family used by the ONNX GPU package.
CUDA execution and RTX 20-series support still require hardware validation.
The [runtime contract](../runtime.md#backend-selection-during-setup) describes
the two prompts, updated readiness metadata and per-stage device selection.

[Milestone verification](../verification/runtime-foundation.md) records real CPU
and DirectML probe execution, Qt imports/window behavior, clean/repeated setup,
backend switches, and startup failures. Main application development on this
machine uses DirectML. Setup tests/switches are reserved for setup improvements
or bug fixes, per the user's 2026-11-17 instruction.

CUDA package resolution is verified, but imports/inference, CUDA/cuDNN libraries,
driver compatibility, 6 GB behavior, and minimum NVIDIA hardware acceptance are
pending. Setup must fail clearly on an unusable CUDA environment and must not
mark it ready. No NVIDIA throughput, memory, or stability result is claimed.

## Commercial use and redistribution inventory

This milestone adopts dependency definitions, not redistributed portrait weights.
The following license information comes from the selected wheel metadata and
bundled notices. Preserve those notices and audit the actual packaged files again
before distribution. Paid photography use does not remove distribution obligations.

| Packages | Recorded terms / distribution considerations |
| --- | --- |
| PySide6 Essentials, shiboken6 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only. Plan the LGPL route for dynamically loaded Qt libraries; retain licenses/notices and satisfy replacement/relinking/source obligations for the shipped build, or obtain a Qt commercial license. No bundled release is cleared by this milestone. |
| ONNX Runtime CPU/DirectML/GPU 1.24.4 | MIT; preserve upstream license and bundled third-party notices. DirectML/CUDA/native binary terms must be audited for the actual distribution. NVIDIA CUDA/cuDNN redistributables are not provisioned or licensed by this repository. |
| numpy 2.5.5 | Wheel metadata: BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0. Include bundled third-party notices. |
| protobuf 7.36.2 | BSD 3-Clause; preserve notices. |
| flatbuffers 25.12.19 | Apache 2.0; preserve license/notices. |
| sympy 1.14.0, mpmath 1.3.0 | BSD; preserve package and bundled notices. |
| packaging 26.3 | Apache-2.0 OR BSD-2-Clause. |
| pip 26.3, setuptools 84.2.1, wheel 0.48.0 | MIT plus their bundled third-party notices. These are environment/build tools, not model assets. |

No application-source license or portrait-checkpoint license is inferred here.
Model weights still need independent commercial-use and redistribution evidence
before adoption. The generated arithmetic probe has no trained weights.

Primary package/provider references (checked 2026-11-17):

- [PySide6 Essentials release](https://pypi.org/project/PySide6-Essentials/6.12.0/)
  and [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html).
- [ORT CPU](https://pypi.org/project/onnxruntime/1.24.4/),
  [ORT DirectML](https://pypi.org/project/onnxruntime-directml/1.24.4/), and
  [ORT GPU](https://pypi.org/project/onnxruntime-gpu/1.24.4/) release files.
- [DirectML execution provider](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)
  and [CUDA execution provider requirements](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).
