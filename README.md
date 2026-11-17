# Auto Retouch (by Neyrome)

A planned Windows 11 desktop tool for fully local batch retouching of studio portraits.
Stages can change pixels, produce reusable region/mask context, or do both.
Exports preserve source capture EXIF and record processing by the tool.
The initial GUI uses PySide6 with Qt Widgets, isolated behind toolkit-independent
application commands and events to keep a future GUI replacement localized.
The application targets Python 3.14, already installed system-wide, with all
application packages in a project-local virtual environment. Root CMD scripts
will prepare/check that environment and launch the app with source/pipeline
options. Configuration specifies checkpoint and temporary-artifact folders.
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

The project is currently in requirements and architecture planning. No application
or retouching engine has been implemented or benchmarked.

- [Product requirements](docs/requirements.md)
- [Implementation proposal](docs/architecture.md)
- [Pipeline contract](docs/pipeline.md)
- [Initial stage catalog and image state](docs/stages.md)
- [Runtime and launch contract](docs/runtime.md)
- [Logging and diagnostics contract](docs/diagnostics.md)
- [Development and agent workflow](docs/workflow.md)
- [Repository instructions for coding agents](AGENTS.md)
