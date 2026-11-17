# Milestone 1a verification and review

Date: 2026-11-17. Outcome: runtime foundation complete on the available machine.
No photo-processing engine, portrait checkpoint, visual-quality acceptance,
minimum-hardware benchmark, or CUDA acceptance is claimed.

## Environment and scope

- Windows 11, build 26300; standard 64-bit CPython 3.14.7 (MSC 1944/AMD64).
- Main interpreter: project `.venv/Scripts/python.exe`; final backend **DirectML**.
- Qt 6.12.0 via PySide6 Essentials/shiboken6 6.12.0; ONNX Runtime DirectML 1.24.4;
  numpy 2.5.5. Exact transitive versions/hashes are in `requirements/*.lock`.
- User-reported hardware: Ryzen 7 7840HS/Radeon 780M, no NVIDIA GPU. CIM hardware
  queries were unavailable in the sandbox; adapter identity/driver version was
  not independently measured. Actual DirectML node execution is recorded below.
- Setup/inference fixtures use generated arithmetic data, synthetic paths/settings,
  and empty pipelines. No portrait is decoded, changed, transmitted, or committed.

## Automated checks

Run ordinary checks in the current DirectML environment:

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

24 checks cover strict/size-bounded JSON and duplicate/nonfinite rejection,
atomic-save failure recovery, config defaults and relative paths, invalid schemas
and backend overrides, caller-relative repeatable CLI paths (including empty-path
rejection), explicit-vs-restored precedence, corrupt-state warnings, invalid
explicit imports without fallback, source validation, frozen startup snapshots,
Qt-free shared imports, structured diagnostic context/tracebacks, credentials,
bounded records/memory, simulated disk-full/retention failures, size/age rotation,
unowned-file preservation, Qt effective-state display, actual selected-provider
execution, launcher help from another directory, invalid configuration exit/log
evidence at INFO, full composition/window open/close, unavailable-provider/wrong-
Python errors, and early missing-environment console/file diagnostics.

The Qt window was also rendered offscreen and visually inspected. That test
platform initially supplied no fonts; registering the machine's Segoe UI font
for the test produced readable, unclipped startup text. No font is bundled and
no offscreen-specific font workaround is added to production. Local render:
`.test-work/runtime-foundation.png` (ignored).

For this setup milestone only, run opt-in checks:

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p setup_checks.py -v
```

Six checks cover the CUDA menu default, bounded invalid selection, failed-update
readiness invalidation, damaged/missing environments without destructive repair,
check-only no-prompt/no-install behavior, and an actual check-only pass that leaves
every environment file's size and modification time unchanged.

The normal test pattern excludes setup checks, preserving the user's policy that
ordinary application tasks do not test setup or change backends.

## Actual preparation and provider evidence

The initial project environment was prepared for CPU and then switched through
setup to DirectML. Each passed exact-version checks, `pip check`, Qt imports, and
actual inference. Conflicting ORT distributions were removed before installation.
The main environment remains ready on DirectML.

`tools/verify_clean_setup.py` ran in a disposable application copy whose directory
names included spaces and Cyrillic characters, invoked from outside that copy:

| Case | Result |
| --- | --- |
| Clean preparation, CPU | Exit 0, ready CPU metadata, actual Add on CPUExecutionProvider |
| Repeated CPU preparation | Exit 0; existing environment retained |
| CPU -> DirectML | Exit 0; CPU runtime removed; actual Add on DmlExecutionProvider |
| Repeated DirectML preparation | Exit 0; existing environment retained |
| DirectML -> CPU | Exit 0; DirectML runtime removed; CPU revalidated |
| Check-only after each case | Exit 0, no menu; prepared backend validated |
| Root CMD launcher from outside Unicode application folder | Help exit 0; arguments forwarded |

This setup-focused acceptance script makes no changes to the main environment.
Its generated copy was removed; local summary is `.test-work/clean-setup-result.json`
(ignored). Initial Unicode-path acceptance exposed pip output encoding failure;
setting UTF-8 mode/stdio in CMD and setup children fixed it before the successful
retest. Structured child events retain `parent_session_id` correlation.

Probe input: float32 vectors `[1,2]` and `[3,4]`; output `[4,6]`. ORT profiling
confirmed the node executed on the requested provider, with GPU CPU-fallback
disabled. No image dimensions, retouching time, peak RAM/VRAM, or model-quality
measurements apply to this arithmetic check. It is not a hardware benchmark.

## Review and remaining gates

Single-agent review checked setup/launch separation, readiness invalidation before
package mutation, conflicting import-package removal, isolated editable installs,
safe quoted/Unicode paths, caller/config path bases, Qt import boundaries, source
preservation, no processing on launch, no network path in application work, and
actionable failures with causes. `.venv`, local state/config/logs, model files, and
test artifacts are ignored. No checkpoints were downloaded.

The foundation window is intentionally limited: no indexing, destination control,
operation editor, processing previews, schedulers, or exports. Pipeline startup
accepts only a strict version-1 envelope with an empty stage list and rejects
nonempty stages explicitly; it does not pretend the planned registry is present.
Corrupt last-used content is preserved with a visible empty-default warning.

Worker logging transport, native-crash capture, batch summaries, diagnostic search
and report export are later milestones. Current low-volume bootstrap logs are
synchronous. Shell errors before Python use bounded plain-text local-time logs;
JSONL uses UTC and configurable size/age limits. Exception/record truncation is
explicit. Full shutdown/overload behavior must be revisited with workers.

CUDA wheels resolved for Python 3.14, but CUDA imports/inference, library/driver
provisioning, minimum 6 GB NVIDIA behavior, and batch stability remain pending on
NVIDIA hardware. Representative photos, user-reviewed Natural/Beauty appearance,
metadata round trips, checkpoint commercial-use evidence, and full offline
import-to-export acceptance remain pending in their respective milestones.
