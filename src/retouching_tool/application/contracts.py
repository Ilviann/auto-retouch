from dataclasses import dataclass
from pathlib import Path
from ..pipeline.validation import StageStatus


@dataclass(frozen=True)
class StartupSnapshot:
    sources: tuple[Path, ...]
    pipeline_name: str
    pipeline_origin: str
    backend: str
    checkpoints: Path
    temporary: Path
    logs: Path
    notices: tuple[str, ...]
    processing_available: bool = False
    stages: tuple[StageStatus, ...] = ()
    pytorch_backend: str | None = None
