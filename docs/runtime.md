# Runtime and launch contract

The application targets Windows 11 only and runs on the user's system-wide
Python 3.14 through a project-local
virtual environment. Milestone 1a implements the script names, flags, configuration,
environment metadata, and startup skeleton below. Milestone 3a adds registered
pipeline validation and reference-stage cards. The headless application service
supports sequential and staged job execution and recovery, including execution in
a worker subprocess. The worker uses the environment interpreter and both backends
recorded by setup. The Qt desktop window exposes batch setup and
saved-job controls. The foreground and Alibaba retouching stages resolve manually
installed, checksum-verified checkpoints from the configured model folder. See the
[runtime evidence](verification/runtime-foundation.md) and
[desktop batch evidence](verification/desktop-batch.md).

The 3b artifact slice adds pinned `tifffile==2026.10.15` to shared dependencies
for uncompressed float32 intermediate TIFF. The user explicitly authorized this
dependency addition to the main `.venv`; DirectML selection and packages were
retained. No preparation/backend probe was rerun. See the
[storage decision](decisions/0002-lossless-tiff-artifacts.md).

The 3b JPEG adapter adds hash-pinned `Pillow==13.0.0` for JPEG decoding and
encoding with LittleCMS ICC conversion. The local interpreter and DirectML
selection were retained, and the prepared lock identity was refreshed after
checking the installed pins and codec support. See
[JPEG verification](verification/jpeg-boundary.md).

## Python environment

- Target Python 3.14.x. The proposed initial target is standard 64-bit CPython on
  Windows, matching the native inference package requirements.
- Store application dependencies in `<app-root>\.venv` without inheriting system
  site packages. Do not install application packages into the system interpreter.
- Declare the Python range `>=3.14,<3.15` in package metadata. Select dependency
  versions for this target and lock the resolved package set for reproducibility.
- Validate native wheels, imports, and actual CPU/GPU execution where relevant;
  successful package resolution alone does not establish runtime compatibility.
- Install the local application package into the environment so module execution
  works from another working directory. Keep project code outside `.venv`.
- UI and worker processes use the same environment interpreter. Checkpoints are
  separate local assets, resolved through the configured checkpoint root.

## Backend selection during setup

The user reports an AMD Ryzen 7 7840HS APU with Radeon 780M Graphics, DirectX 12
Ultimate capability, and no NVIDIA CUDA GPU. Keep the RTX 20-series / 6 GB production
target and make most development validation possible through CPU and AMD backends.
Setup selects one device per runtime. On the Radeon 780M development machine,
select DirectML for ONNX Runtime and CPU for PyTorch. Use the prepared environment
for ordinary development. Run preparation or change devices for setup work only.

| Runtime | Setup choices | Development machine |
| --- | --- | --- |
| ONNX Runtime | CUDA (default), DirectML, CPU | DirectML |
| PyTorch | CUDA (default), CPU | CPU |

Each neural operation declares its inference runtime and supported devices.
For example, ONNX foreground segmentation can run on the Radeon GPU in the
same pipeline as a native PyTorch operation on the CPU. Windows CPython 3.14
has native PyTorch CPU and CUDA builds. The available `torch-directml` wheels
target older Python versions. [PyTorch release matrix](https://github.com/pytorch/pytorch/blob/main/RELEASE.md)
and [torch-directml release files](https://pypi.org/project/torch-directml/#files).

Running `prepare_env.cmd` displays two prompts.

```text
Select ONNX Runtime backend:
  1. CUDA (default)
  2. DirectML
  3. CPU
Choice [1]:

Select PyTorch backend:
  1. CUDA (default)
  2. CPU
Choice [1]:
```

Setup collects both choices before updating an existing environment. Each Enter
accepts CUDA for that prompt. Setup installs the two selected hash-pinned builds
and shared packages in the project-local `.venv`. Switching devices removes
conflicting builds before installation. Checkpoints stay in the configured folder.

Setup writes schema 2 of `.venv/retouching-backend.json` after both execution
probes pass. It records `onnx_backend`, `pytorch_backend`, and the hash covering
the shared and selected locks. Upgrading an environment with single-backend
metadata requires running setup once and answering both prompts.

`prepare_env.cmd --check` validates the recorded pair without prompting or
changing the environment. The ONNX probe verifies the provider in the execution
profile. The PyTorch probe checks the installed build, tensor device, and result.
Unavailable devices produce an error with the runtime and selected device.

Launch, previews and workers use the recorded pair. Pipeline validation checks
each stage against its own runtime's selection. Saved jobs record both choices
and package versions. Earlier ONNX-only jobs can resume with their original
ONNX device and versions. Device selection is available only during setup.
Snapshot actual providers, devices, precision, and runtime versions per job;
include them in cache identities and validation records. Portable pipeline JSON
and user-editable app configuration contain no backend-selection setting.

Each neural operation declares compatible backends and model formats. A raw
PyTorch checkpoint is not automatically usable by an ONNX/DirectML adapter.
Validate exportability, operator support, preprocessing, and output quality per
model. CUDA-only custom operations need an alternate implementation or remain an
explicitly untested capability on this machine. Do not force every model through
an unvalidated ONNX conversion or silently substitute different weights.

For DirectML sessions, follow provider-specific requirements: sequential session
execution, disabled memory-pattern optimization, and one inference call at a time
per session. This session execution mode is independent of the application's
sequential versus staged batch schedules.

## Environment preparation script

Proposed root script: `prepare_env.cmd`.

1. Locate the application root relative to the script, independent of the caller's
   working directory. Quote filesystem paths and preserve non-ASCII names.
2. Find an already-installed Python 3.14 interpreter, using the Windows Python
   launcher or a verified interpreter on PATH. Validate actual major/minor version
   and architecture; do not trigger automatic Python installation or accept a
   different minor version.
3. Create `.venv` when absent. If an existing environment is incompatible or
   damaged, stop with a remediation message instead of silently deleting it or
   falling back to system packages.
4. Ask for the ONNX Runtime backend and the PyTorch backend. Both default to CUDA.
   Install their locked builds, shared dependencies, and application package through
   that environment's interpreter. Repeated preparation must be safe and retain a
   compatible environment rather than needlessly rebuilding it.
5. Verify package versions, dependency consistency, and required imports. Report
   asset provisioning separately; do not automatically download neural checkpoints.
6. Persist the environment's backend/readiness metadata only after successful
   validation. Return success then; propagate a nonzero exit code on failure.

`prepare_env.cmd --check` performs validation only: no environment creation,
package installation/upgrades, or downloads. It diagnoses an absent or invalid
environment and exits nonzero. The script must not change system Python, global
PATH, or PowerShell execution policy.

Preparation may need network access for package installation; application photo
workflows remain offline after preparation and checkpoint provisioning. An offline
dependency bundle can be added later if required.

## Application launcher

Proposed root script: `run.cmd`.

- Run `<app-root>\.venv\Scripts\python.exe -m retouching_tool` explicitly.
  Environment activation is unnecessary; invoking `python` from PATH is not an
  acceptable substitute after preparation.
- Check that the environment is usable; if not, explain how to run preparation.
  Do not install/upgrade packages or automatically repair the environment on launch.
- Forward application arguments faithfully and propagate startup errors and exit
  codes. Locate app-owned files relative to the script, preserving the caller's
  working directory for command-line path resolution.
- Open the desktop application with the requested settings. Launch does not start
  automatic processing or bypass destination/pipeline validation.
- Use the devices recorded by setup. Missing backend support or incompatible
  hardware is an error; changing it requires rerunning `prepare_env.cmd`.
- Preserve setup/launch console errors and diagnostic file locations, including
  failures before environment/configuration/UI initialization. Full context and
  report behavior are defined in the [Diagnostics contract](diagnostics.md).

| Option | Meaning |
| --- | --- |
| `--source PATH` | Add a source folder or explicit JPEG file. Repeat to supply several paths. |
| `--pipeline FILE` | Load this pipeline JSON instead of the last-used pipeline. |
| `--config FILE` | Use this machine-local configuration file instead of the default root `config.json`. |
| `--help` | Show usage without creating a GUI, loading checkpoints, or starting work. |

CMD invocations (registered reference operations are supported as of milestone 3a):

```bat
prepare_env.cmd
prepare_env.cmd --check
run.cmd
run.cmd --source "D:\Photos\Session 1" --pipeline "D:\Pipelines\beauty.json"
run.cmd --source "D:\Photos\one.jpg" --source "D:\Photos\two.jpg"
```

Command-line source paths override restored session sources. An explicit pipeline
overrides last-used state; an invalid explicit file is an error, not permission to
silently select another pipeline. With no explicit pipeline, restore the last valid
pipeline, falling back to a visible default only as documented in the pipeline
contract. A successfully loaded valid pipeline becomes the current pipeline and
participates in normal last-used persistence.
The desktop also remembers paths after a successful source scan. Scan the
restored or command-line sources to build a fresh index. Recursion, destination,
schedule and JPEG options start at their setup defaults, while reopened jobs
use their frozen settings.

Resolve relative CLI file/directory paths against the caller's working directory.
Resolve an explicit configuration-file path the same way. Paths inside that file
follow the configuration rules below.

For explicit JPEG inputs, use each containing folder as a source location for
destination validation. For folder inputs, honor the UI recursion setting. Combine
and deduplicate resolved inputs. A destination must satisfy the source relationship
rules for every input root. Preserve relative paths within each root; when multiple
roots would collide, use deterministic root namespaces or require an explicit
collision resolution before processing. Do not silently flatten or overwrite.

## Machine-local configuration

The desktop **Settings…** dialog shows the active configuration file, both
runtime devices, configured folder values and effective paths. Edit the checkpoint,
temporary-artifact and log folders, minimum log severity, size limits and
retention period. Cancel discards the draft. Save validates folders and logging
options, then atomically replaces the active file, including an explicit
`--config` selection. Validation and write errors appear in the dialog.

Changing the checkpoint folder rechecks stage availability and dependencies.
Changing the temporary folder invalidates the disk check. An idle workspace with
included photos and a destination repeats preflight. Existing jobs retain their
frozen folders on resume. The session log writer applies the new directory and
limits on Save, retaining its session ID. A running worker keeps its launch-time
verbosity, and its events follow the session writer to the new directory. Backend
selection remains setup-only. [Settings verification](verification/desktop-settings.md)

The default file is `<app-root>\config.json`, with a versioned
`config.example.json` template supplied alongside the application:

```json
{
  "schema_version": 1,
  "paths": {
    "checkpoints": "./models",
    "temporary": "./.work",
    "logs": "./logs"
  },
  "logging": {
    "level": "INFO",
    "max_file_mb": 10,
    "max_total_mb": 200,
    "retention_days": 14
  }
}
```

- `paths.checkpoints` locates the model files. Its default is `./models`, and
  existing explicit paths continue to take precedence.
- `paths.temporary` is the root for app-owned per-job scratch/cache directories.
- `paths.logs` holds persistent diagnostic logs, outside disposable processing
  artifacts. Logging level/size/age limits use the proposed defaults above.
- Absolute paths are allowed. Relative folder paths are resolved against the
  configuration file's directory, not the caller's directory or a pipeline file.
- When the default configuration file is absent, expose documented defaults in
  the UI; an explicitly requested absent/malformed configuration is a startup
  error. Invalid schema or values in an existing default file are also reported.
- Resolve model references from pipeline JSON through the effective checkpoint
  root and asset manifest. Validate existence, expected checksum, and runtime
  requirements. Missing required files turn the stage off and produce a card error;
  a structurally valid pipeline still opens. An invalid/unavailable asset prevents
  stage activation and preview until recovery. Revalidate after checkpoint-root
  changes; folder configuration never causes an automatic checkpoint download.
- Open model page leads to the publisher. The user downloads the specified
  version, places the files at the reported destination paths, and uses Recheck
  to validate the local installation. Backend selection remains setup-only.
- Validate scratch writeability, free space, and source relationships before
  processing. Changing the setting affects future jobs; existing jobs retain
  their recorded artifact locations so recovery/cleanup can find them.
- After indexing, show mode-specific temporary and output reservations using the
  [disk space budget](requirements.md#disk-space-budgeting). Sum them for shared
  volumes, include declared extra artifact/write overhead, and monitor free space
  throughout execution. Do not automatically lower compression quality to fit.
- Keep configuration, last-used pipeline/session state, and durable job metadata
  outside disposable scratch directories. Clearing temporary data must not erase
  the application configuration or last-used pipeline.
- Application config, session state, `.venv`, checkpoints, and job artifacts are
  machine-local and excluded from source control. Version templates and dependency
  definitions. Persist config/session updates atomically.
- Validate logging settings and initialize a fallback logger if configuration or
  the requested log directory fails. Diagnostic export is local; images and model
  weights are excluded by default.

Settings are separate layers: application config supplies machine-local folders;
CLI options override the initial source/pipeline/config selection; pipeline JSON
supplies ordered operations and parameters; a job snapshots the effective result.
The prepared environment supplies each runtime's selected device.
UI edits affect current settings and future jobs, not already running jobs.

## References

[Python 3.14 venv documentation](https://docs.python.org/3.14/library/venv.html)
describes isolated environments, Windows `Scripts` paths, explicit interpreter
invocation without activation, and why an environment is recreated on each machine.

- [ONNX Runtime DirectML provider](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)
  documents DirectX 12 hardware support, operator restrictions, session options,
  and its sustained-engineering status. New Windows development is directed to
  WinML; evaluate its Python 3.14 API/package and fully offline provisioning before
  treating it as an alternative to the proposed DirectML adapter.
- [ONNX Runtime DirectML release files](https://pypi.org/project/onnxruntime-directml/#files)
  list a Windows x86-64 CPython 3.14 wheel for version 1.24.4, checked 2026-11-16.
  Wheel availability does not establish compatibility with a selected model.
- [PyTorch DirectML release files](https://pypi.org/project/torch-directml/#files)
  have no CPython 3.14 wheel in the published release inspected on 2026-11-16.
  Do not make this package the default AMD backend under the fixed Python version.

ROCm remains a possible model-specific adapter only after checking the exact GPU,
Windows release, driver, Python version, and current production support matrix.
DirectX capability alone does not establish ROCm compatibility. No ROCm package
or GPU model has been validated for this machine.
