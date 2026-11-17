"""Strict, bounded JSON reads and atomic writes for local settings."""

import json
import os
import tempfile
from pathlib import Path

from .errors import RetouchingError


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _check_depth(raw, limit=64):
    """Bound nesting before decoding, ignoring brackets inside JSON strings."""
    depth, quoted, escaped = 0, False, False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > limit:
                raise ValueError(f"JSON nesting exceeds {limit} levels")
        elif char in (93, 125):
            depth -= 1


def read_json(path: Path, *, limit: int = 1_048_576):
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError(f"JSON exceeds {limit} bytes")
        _check_depth(raw)
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique,
                          parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise RetouchingError("JSON_INVALID", f"Cannot read {path}: {exc}") from exc


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
