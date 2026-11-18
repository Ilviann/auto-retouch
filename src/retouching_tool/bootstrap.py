"""Composition root: parse first, log before config/UI, wire one Qt adapter."""

from dataclasses import asdict
from pathlib import Path
import sys

from . import __version__
from .application.service import ApplicationService
from .cli import parse_args
from .config import load_config
from .diagnostics.service import Diagnostics
from .environment import validate_ready


def main(argv=None) -> int:
    # Help/argument errors never create Qt or load inference/checkpoints.
    options = parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    log = Diagnostics(root / "logs")
    try:
        log.emit("LAUNCH_STARTED", "Starting Auto ReTouch", app_version=__version__, python=sys.version,
                 executable=sys.executable, platform=sys.platform)
        config = load_config(root, options.config)
        if config.logs != log.path.parent or config.logging != log.settings:
            old = log
            log = Diagnostics(config.logs, config.logging, session_id=old.session_id)
            log.emit("LOG_CONFIGURED", "Using configured diagnostic settings", bootstrap_log=old.path,
                     bootstrap_log_failure=old.failure)
            if log.failure:
                old.emit("LOG_CONFIG_FAILED", log.failure, level="WARNING")
            old.close()
        backend = validate_ready(root)
        log.emit("BACKEND_READY", "ONNX Runtime and PyTorch executed on the devices selected in setup", **asdict(backend))
        service = ApplicationService(root, config, log)
        snapshot = service.startup(options, backend.backend, pytorch_backend=backend.pytorch_backend)
        from .ui.qt.startup import run_desktop
        log.emit("GUI_STARTED", "Opening Auto ReTouch; no batch started")
        result = run_desktop(snapshot, service)
        log.emit("LAUNCH_EXITED", "Desktop application closed", exit_code=result)
        return result
    except Exception as exc:
        log.emit(getattr(exc, "code", "STARTUP_FAILED"), str(exc), level="ERROR", exc_info=True)
        if log.failure:
            print(log.failure, file=sys.stderr)
        else:
            print(f"Startup failed. Diagnostics: {log.path}", file=sys.stderr)
        return 1
    finally:
        log.close()
