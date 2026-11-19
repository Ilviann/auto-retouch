"""Machine-local desktop settings, merged atomically across commands and launches."""

from ..errors import RetouchingError
from ..json_io import atomic_json, exclusive_file, read_json
from ..storage.batch_paths import current_file_stamp

STATE_LIMIT = 4 * 1024 * 1024


class WorkspaceState:
    def __init__(self, path):
        self.path = path

    def read(self):
        with exclusive_file(self.path.with_suffix(".json.lock"), wait=3):
            return self._read()

    def _read(self):
        if not self.path.exists():
            return {"schema_version": 1}
        data = read_json(self.path, limit=STATE_LIMIT)
        if (not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1
                or data.keys() - {"schema_version", "pipeline", "preset", "sources", "windows"}):
            raise RetouchingError("STATE_INVALID", f"Unsupported desktop state in {self.path}. Check its format.")
        return data

    def update(self, **changes):
        with exclusive_file(self.path.with_suffix(".json.lock"), wait=3):
            data = self._read()
            if "windows" in changes:
                windows = data.get("windows")
                changes["windows"] = {**(windows if isinstance(windows, dict) else {}), **changes["windows"]}
            if any(data.get(key) != value for key, value in changes.items()):
                atomic_json(self.path, {**data, **changes}, limit=STATE_LIMIT)

    def migrate(self, diagnostics):
        legacy = self.path.parent / ".state"
        if not legacy.is_dir() or legacy.resolve() != legacy:
            return
        with exclusive_file(self.path.with_suffix(".json.lock"), wait=3):
            data = self._read()
            additions = {}
            copied = {}
            for name, key in (("last-pipeline.json", "pipeline"), ("last-preset.json", "preset"),
                              ("session.json", "sources")):
                path = legacy / name
                if not path.exists():
                    continue
                try:
                    stamp = current_file_stamp(path)
                    value = read_json(path)
                    if key == "sources":
                        if (not isinstance(value, dict) or set(value) != {"sources", "schema_version"}
                                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                                or not isinstance(value["sources"], list)):
                            raise ValueError("Invalid legacy source selection")
                        value = value["sources"]
                    if key in data and data[key] != value:
                        continue  # A newer selection takes precedence. Preserve the old file.
                    additions[key] = value
                    copied[path] = stamp
                except (OSError, RetouchingError, ValueError):
                    diagnostics.emit("STATE_MIGRATION_SKIPPED", "Could not import this legacy settings file.",
                                     component="application", level="WARNING", exc_info=True, state_path=path)
            if copied:
                if additions.keys() - data.keys():
                    atomic_json(self.path, {**data, **additions}, limit=STATE_LIMIT)
                for path, stamp in copied.items():
                    try:
                        if current_file_stamp(path) == stamp:
                            path.unlink()
                    except OSError:
                        diagnostics.emit("STATE_MIGRATION_CLEANUP_FAILED", "Imported settings. Could not remove the old file.",
                                         component="application", level="WARNING", exc_info=True, state_path=path)
                try:
                    if not any(legacy.iterdir()):
                        legacy.rmdir()
                except OSError:
                    diagnostics.emit("STATE_MIGRATION_CLEANUP_FAILED", "Imported settings. Could not remove the old directory.",
                                     component="application", level="WARNING", exc_info=True, state_path=legacy)

    def window_size(self, name):
        windows = self.read().get("windows", {})
        if isinstance(windows, dict) and name not in windows:
            return None
        size = windows.get(name) if isinstance(windows, dict) else None
        if (not isinstance(size, dict) or not all(type(size.get(key)) is int and 0 < size[key] <= 65535
                                                  for key in ("width", "height"))
                or type(size.get("maximized")) is not bool):
            raise RetouchingError("WINDOW_SIZE_INVALID", f"Invalid saved size for {name} in {self.path}")
        return size
