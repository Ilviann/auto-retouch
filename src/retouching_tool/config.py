"""Machine-local settings, independent of widgets and inference packages."""

from dataclasses import dataclass
from pathlib import Path

from .errors import RetouchingError
from .json_io import read_json

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LOG_LIMITS = {"max_file_mb": 1024, "max_total_mb": 10240, "retention_days": 3650}
SCALING_METHODS = ("bilinear", "nearest")


@dataclass(frozen=True)
class ScalingSettings:
    downscale: str = "bilinear"
    upscale: str = "bilinear"


@dataclass(frozen=True)
class FolderSettings:
    checkpoints: str = "models"
    temporary: str = ".work"
    logs: str = "logs"
    pipelines: str = "pipelines"


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
    pipelines: Path
    logging: LoggingSettings
    using_defaults: bool
    folders: FolderSettings
    scaling: ScalingSettings = ScalingSettings()


def _keys(data, allowed, label):
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise RetouchingError("CONFIG_INVALID", f"Invalid or unknown {label} fields; allowed: {', '.join(allowed)}")


def load_config(root: Path, explicit: Path | None = None) -> AppConfig:
    path = (explicit if explicit is not None else root / "config.json").resolve()
    defaults = not path.exists() and explicit is None
    data = {"schema_version": 1} if defaults else read_json(path)
    return parse_config(path, data, defaults=defaults, app_root=root.resolve())


def parse_config(path: Path, data, *, defaults=False, app_root=None) -> AppConfig:
    _keys(data, ["schema_version", "paths", "logging", "scaling"], "configuration")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise RetouchingError("CONFIG_INVALID", "Configuration schema_version must be 1")
    paths = data.get("paths", {})
    names = ("checkpoints", "temporary", "logs", "pipelines")
    _keys(paths, names, "paths")
    resolved = {}
    if "pipelines" not in paths and app_root is not None and app_root != path.parent:
        paths = {**paths, "pipelines": str(app_root / "pipelines")}
    folders = FolderSettings(**paths)
    for name in names:
        value = getattr(folders, name)
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise RetouchingError("CONFIG_INVALID", f"paths.{name} must be a nonempty path")
        folder = Path(value)
        if folder.anchor and not folder.is_absolute():
            raise RetouchingError("CONFIG_INVALID", f"paths.{name} needs an absolute or configuration-relative path")
        try:
            resolved[name] = (path.parent / folder).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise RetouchingError("CONFIG_INVALID", f"Cannot resolve paths.{name} ({value}): {exc}") from exc
        if resolved[name].is_relative_to(path):
            raise RetouchingError("CONFIG_INVALID", f"paths.{name} must be a folder separate from the configuration file")
    if resolved["logs"] == resolved["temporary"] or resolved["logs"].is_relative_to(resolved["temporary"]):
        raise RetouchingError("CONFIG_INVALID", "Persistent logs must be outside temporary artifacts")
    settings = data.get("logging", {})
    _keys(settings, ["level", "max_file_mb", "max_total_mb", "retention_days"], "logging")
    level = settings.get("level", "INFO")
    if level not in LOG_LEVELS:
        raise RetouchingError("CONFIG_INVALID", "logging.level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    logging_settings = LoggingSettings(**settings)
    for name, maximum in LOG_LIMITS.items():
        value = getattr(logging_settings, name)
        if type(value) is not int or not 1 <= value <= maximum:
            raise RetouchingError("CONFIG_INVALID", f"logging.{name} must be an integer in 1..{maximum}")
    if logging_settings.max_total_mb < logging_settings.max_file_mb:
        raise RetouchingError("CONFIG_INVALID", "logging.max_total_mb must be at least max_file_mb")
    scaling_data = data.get("scaling", {})
    _keys(scaling_data, ["downscale", "upscale"], "scaling")
    scaling = ScalingSettings(**scaling_data)
    for direction in ("downscale", "upscale"):
        if getattr(scaling, direction) not in SCALING_METHODS:
            raise RetouchingError("CONFIG_INVALID", f"scaling.{direction} must be one of {', '.join(SCALING_METHODS)}")
    return AppConfig(path, **resolved, logging=logging_settings, using_defaults=defaults, folders=folders, scaling=scaling)
