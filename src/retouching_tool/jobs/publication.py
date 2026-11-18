"""Recovery proof for a verified export and its approved publication target."""

from dataclasses import asdict, dataclass
import json
import re

from ..errors import RetouchingError
from ..storage.artifact_codec import digest, fields, require
from ..storage.batch_paths import (FileStamp, check_output_target, existing_output_stamp, file_identity,
                                   output_path, publish_pending, stamp_and_hash)
from .snapshot import parse_stamp


@dataclass(frozen=True)
class PreparedExport:
    pending_name: str
    stamp: FileStamp
    sha256: str

    @classmethod
    def capture(cls, pending):
        stamp, checksum = stamp_and_hash(pending)
        return cls(pending.name, stamp, checksum)

    @classmethod
    def parse(cls, encoded):
        data = json.loads(encoded)
        fields(data, "pending_name stamp sha256")
        require(re.fullmatch(r"\.auto-retouch-[a-z0-9_]+\.pending", data["pending_name"])
                and digest(data["sha256"]), "Invalid verified export receipt")
        return cls(data["pending_name"], parse_stamp(data["stamp"]), data["sha256"])

    def encode(self):
        return asdict(self)

    def verify(self, path):
        try:
            _, actual = stamp_and_hash(path, self.stamp)
        except OSError as exc:
            raise RetouchingError("JOB_EXPORT_CONFLICT", f"File does not match this job's verified export: {path}. Inspect it before retrying.") from exc
        if actual != self.sha256:
            raise RetouchingError("JOB_EXPORT_CONFLICT", f"Export checksum differs from this job's verified export: {path}. Inspect it before retrying.")


def check_destination(job):
    path = job.destination
    try:
        unchanged = path.resolve(strict=True) == path and path.is_dir() and file_identity(path.stat()) == job.destination_identity
    except OSError as exc:
        raise RetouchingError("JOB_FOLDER_CHANGED", f"Cannot reopen the frozen destination {path}. Restore access before resuming.") from exc
    if not unchanged:
        raise RetouchingError("JOB_FOLDER_CHANGED", f"Frozen destination changed at {path}. Restore the original folder before resuming.")


def checked_output(job, item):
    output, existing = output_path(job.destination, job.output_relative(item), allow_existing=True)
    check_output_target(output, item.replace_stamp)
    return output, existing


def prepare_parent(job, item):
    check_destination(job)
    output, existing = checked_output(job, item)
    for part in output.parent.relative_to(existing).parts:
        existing /= part
        existing.mkdir(exist_ok=True)
        checked_output(job, item)
    return output


def _exists(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def recover_export(job, item, receipt):
    """Return a published file only if it matches the recorded verified artifact."""
    check_destination(job)
    output, _ = output_path(job.destination, job.output_relative(item), allow_existing=True)
    current = existing_output_stamp(output)
    if current is not None and current != item.replace_stamp:
        receipt.verify(output)
        return output
    pending = output.with_name(receipt.pending_name)
    if not _exists(pending):
        return None
    receipt.verify(pending)
    try:
        publish_pending(pending, output, item.replace_stamp)
    except FileExistsError as exc:
        raise RetouchingError("OUTPUT_COLLISION", f"An export appeared during recovery: {output}. Existing file was preserved.") from exc
    return output
