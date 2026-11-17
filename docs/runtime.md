# Runtime and launch contract

The application targets Windows 11 only and runs on the user's system-wide
Python 3.14 through a project-local
virtual environment. Milestone 1a implements the script names, flags, configuration,
environment metadata, and startup skeleton below. Milestone 3a adds registered
pipeline validation and reference-stage cards. Sequential execution and recovery
are available through the headless application service. Staged execution, job
controls in the GUI and model provisioning remain later work. See the
[runtime evidence](verification/runtime-foundation.md) and
[sequential job evidence](verification/sequential-jobs.md).

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
The setup menu selects one installed backend. Model/runtime compatibility is
still subject to validation. A generated Add graph has executed on CPU and
DirectML; this establishes neither portrait-model compatibility nor CUDA acceptance.

Development-machine policy (confirmed 2026-11-17): use the prepared DirectML
environment for ordinary application development. Run setup tests and change
computing backends only for setup-script improvements or bug fixes. Routine
application verification must use the existing environment without preparation
or backend switching.

| Profile | Intended use | Candidate inference package |
| --- | --- | --- |
| `cpu` | Portable development, automated functional tests, reference inference | ONNX Runtime CPU; native PyTorch CPU if the chosen model needs it. |
| `directml` | AMD GPU integration tests on this Windows machine | ONNX Runtime DirectML for compatible ONNX models. |
| `cuda` | NVIDIA production and minimum-hardware validation | ONNX Runtime CUDA or a model-specific native PyTorch CUDA adapter. |

Running `prepare_env.cmd` interactively asks which backend to install or update:

```text
Select inference backend:
  1. CUDA (default)
  2. DirectML
  3. CPU
Choice [1]:
```

Pressing Enter chooses CUDA. Show the currently installed backend when one exists,
but keep CUDA as the menu default. Only setup offers backend selection; no backend
command-line flag or application configuration override is exposed.

Install/update shared application dependencies and support for only the selected
backend, using its complete pinned dependency set under `requirements/`. Maintain
one environment in `.venv`. If the selection changes, remove the previous backend's
exclusive packages rather than accumulating alternative runtime installations.
Do not co-install conflicting ONNX Runtime package variants that supply the same
`onnxruntime` import package. Checkpoints and temporary data are not removed by a
backend change.

Record the selected backend and dependency-set identity in environment-owned
metadata, proposed as `.venv/retouching-backend.json`. Record readiness only after
preparation and validation succeed. A failed update/switch must not leave stale
metadata claiming the environment is ready. This metadata is managed by setup,
not a user-facing backend setting.

`prepare_env.cmd --check` validates the recorded backend without a selection prompt
or modifying the environment. Missing/inconsistent metadata is an actionable setup
error, not a reason to guess a backend from installed package names.

Preparation/check diagnostics report Python version, profile, installed runtime,
available providers/devices, and a small actual inference result for the selected
compute backend. GPU availability is separate from package installation success.
Do not report an explicit GPU check as successful if it executed only on CPU.

`run.cmd` reads the prepared environment's metadata and uses that backend only.
It has no backend menu, backend flag, automatic selection, or automatic fallback.
If that backend is unavailable or incompatible, show an error and direct the user
to rerun setup. Validate the pipeline's operations against the installed backend
before processing. Ordinary CPU image operations remain available in every setup;
they do not represent a change of inference backend. Report any runtime-internal
CPU model partitions without treating them as proof of complete GPU execution.
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
4. Ask for the backend with CUDA as default, then install/synchronize only its
   locked backend support, shared dependencies, and application package through
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
- Use the backend recorded by setup. Missing backend support or incompatible
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

An in-app UI for editing application configuration is required (U48). It is
pending desktop-workflow implementation; the foundation window currently only
displays effective startup settings. Proposed editor behavior:

- Edit checkpoint, temporary-artifact, and log folders and logging options; show
  the active configuration file, including an explicit `--config` selection.
- Use shared application services for validation and atomic save. Show actionable
  validation/write errors and preserve the last valid settings on failure.
- Cancel discards unsaved edits. Save to the active configuration file, creating
  the default file when absent; retain configuration-relative path semantics.
- Revalidate model availability and processing paths after relevant changes;
  existing jobs retain their snapshots and artifact locations.
- Keep inference backend selection exclusively in setup.

Proposed default file: `<app-root>\config.json`, with a versioned
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
The prepared environment supplies its single installed backend.
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
