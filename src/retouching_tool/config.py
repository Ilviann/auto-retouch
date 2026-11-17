"""Machine-local settings, independent of widgets and inference packages."""

from dataclasses import dataclass
from pathlib import Path

from .errors import RetouchingError
from .json_io import read_json


@dataclass(frozen=True)
class LoggingSettings:
    level: str = "INFO"
    max_file_mb: int = 10
    max_total_mb: int = 200
    retention_days: int = 14


@dataclass(frozen=True)
class AppConfig:
    file: Path
    checkpoints: Path
    temporary: Path
    logs: Path
    logging: LoggingSettings
    using_defaults: bool


def _keys(data, allowed, label):
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise RetouchingError("CONFIG_INVALID", f"Invalid or unknown {label} fields; allowed: {', '.join(allowed)}")


def load_config(root: Path, explicit: Path | None = None) -> AppConfig:
    path = (explicit if explicit is not None else root / "config.json").resolve()
    defaults = not path.exists() and explicit is None
    data = {"schema_version": 1} if defaults else read_json(path)
    _keys(data, ["schema_version", "paths", "logging"], "configuration")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise RetouchingError("CONFIG_INVALID", "Configuration schema_version must be 1")
    paths = data.get("paths", {})
    _keys(paths, ["checkpoints", "temporary", "logs"], "paths")
    resolved = {}
    for name, default in (("checkpoints", "checkpoints"), ("temporary", ".work"), ("logs", "logs")):
        value = paths.get(name, default)
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise RetouchingError("CONFIG_INVALID", f"paths.{name} must be a nonempty path")
        resolved[name] = (path.parent / value).resolve()
    if resolved["logs"] == resolved["temporary"] or resolved["logs"].is_relative_to(resolved["temporary"]):
        raise RetouchingError("CONFIG_INVALID", "Persistent logs must be outside temporary artifacts")
    settings = data.get("logging", {})
    _keys(settings, ["level", "max_file_mb", "max_total_mb", "retention_days"], "logging")
    level = settings.get("level", "INFO")
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise RetouchingError("CONFIG_INVALID", "logging.level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    for name, default, maximum in (("max_file_mb", 10, 1024), ("max_total_mb", 200, 10240), ("retention_days", 14, 3650)):
        value = settings.get(name, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise RetouchingError("CONFIG_INVALID", f"logging.{name} must be an integer in 1..{maximum}")
    logging_settings = LoggingSettings(**settings)
    if logging_settings.max_total_mb < logging_settings.max_file_mb:
        raise RetouchingError("CONFIG_INVALID", "logging.max_total_mb must be at least max_file_mb")
    return AppConfig(path, **resolved, logging=logging_settings, using_defaults=defaults)
