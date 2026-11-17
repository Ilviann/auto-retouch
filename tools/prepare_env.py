"""Standard-library bootstrap, invoked by prepare_env.cmd using system Python 3.14."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from retouching_tool import __version__
from retouching_tool.diagnostics.service import Diagnostics, redact
from retouching_tool.environment import (RUNTIMES, lock_identity, probe_backend, read_ready_backend,
                                        validate_packages, validate_python, validate_venv)
from retouching_tool.errors import RetouchingError
from retouching_tool.json_io import atomic_json


def run(command, log, *, timeout=900):
    log.emit("SETUP_COMMAND", "Running environment validation/install", component="setup", command=command)
    # Disk-spooled output avoids holding package-manager logs unboundedly in RAM.
    import tempfile
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=timeout, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                                                         "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                                                         "AUTO_RETOUCH_PARENT_SESSION": log.session_id})
        except subprocess.TimeoutExpired as exc:
            raise RetouchingError("SETUP_TIMEOUT", f"Command exceeded {timeout}s; rerun setup after checking connectivity") from exc
        output.seek(0, 2)
        size = output.tell()
        output.seek(max(0, size - 24000))
        detail = redact(output.read().decode("utf-8", errors="replace"))
    if result.returncode:
        raise RetouchingError("SETUP_COMMAND_FAILED", f"Command exited {result.returncode}: {detail}")
    log.emit("SETUP_COMMAND_OK", detail or "Command succeeded", component="setup", exit_code=0)


def select_backend(input_fn=input):
    print("Select inference backend:\n  1. CUDA (default)\n  2. DirectML\n  3. CPU")
    for _ in range(3):
        try:
            choice = input_fn("Choice [1]: ").strip()
        except EOFError as exc:
            raise RetouchingError("SETUP_INPUT_REQUIRED", "Setup requires an interactive backend selection; rerun prepare_env.cmd in a console") from exc
        if choice in ("", "1", "2", "3"):
            return {"": "cuda", "1": "cuda", "2": "directml", "3": "cpu"}[choice]
        print("Enter 1, 2, or 3.")
    raise RetouchingError("SETUP_SELECTION_INVALID", "No valid backend selected after three attempts; rerun prepare_env.cmd")


def prepare(root, log, *, check=False, input_fn=input, runner=run):
    validate_python()
    environment = root / ".venv"
    python = environment / "Scripts" / "python.exe"
    metadata = environment / "retouching-backend.json"
    if environment.is_symlink() or environment.is_junction() or environment.resolve().parent != root.resolve():
        raise RetouchingError("ENVIRONMENT_INVALID", ".venv must be a real project-local directory, not a link/junction to another environment")
    if environment.exists():
        if not python.is_file():
            raise RetouchingError("ENVIRONMENT_INVALID", f"{environment} is damaged; move it aside explicitly and rerun setup")
        runner([str(python), "-B", str(root / "tools" / "prepare_env.py"), "--internal-environment-check"], log, timeout=60)
    elif check:
        raise RetouchingError("ENVIRONMENT_MISSING", "No .venv exists; run prepare_env.cmd without --check")
    else:
        venv.EnvBuilder(with_pip=True).create(environment)
        log.emit("ENVIRONMENT_CREATED", "Created project-local Python 3.14 environment", component="setup")
    if check:
        backend = read_ready_backend(root)
    else:
        if metadata.exists():
            # Display even stale metadata without trusting readiness.
            try:
                print(f"Currently prepared backend: {read_ready_backend(root)}")
            except RetouchingError as exc:
                log.emit("BACKEND_METADATA_STALE", str(exc), level="WARNING", component="setup")
        backend = select_backend(input_fn)
        # Readiness is invalidated BEFORE any package update, including same-profile updates.
        metadata.unlink(missing_ok=True)
        log.emit("BACKEND_SELECTED", f"Preparing {backend}; readiness invalidated until checks pass", component="setup", backend=backend)
        runner([str(python), "-B", str(root / "tools" / "prepare_env.py"), "--internal-remove-conflicts", backend], log, timeout=120)
        runner([str(python), "-m", "pip", "install", "--require-hashes", "--only-binary=:all:", "--no-deps",
                "-r", str(root / "requirements" / f"{backend}.lock")], log)
        runner([str(python), "-m", "pip", "install", "--no-deps", "--no-build-isolation", "-e", str(root)], log)
    runner([str(python), "-m", "pip", "check"], log, timeout=60)
    runner([str(python), "-B", str(root / "tools" / "prepare_env.py"), "--internal-probe", backend], log, timeout=120)
    if not check:
        atomic_json(metadata, {"schema_version": 1, "ready": True, "backend": backend,
                               "lock_sha256": lock_identity(root, backend), "app_version": __version__,
                               "python": sys.version, "prepared_at": datetime.now(timezone.utc).isoformat()})
    log.emit("ENVIRONMENT_READY", f"{backend} environment {'checked (no environment changes)' if check else 'prepared'}", component="setup", backend=backend)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # These private child commands validate a selection already made by setup.
    if argv and argv[0].startswith("--internal-"):
        log = Diagnostics(ROOT / "logs")
        try:
            if not os.environ.get("AUTO_RETOUCH_PARENT_SESSION"):
                raise RetouchingError("SETUP_ARGUMENT_INVALID", "Internal setup commands are child-process checks, not public backend options; run prepare_env.cmd")
            validate_venv(ROOT)
            if argv == ["--internal-environment-check"]:
                return 0
            if len(argv) != 2 or argv[1] not in RUNTIMES:
                raise RetouchingError("SETUP_ARGUMENT_INVALID", "Invalid internal setup invocation")
            backend = argv[1]
            if argv[0] == "--internal-remove-conflicts":
                from importlib import metadata
                installed = []
                for package in RUNTIMES.values():
                    try:
                        metadata.version(package)
                        installed.append(package)
                    except metadata.PackageNotFoundError:
                        continue
                if any(p != RUNTIMES[backend] for p in installed):
                    run([sys.executable, "-m", "pip", "uninstall", "-y", *installed], log)
            elif argv[0] == "--internal-probe":
                validate_packages(ROOT, backend)
                from retouching_tool.ui.qt.startup import verify_imports
                log.emit("GUI_IMPORT_OK", "Qt Widgets imported without creating a GUI", component="setup", qt_version=verify_imports())
                log.emit("BACKEND_PROBE_OK", "Generated Add graph executed on selected provider", component="setup", **asdict(probe_backend(backend)))
            else:
                raise RetouchingError("SETUP_ARGUMENT_INVALID", "Unknown internal setup command")
            return 0
        except Exception as exc:
            log.emit(getattr(exc, "code", "SETUP_FAILED"), str(exc), level="ERROR", component="setup", exc_info=True)
            return 1
        finally:
            log.close()
    parser = argparse.ArgumentParser(description="Prepare Auto Retouch's local Python 3.14 environment")
    parser.add_argument("--check", action="store_true", help="Validate only; no selection, installs, downloads, or environment changes")
    args = parser.parse_args(argv)
    log = Diagnostics(ROOT / "logs")
    try:
        log.emit("SETUP_STARTED", "Checking interpreter and project environment", component="setup",
                 python=sys.version, executable=sys.executable, check_only=args.check)
        prepare(ROOT, log, check=args.check)
        return 0
    except Exception as exc:
        log.emit(getattr(exc, "code", "SETUP_FAILED"), str(exc), level="ERROR", component="setup", exc_info=True)
        print(log.failure if log.failure else f"Setup failed. Diagnostics: {log.path}", file=sys.stderr)
        return 1
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
