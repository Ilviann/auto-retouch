"""Filesystem checks shared by headless batch preparation and future workers."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import tempfile

from ..errors import RetouchingError


def is_reparse(info):
    return bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def file_identity(info):
    if not info.st_ino:
        raise OSError("The filesystem did not report a file identity")
    return info.st_dev, info.st_ino


@dataclass(frozen=True)
class FileStamp:
    identity: tuple[int, int]
    size: int
    modified_ns: int

    @classmethod
    def capture(cls, info):
        if not stat.S_ISREG(info.st_mode):
            raise OSError("Expected a regular file")
        return cls(file_identity(info), info.st_size, info.st_mtime_ns)


def current_file_stamp(path):
    if path.resolve(strict=True) != path or is_reparse(path.lstat()):
        raise OSError(f"File path now uses an alias: {path}")
    return FileStamp.capture(path.stat())


def stamp_and_hash(path, expected=None):
    """Hash one stable regular file using a bounded read buffer."""
    stamp = current_file_stamp(path)
    if expected is not None and stamp != expected:
        raise OSError(f"File identity or attributes changed: {path}")
    with path.open("rb") as stream:
        if FileStamp.capture(os.fstat(stream.fileno())) != stamp:
            raise OSError(f"File changed while it was opened: {path}")
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if FileStamp.capture(os.fstat(stream.fileno())) != stamp or current_file_stamp(path) != stamp:
            raise OSError(f"File changed while it was hashed: {path}")
    return stamp, checksum


def resolve_folder(path: Path, *, allow_missing=False):
    """Return the canonical target and its nearest existing directory."""
    existing = path.absolute()
    missing = []
    while True:
        try:
            existing.lstat()  # Keep dangling links distinct from absent directories.
        except FileNotFoundError:
            if not allow_missing or existing == existing.parent or os.path.isreserved(existing.name):
                raise
            missing.append(existing.name)
            existing = existing.parent
        else:
            break
    existing = existing.resolve(strict=True)
    if not stat.S_ISDIR(existing.stat().st_mode):
        raise NotADirectoryError(f"Expected a directory at {existing}")
    return existing.joinpath(*reversed(missing)), existing


def directory_chain(existing: Path):
    return tuple(file_identity(path.stat()) for path in (existing, *existing.parents))


def output_path(destination: Path, relative: Path, *, allow_existing=False):
    """Validate an export below the canonical destination and locate its existing parent."""
    if relative.anchor or not relative.parts or any(part == ".." or os.path.isreserved(part)
                                                    for part in relative.parts):
        raise RetouchingError("OUTPUT_PATH_INVALID", f"Output filename is invalid on Windows: {relative}")
    parent = destination
    for part in relative.parts[:-1]:
        child = parent / part
        try:
            info = child.lstat()
        except FileNotFoundError:
            break  # Remaining ancestors must be created at execution time.
        if not stat.S_ISDIR(info.st_mode) or is_reparse(info) or child.resolve(strict=True) != child:
            raise RetouchingError("OUTPUT_PARENT_INVALID",
                                  f"Output parent must be a regular directory without aliases: {child}")
        parent = child
    output = destination / relative
    if allow_existing:
        return output, parent
    try:
        output.lstat()
    except FileNotFoundError:
        return output, parent
    raise RetouchingError("OUTPUT_COLLISION", f"Output already exists: {output}. Choose a different destination or input selection.")


def probe_writable(directory: Path, *, needs_subdirectory: bool):
    # TemporaryDirectory also checks permission to create a missing subfolder.
    if needs_subdirectory:
        with tempfile.TemporaryDirectory(dir=directory, prefix=".auto-retouch-check-") as probe:
            probe_writable(Path(probe), needs_subdirectory=False)
    else:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".auto-retouch-check-") as stream:
            stream.write(b"\0")
            stream.flush()
