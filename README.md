# Auto ReTouch

Author: Neyrome.

A Windows 11 desktop project for fully local batch retouching of studio portraits.
Stages can change pixels, produce reusable region/mask context, or do both.
JPEG, JPEG XL, PNG and TIFF exports preserve supported capture EXIF and record processing by the tool. BMP exports omit photo metadata.
The initial GUI uses PySide6 with Qt Widgets, isolated behind toolkit-independent
application commands and events to keep a future GUI replacement localized.
The application targets Python 3.14, already installed system-wide, with all
application packages in a project-local virtual environment. Root CMD scripts
will prepare/check that environment and launch the app with source/pipeline
options. Configuration specifies checkpoint and temporary-artifact folders.
**Settings…** edits local folders, logging severity, size limits, and retention.
Setup asks separately for the ONNX Runtime backend (CUDA, DirectML, or CPU) and
the PyTorch backend (CUDA or CPU). Each stage uses its runtime's recorded device.
NVIDIA acceptance requires testing on actual NVIDIA hardware.
Open **Diagnostics…** to search retained local logs and copy technical details.
**Job diagnostics…** in the monitor opens the selected job's warnings and errors.
Use **Export report…** in either view to save a local ZIP with redacted
diagnostics and available settings for the session or job. Review the archive
before sharing it.

The reference workload is approximately 500 JPEG photos on a consumer NVIDIA RTX
20-series GPU with at least 6 GB VRAM. Natural and Beauty presets are required.
Minimum system RAM is 16 GB. Retouching covers all exposed skin and multiple people
per image. Models must permit commercial use for paid photography work.
Choose an output format under **Batch and export settings**.

| Format | Controls |
| --- | --- |
| JPEG | Quality, chroma subsampling, progressive encoding |
| JPEG XL | Quality or lossless mode, encoding effort |
| PNG | Lossless compression level |
| TIFF | Uncompressed or lossless Deflate |
| BMP | Print resolution in DPI, 24-bit RGB, no EXIF |

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
CPU statistics and manual exposure are contract references. Natural and Beauty
presets remain pending. To try the local CPU retouching model, add **Alibaba skin
retouching** and use its preview to compare the original and edited image. The
stage offers strength and scaling controls. [Model files and stage behavior](docs/stages.md#alibaba-skin-retouching-stage).

The 3b storage slice now uses uncompressed float32 TIFF intermediates with typed
context manifests, immutable source metadata blobs, checksum validation and
reference-aware cleanup. `tifffile` is pinned in the main shared dependencies.
See [artifact evidence](docs/verification/lossless-artifacts.md).

The headless JPEG adapter uses pinned Pillow to apply input orientation and color
management, then verifies capture metadata, sRGB and processing history before
publishing exports. Both headless schedules support restart recovery. Staged
execution keeps a lossless checkpoint for each active image and encodes the selected format
after the final stage releases its resources. Both modes run in a local worker
process. The desktop workspace keeps the pipeline editor, source and destination
paths, photo thumbnails, and export settings on one screen. **Job progress and
history** opens the monitor below the batch controls. See
[JPEG verification](docs/verification/jpeg-boundary.md),
[staged job verification](docs/verification/staged-jobs.md), and
[desktop workspace verification](docs/verification/desktop-workspace.md).

Use standard 64-bit Python 3.14 on Windows 11. For a new installation, run
`prepare_env.cmd` and answer both prompts. Enter selects CUDA at either prompt.
Setup installs the hash-pinned packages in `.venv` and tests execution on both devices.
GPU setup fails when its provider cannot execute; it never silently falls back.
CUDA also requires compatible NVIDIA drivers/CUDA/cuDNN runtime provisioning;
that profile has not been hardware-tested here.

On this development machine, choose **2. DirectML** for ONNX Runtime and
**2. CPU** for PyTorch. Use the prepared environment for development. Rerun
setup or change backends only for setup improvements or bug fixes. Launch with
`run.cmd`; `run.cmd --help` lists repeatable `--source`, `--pipeline`, and `--config`
options. Launch never installs packages or starts processing automatically.
Open **Settings…** to see the active configuration file and effective paths.
Saving creates the default `config.json` or updates the file passed to `--config`.
Relative paths resolve from that file's directory. Folder changes apply to future
previews and batches. Existing jobs retain their recorded paths.

When a model is required, download the specified files from the publisher and
place them at the paths shown on the stage card. The default model folder is
`models/`, configurable through `paths.checkpoints`. On the stage card, choose
**Settings → Check required model files** after placement.

Pipeline files use version 1 and registered operation versions. Try the reference
cards with `run.cmd --pipeline examples\pipelines\reference.json`. This example is
not a Natural or Beauty preset. Unknown operations/parameters fail explicitly.
Valid explicit settings replace last-used selection; corrupt restored state
produces a visible warning.

Choosing sources starts a scan and shows thumbnails and disk estimates. Use the
thumbnail checkboxes to include photos in a batch. Double-click a thumbnail or
press Enter on the selected photo to open the source. Choose **Run pipeline** in
that window to see the result. All comparisons start side by side. Press Space
to switch between input and result at the full pane width, or choose **Side by
side** in the view selector to see both again. Scroll over a detail to zoom there,
or use **+** and **−** to zoom around the center. Drag either image to pan both.
Select **100%** to inspect the current detail at one image pixel per physical
screen pixel. Space keeps the same magnification and center. **Fit** shows the
whole photo again.

On a stage card, **Preview** compares the
source with the stage result. **Preview in pipeline** includes earlier enabled
stages in the comparison input. Both stage previews let you adjust and save
settings. Pixel stages have a **Blending** slider for mixing the stage input
with its result.

Choose a destination and set the schedule and JPEG options. Disk status updates
automatically when you select folders, rescan, change the included photos, or
switch schedules. Select **Start processing** when the batch is ready.
The processing window shows progress and per-photo results. **Pause** waits for
the current unit to finish. **Continue** resumes the saved job. **Break** asks
for confirmation before canceling the remaining work and keeps completed exports.
To resume after relaunching, choose **Open saved job…** and then **Continue**.

Run focused desktop batch checks in the existing environment:

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_desktop_batch.py -v
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
