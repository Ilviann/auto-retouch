"""Immutable source indexes and headless path/storage preflight for photo batches."""

from dataclasses import dataclass
from functools import cached_property
import os
from pathlib import Path
import shutil
import stat
from uuid import uuid4

from ..errors import RetouchingError
from ..imaging.export_settings import ExportSettings, JpegSettings, JxlSettings, export_relative_path, output_reservation, require_jxl
from ..imaging.jpeg import JpegHeader
from ..imaging.source import SOURCE_EXTENSIONS, inspect_source_header
from ..pipeline.image_state import RGB_FLOAT32_BYTES_PER_PIXEL
from ..storage.artifacts import MANIFEST_LIMIT
from ..storage.batch_paths import (FileStamp, current_file_stamp, directory_chain, existing_output_stamp, file_identity,
                                   is_reparse, output_path, probe_writable, resolve_folder)
from ..storage.tiff_pixels import TIFF_OVERHEAD_LIMIT

BASE_SCRATCH_BYTES = 200_000_000
CHECKPOINT_OVERHEAD_BYTES = MANIFEST_LIMIT + TIFF_OVERHEAD_LIMIT


@dataclass(frozen=True)
class SourceRoot:
    path: Path
    identity: tuple[int, int]


@dataclass(frozen=True)
class IndexedImage:
    image_id: str
    path: Path
    relative_path: Path
    stamp: FileStamp | None
    header: JpegHeader | None
    issue: str | None = None

    @property
    def working_bytes(self) -> int | None:
        if self.header is not None:
            return self.header.width * self.header.height * RGB_FLOAT32_BYTES_PER_PIXEL
        return None


@dataclass(frozen=True)
class SourceIndex:
    index_id: str
    recursive: bool
    roots: tuple[SourceRoot, ...]
    images: tuple[IndexedImage, ...]

    @cached_property
    def name_conflicts(self):
        groups = {}
        for image in self.images:
            groups.setdefault(image.path.stem.casefold(), []).append(image.path)
        return tuple(tuple(group) for group in groups.values() if len(group) > 1)

    @cached_property
    def name_error(self):
        if not self.name_conflicts:
            return ""
        names = "\n".join(f"{group[0].stem} — " + ", ".join(str(path) for path in group)
                          for group in self.name_conflicts)
        return "Source names must be unique without extensions. Rename or remove the duplicates, then rescan.\n" + names


@dataclass(frozen=True)
class PlannedImage:
    source: IndexedImage
    output: Path
    replace_stamp: FileStamp | None = None
    skip: bool = False


@dataclass(frozen=True)
class ExistingOutputDecision:
    action: str
    files: tuple[tuple[Path, FileStamp], ...]

    def __post_init__(self):
        if self.action not in ("skip", "overwrite"):
            raise ValueError("Choose skip or overwrite for existing outputs.")


class ExistingOutputs(RetouchingError):
    def __init__(self, files):
        self.files = tuple(files)
        count = len(self.files)
        message = "1 planned output file already exists." if count == 1 else f"{count} planned output files already exist."
        super().__init__("OUTPUT_COLLISION", message)


@dataclass(frozen=True)
class SpaceBudget:
    working_bytes: int
    base_bytes: int
    tiff_bytes: int
    metadata_bytes: int
    manifest_bytes: int
    replacement_bytes: int
    extra_scratch_bytes: int
    output_bytes: int

    @property
    def scratch_bytes(self) -> int:
        return (self.working_bytes + self.base_bytes + self.tiff_bytes + self.metadata_bytes
                + self.manifest_bytes + self.replacement_bytes + self.extra_scratch_bytes)


@dataclass(frozen=True)
class VolumeBudget:
    device: int
    paths: tuple[Path, ...]
    required_bytes: int
    available_bytes: int


@dataclass(frozen=True)
class BatchPlan:
    index_id: str
    mode: str
    destination: Path
    scratch: Path
    images: tuple[PlannedImage, ...]
    budget: SpaceBudget
    volumes: tuple[VolumeBudget, ...]

    @property
    def ready(self) -> bool:
        return all(volume.available_bytes >= volume.required_bytes for volume in self.volumes)


def _source_name(path):
    return path.suffix.lower() in SOURCE_EXTENSIONS


def _space_budget(images, mode, extra_scratch_bytes, settings):
    if not images:
        return SpaceBudget(0, 0, 0, 0, 0, 0, 0, 0)
    largest = max(image.working_bytes for image in images)
    output = sum(output_reservation(settings, image.stamp.size, image.working_bytes) for image in images)
    if mode == "sequential":
        return SpaceBudget(largest, BASE_SCRATCH_BYTES, 0, 0, 0, 0, extra_scratch_bytes, output)
    count = len(images)
    return SpaceBudget(
        sum(image.working_bytes for image in images), BASE_SCRATCH_BYTES,
        count * TIFF_OVERHEAD_LIMIT, sum(image.header.header_bytes for image in images),
        count * MANIFEST_LIMIT, largest + CHECKPOINT_OVERHEAD_BYTES,
        extra_scratch_bytes, output,
    )


def volume_budgets(reservations):
    groups = {}
    for folder, existing, amount in reservations:
        device, _ = file_identity(existing.stat())
        available = shutil.disk_usage(existing).free
        previous = groups.get(device)
        if previous is None:
            groups[device] = VolumeBudget(device, (folder,), amount, available)
        else:
            groups[device] = VolumeBudget(device, (*previous.paths, folder), previous.required_bytes + amount,
                                          min(previous.available_bytes, available))
    return tuple(groups.values())


class BatchService:
    def __init__(self, scratch: Path, diagnostics):
        self.scratch = scratch
        self.diagnostics = diagnostics

    def estimate(self, index: SourceIndex, *, mode: str, selected_ids=None, settings=JpegSettings()) -> SpaceBudget | None:
        """Estimate from indexed headers alone, without needing a destination."""
        if mode not in ("sequential", "staged"):
            raise RetouchingError("BATCH_SETTINGS_INVALID", "Select sequential or staged mode.")
        if not isinstance(settings, ExportSettings):
            raise RetouchingError("BATCH_SETTINGS_INVALID", "Select a supported export format and settings.")
        images = self._select(index, selected_ids, allow_empty=True)
        if not images or any(image.header is None for image in images):
            return None
        return _space_budget(images, mode, 0, settings)

    def index(self, sources, *, recursive: bool = False) -> SourceIndex:
        """Combine launch/UI sources. The first selected folder owning a file supplies its relative path."""
        index_id = uuid4().hex
        source = None
        try:
            if type(recursive) is not bool:
                raise RetouchingError("SOURCE_RECURSION_INVALID", "Set source recursion on or off.")
            roots = {}
            folders = {}
            explicit = {}
            for selection in sources:
                source = Path(selection)
                source = source.resolve(strict=True)
                info = source.stat()
                if stat.S_ISDIR(info.st_mode):
                    folders[str(source)] = source
                    root = source
                elif stat.S_ISREG(info.st_mode) and _source_name(source):
                    explicit[str(source)] = source
                    root = source.parent
                else:
                    raise RetouchingError("SOURCE_INVALID", f"Select an existing folder or photo file (JPEG, AVIF, PNG, TIFF, BMP, JPEG XL, DNG): {source}")
                if str(root) not in roots:
                    roots[str(root)] = SourceRoot(root, file_identity(root.stat()))

            images = {}
            for folder in folders.values():
                source = folder
                for path in self._paths(folder, recursive, index_id):
                    if path not in images:
                        images[path] = self._inspect(path, path.relative_to(folder), index_id)
            for path in explicit.values():
                if path not in images:
                    images[path] = self._inspect(path, Path(path.name), index_id)
        except (TypeError, OSError, ValueError, RetouchingError) as exc:
            error = exc if isinstance(exc, RetouchingError) else RetouchingError(
                "SOURCE_SCAN_FAILED", f"Cannot index {source}. Check that the source exists and can be read.")
            self._emit_failure(error, index_id=index_id, source_path=source)
            if error is exc:
                raise
            raise error from exc

        self.diagnostics.emit("SOURCE_INDEXED", "Finished reading source image headers", component="batch",
                              index_id=index_id, recursive=recursive, source_count=len(images),
                              unreadable_count=sum(image.issue is not None for image in images.values()))
        index = SourceIndex(index_id, recursive, tuple(roots.values()), tuple(images.values()))
        if index.name_conflicts:
            self.diagnostics.emit("SOURCE_NAME_CONFLICT", index.name_error, component="batch",
                                  level="ERROR", index_id=index_id)
        return index

    def _paths(self, folder, recursive, index_id):
        pending = [folder]
        while pending:
            directory = pending.pop()
            try:
                if directory.resolve(strict=True) != directory or is_reparse(directory.lstat()):
                    raise OSError("Source directory became an alias during indexing")
                with os.scandir(directory) as entries:
                    children = sorted(entries, key=lambda entry: (entry.name.casefold(), entry.name))
                subdirectories = []
                for entry in children:
                    path = Path(entry.path)
                    info = entry.stat(follow_symlinks=False)
                    if is_reparse(info) or stat.S_ISLNK(info.st_mode):
                        if recursive or _source_name(path):
                            self.diagnostics.emit("SOURCE_LINK_SKIPPED", "Skipped a link or reparse point during photo discovery",
                                                  component="batch", level="WARNING", index_id=index_id, source_path=path)
                    elif stat.S_ISDIR(info.st_mode) and recursive:
                        subdirectories.append(path)
                    elif stat.S_ISREG(info.st_mode) and _source_name(path):
                        yield path
                pending.extend(reversed(subdirectories))
            except OSError as exc:
                raise RetouchingError("SOURCE_SCAN_FAILED", f"Cannot enumerate {directory}. Check folder access and retry.") from exc

    def _inspect(self, path, relative, index_id):
        image_id = uuid4().hex
        stamp = None
        try:
            stamp = current_file_stamp(path)
            with path.open("rb") as stream:
                header = inspect_source_header(path, stream)
                opened = FileStamp.capture(os.fstat(stream.fileno()))
            if opened != stamp or current_file_stamp(path) != stamp:
                raise RetouchingError("SOURCE_CHANGED", f"Source changed while it was indexed: {path}. Re-index it.")
        except (OSError, ValueError, RetouchingError) as exc:
            error = exc if isinstance(exc, RetouchingError) else RetouchingError(
                "INPUT_HEADER_INVALID", f"Cannot read the image header in {path}. Check the file and re-index it.")
            self._emit_failure(error, index_id=index_id, image_id=image_id, source_path=path)
            return IndexedImage(image_id, path, relative, stamp, None, str(error))
        return IndexedImage(image_id, path, relative, stamp, header)

    def preflight(self, index: SourceIndex, destination: Path, *, mode: str,
                  selected_ids=None, extra_scratch_bytes: int = 0, job_id: str | None = None,
                  preview_request_id: str | None = None, settings=JpegSettings(), check_outputs=True,
                  existing_decision: ExistingOutputDecision | None = None) -> BatchPlan:
        """Validate folders and space. Check individual output targets only for a start request."""
        context = {"index_id": index.index_id, "job_id": job_id, "mode": mode, "destination_path": destination,
                   "preview_request_id": preview_request_id}
        try:
            if mode not in ("sequential", "staged") or type(extra_scratch_bytes) is not int or extra_scratch_bytes < 0:
                raise RetouchingError("BATCH_SETTINGS_INVALID", "Select sequential or staged mode and a nonnegative extra scratch budget.")
            if not isinstance(settings, ExportSettings):
                raise RetouchingError("BATCH_SETTINGS_INVALID", "Select a supported export format and settings.")
            context["export_format"] = settings.format
            if index.name_conflicts:
                raise RetouchingError("SOURCE_NAME_CONFLICT", index.name_error)
            if isinstance(settings, JxlSettings):
                require_jxl()
            selected = self._select(index, selected_ids)
            destination, scratch, scratch_parent = self.validate_folders(index, destination)
            planned = self._plan_images(selected, destination, context, settings)
            probes = {(destination, False): None}
            if check_outputs:
                planned, probes = self._check_outputs(planned, destination, scratch, index, existing_decision, context)
            budget = _space_budget([p.source for p in planned if not p.skip], mode, extra_scratch_bytes, settings)
            volumes = volume_budgets(((destination, destination, budget.output_bytes),
                                      (scratch, scratch_parent, budget.scratch_bytes)))
            plan = BatchPlan(index.index_id, mode, destination, scratch, planned, budget, volumes)
            if plan.ready and budget.scratch_bytes:
                probes[scratch_parent, True] = None  # Processing needs an application-owned child directory.
                for directory, needs_subdirectory in probes:
                    try:
                        probe_writable(directory, needs_subdirectory=needs_subdirectory)
                    except OSError as exc:
                        raise RetouchingError("BATCH_FOLDER_UNWRITABLE",
                                              f"Cannot create and remove preflight files in {directory}. Check folder permissions.") from exc
        except ExistingOutputs as exc:
            self.diagnostics.emit(exc.code, str(exc), component="batch", collision_count=len(exc.files), **context)
            raise
        except RetouchingError as exc:
            self._emit_failure(exc, **context)
            raise
        except (OSError, TypeError, ValueError) as exc:
            error = RetouchingError("BATCH_PREFLIGHT_FAILED", "Cannot verify path or free space. Check folder access and retry.")
            self._emit_failure(error, **context)
            raise error from exc

        for volume in plan.volumes:
            fits = volume.available_bytes >= volume.required_bytes
            self.diagnostics.emit("BATCH_SPACE_AVAILABLE" if fits else "BATCH_SPACE_INSUFFICIENT",
                                  "Volume has enough space" if fits else "Free disk space or choose a different folder before starting",
                                  component="batch", level="INFO" if fits else "ERROR",
                                  paths=volume.paths, required_bytes=volume.required_bytes,
                                  available_bytes=volume.available_bytes, **context)
        return plan

    @staticmethod
    def _select(index, selected_ids, *, allow_empty=False):
        if selected_ids is None:
            selected = index.images
        else:
            wanted = set(selected_ids)
            selected = tuple(image for image in index.images if image.image_id in wanted)
            if len(selected) != len(wanted):
                raise RetouchingError("BATCH_SELECTION_INVALID", "Select photos from the current index.")
        if not selected and not allow_empty:
            raise RetouchingError("BATCH_SELECTION_INVALID", "Select one or more photos from the current index.")
        return selected

    def validate_folders(self, index, destination):
        try:
            destination, _ = resolve_folder(Path(destination))
        except (TypeError, OSError, ValueError) as exc:
            raise RetouchingError("DESTINATION_INVALID", "Select an existing, accessible destination folder.") from exc
        try:
            scratch, scratch_parent = resolve_folder(self.scratch, allow_missing=True)
        except (OSError, ValueError) as exc:
            raise RetouchingError("SCRATCH_INVALID", f"Choose an accessible temporary folder in settings: {self.scratch}") from exc

        destination_chain = directory_chain(destination)
        scratch_chain = directory_chain(scratch_parent)
        if destination == scratch or (scratch == scratch_parent and destination_chain[0] == scratch_chain[0]):
            raise RetouchingError("SCRATCH_IS_DESTINATION", "Choose distinct folders for temporary artifacts and exports.")
        for root in index.roots:
            try:
                unchanged = root.path.resolve(strict=True) == root.path and file_identity(root.path.stat()) == root.identity
            except OSError as exc:
                raise RetouchingError("SOURCE_CHANGED", f"Cannot reopen source folder {root.path}. Re-index the sources.") from exc
            if not unchanged:
                raise RetouchingError("SOURCE_CHANGED", f"Source folder changed: {root.path}. Re-index the sources.")
            if destination == root.path or destination_chain[0] == root.identity:
                raise RetouchingError("DESTINATION_IS_SOURCE", f"Choose a destination distinct from source {root.path}.")
            if index.recursive and (destination.is_relative_to(root.path) or root.identity in destination_chain):
                raise RetouchingError("DESTINATION_INSIDE_SOURCE", f"Choose a destination outside recursively indexed source {root.path}.")
            same_scratch = scratch == root.path or (scratch == scratch_parent and scratch_chain[0] == root.identity)
            nested_scratch = scratch.is_relative_to(root.path) or root.identity in scratch_chain
            if same_scratch or (index.recursive and nested_scratch):
                raise RetouchingError("SCRATCH_INSIDE_SOURCE", f"Choose a temporary folder outside the scanned source {root.path}.")
        return destination, scratch, scratch_parent

    @staticmethod
    def _plan_images(selected, destination, context, settings):
        planned = []
        for image in selected:
            context.update(image_id=image.image_id, source_path=image.path)
            if image.header is None:
                raise RetouchingError("BATCH_INDEX_INCOMPLETE",
                                      f"No disk estimate for {image.path}. Fix and re-index the image or remove it from the selection.")
            try:
                unchanged = current_file_stamp(image.path) == image.stamp
            except OSError as exc:
                raise RetouchingError("SOURCE_CHANGED", f"Cannot reopen {image.path}. Re-index the sources.") from exc
            if not unchanged:
                raise RetouchingError("SOURCE_CHANGED", f"Source changed since indexing: {image.path}. Re-index the sources.")
            relative = export_relative_path(image.relative_path, settings)
            planned.append(PlannedImage(image, destination / relative))
        context.pop("image_id")
        context.pop("source_path")
        return tuple(planned)

    @staticmethod
    def _check_outputs(planned, destination, scratch, index, decision, context):
        outputs = set()
        output_directories = set()
        probes = {}
        collisions = {}
        reviewed = dict(decision.files) if decision else {}
        originals = {image.stamp.identity for image in index.images if image.stamp is not None}
        checked = []
        for item in planned:
            image = item.source
            context.update(image_id=image.image_id, source_path=image.path)
            output, existing_parent = output_path(destination, item.output.relative_to(destination), allow_existing=True)
            if (output in outputs or output in output_directories or any(parent in outputs for parent in output.parents)
                    or scratch.is_relative_to(output)):
                raise RetouchingError("OUTPUT_COLLISION", f"Planned paths conflict at {output}. Change the source selection or folders.")
            outputs.add(output)
            output_directories.update(output.parents)
            stamp = existing_output_stamp(output)
            if stamp is not None:
                if stamp.identity in originals:
                    raise RetouchingError("OUTPUT_IS_SOURCE", f"An export would replace an indexed source: {output}. Choose another destination.")
                collisions[output] = stamp
            skip = decision is not None and decision.action == "skip" and output in reviewed
            checked.append(PlannedImage(image, output, None if skip else stamp, skip))
            if not skip:
                probes[existing_parent, output.parent != existing_parent] = None
        context.pop("image_id")
        context.pop("source_path")
        if any(reviewed.get(path) != stamp for path, stamp in collisions.items()):
            raise ExistingOutputs(collisions.items())
        return tuple(checked), probes

    def _emit_failure(self, error, **context):
        self.diagnostics.emit(error.code, str(error), level="ERROR", component="batch", exc_info=True, **context)
