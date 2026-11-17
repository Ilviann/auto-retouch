# Auto ReTouch

Author: Neyrome.

A Windows 11 desktop project for fully local batch retouching of studio portraits.
Stages can change pixels, produce reusable region/mask context, or do both.
Exports preserve source capture EXIF and record processing by the tool.
The initial GUI uses PySide6 with Qt Widgets, isolated behind toolkit-independent
application commands and events to keep a future GUI replacement localized.
The application targets Python 3.14, already installed system-wide, with all
application packages in a project-local virtual environment. Root CMD scripts
will prepare/check that environment and launch the app with source/pipeline
options. Configuration specifies checkpoint and temporary-artifact folders.
An in-app configuration editor is required and planned for the desktop workflow.
Setup asks for CUDA (default), DirectML, or CPU and installs only the selected
backend support plus shared dependencies. Launch always uses the recorded backend.
NVIDIA acceptance requires testing on actual NVIDIA hardware.
Diagnostics will include contextual warning/error logs, in-app filtering, batch
summaries, and local bug-report export, with bounded storage outside scratch data.

The reference workload is approximately 500 JPEG photos on a consumer NVIDIA RTX
20-series GPU with at least 6 GB VRAM. Natural and Beauty presets are required.
Minimum system RAM is 16 GB. Retouching covers all exposed skin and multiple people
per image. Models must permit commercial use for paid photography work. Export
settings initially support JPEG compression; WebP, JPEG XL, and AVIF follow later.
Unattended processing for a couple of hours is acceptable; visual quality takes
priority over a strict throughput deadline.

The interface uses an ordered list of configurable operations, saved as JSON and
restored at startup. Source scanning supports recursion; processing requires a
validated destination. Sequential mode completes one photo at a time, while staged
mode processes the batch one operation at a time using temporary artifacts and
releasing model resources between stages.

The runtime foundation (milestone 1a) and pipeline contracts/reference stages (3a)
are implemented. The Qt window shows startup settings and loaded stage cards with
on/off controls, numeric parameters, asset errors, and dependency status. Headless
services support add/remove/reorder, atomic JSON save/load, and last-used restore.
CPU statistics and manual exposure are contract references; photo import/processing,
Natural/Beauty presets, and exports remain pending. No portrait models
have been selected or downloaded, and no retouching quality has been benchmarked.

Use standard 64-bit Python 3.14 on Windows 11. For a new installation, run
`prepare_env.cmd` and choose a backend (Enter selects CUDA). Setup installs the
hash-pinned dependency set in `.venv` and validates actual provider execution.
GPU setup fails when its provider cannot execute; it never silently falls back.
CUDA also requires compatible NVIDIA drivers/CUDA/cuDNN runtime provisioning;
that profile has not been hardware-tested here.

On this development machine, **use the existing DirectML environment**. Rerun
setup or change backends only for setup improvements or bug fixes. Launch with
`run.cmd`; `run.cmd --help` lists repeatable `--source`, `--pipeline`, and `--config`
options. Launch never installs packages or starts processing automatically.
Copy `config.example.json` to `config.json` to customize local folders; without it,
the window shows the documented defaults.

Pipeline files use version 1 and registered operation versions. Try the reference
cards with `run.cmd --pipeline examples\pipelines\reference.json`. This example is
not a Natural or Beauty preset. Unknown operations/parameters fail explicitly.
Valid explicit settings replace last-used selection; corrupt restored state
produces a visible warning. No selected photo is decoded or modified.

Run ordinary app checks in the existing environment:

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Setup-specific checks are opt-in and reserved for setup work:

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p setup_checks.py -v
.venv\Scripts\python.exe -B tools\verify_clean_setup.py
```

The second setup command installs packages in a disposable Unicode-path copy and
switches that copy between CPU and DirectML; it leaves the main environment alone.
See [milestone evidence](docs/verification/runtime-foundation.md), the
[pipeline evidence](docs/verification/pipeline-contracts.md), the
[implementation backlog](docs/backlog.md), and the
[dependency decision and terms](docs/decisions/0001-runtime-foundation.md).

- [Product requirements](docs/requirements.md)
- [Implementation proposal](docs/architecture.md)
- [Pipeline contract](docs/pipeline.md)
- [Initial stage catalog and image state](docs/stages.md)
- [Runtime and launch contract](docs/runtime.md)
- [Logging and diagnostics contract](docs/diagnostics.md)
- [Development and agent workflow](docs/workflow.md)
- [Repository instructions for coding agents](AGENTS.md)
