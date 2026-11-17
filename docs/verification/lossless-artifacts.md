# Lossless TIFF artifact slice of milestone 3b

Implemented and reviewed by one agent on 2026-11-17, using the existing Windows
11/Python 3.14.7 environment with DirectML retained. Added tifffile 2026.10.15 to
the main environment and reproducible shared dependencies with explicit user
authorization. No checkpoints or portrait data were downloaded or uploaded.

## Delivered and checked

- Ordinary uncompressed float32 RGB TIFF with exact sample-byte preservation,
  including adjacent float32 values, subnormals and signed zero. The library reads
  the generated TIFF independently of the store and reports the expected standard
  TIFF tags. No sample quantization, JPEG encoding or resolution reduction occurs.
- Version-1 bounded, strict JSON manifests; immutable original EXIF/ICC bytes;
  statistics and spatial measurements, coordinate/revision/source checks and full
  producer/settings/model provenance. Unknown payloads, stale context and corrupt
  statistics fail. Dense mask codecs are not implemented yet.
- Content-addressed TIFF/metadata blobs, context-only pixel reuse, idempotent
  commits and conflict rejection, store/job ownership, checksummed immutable refs,
  explicit retirement and reference-aware collection. File handles close before
  returning immutable bytes-backed image states.
- Manifest-last writes preserve a previously committed predecessor during simulated
  TIFF interruption, flush failure and disk-full/manifest-finalization failure.
  Missing/corrupt blobs and altered manifests fail reuse with actionable diagnostic
  codes and chained tracebacks at normal verbosity. Writes include job/image/stage
  correlation. Owned unreferenced blobs are reclaimable; unknown files and pending
  write evidence remain. Invalid manifests stop collection before deletion.
- Three reference stages (statistics/exposure/statistics) agree bit for bit between
  in-memory execution and reopening/persisting each stage, including metadata and
  context. This establishes storage/reference semantics, not scheduler equivalence.
- Shared artifact modules import headlessly with Qt, NumPy, tifffile and ONNX
  imports blocked. Array/TIFF imports occur only when needed for storage operations.

Focused command:

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_artifacts.py -v
```

18 cases: **17 passed, 1 skipped**. The skipped real Windows junction check
requires explicit `AUTO_RETOUCH_TEST_JUNCTION=1` opt-in; sandbox creation was denied
and the elevated check was not authorized. Real junction acceptance is pending.
Mocked alias rejection, wrong-job/store isolation and changed-marker refusal pass;
they are not claimed as actual junction filesystem evidence. No unrelated/full
test suite or setup test was run. Installed-version validation and `pip check`
pass; the prepared backend still reads `directml`, ORT DirectML 1.24.4. Only its
dependency lock hash changed; backend selection/probing was not repeated.

The initial editable-package refresh encountered a setuptools error interpreting
braces in the sandbox temporary path. Retrying with a workspace-local temporary
directory succeeded, without changing packages or weakening isolation.

## Review and remaining work

Reviewed strict schema/type/allocation limits, sample precision, source/context
identity, transactional boundaries, shared-blob lifetime, failed-write evidence,
path naming and ownership checks, optional dependency loading and diagnostic
causes. Store calls are serialized by the caller; concurrent writers/collectors,
hostile filesystem races and physical power-loss durability are not validated.
Manifests and TIFFs must be revalidated after reopening. The job journal/recovery
scheduler and complete folder/disk preflight remain unimplemented.

Seven local JPEG study shots are available in the user-authorized source folder
as of this task. Only the file count/extensions were inspected; no study photo was
decoded, modified or exported by these synthetic storage tests. Representative
coverage and Natural/Beauty visual acceptance remain unreviewed. The subsequent
[JPEG verification](jpeg-boundary.md) covers decoding, color, orientation and
verified export. CUDA/portrait models, full batch benchmarks and minimum-memory
acceptance remain pending.
