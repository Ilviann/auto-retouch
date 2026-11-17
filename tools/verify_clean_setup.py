"""Opt-in setup acceptance: isolated Unicode copy, real installs and backend switch.

Run ONLY for setup-script improvements or bug fixes, using the prepared interpreter.
The main development environment stays on DirectML. This is outside unittest discovery
because it installs packages (never weights) and may contact the package index.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    work = ROOT / ".test-work"
    work.mkdir(exist_ok=True)
    evidence = []
    with tempfile.TemporaryDirectory(prefix="чистая установка ", dir=work) as folder:
        application = Path(folder) / "Auto Retouch пример"
        application.mkdir()
        for name in ("prepare_env.cmd", "run.cmd", "pyproject.toml"):
            shutil.copy2(ROOT / name, application / name)
        for name in ("tools", "src", "requirements"):
            shutil.copytree(ROOT / name, application / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
        python = application / ".venv" / "Scripts" / "python.exe"
        cfg_time = None
        for label, choice, backend in (("clean CPU", "3", "cpu"), ("repeat CPU", "3", "cpu"),
                                       ("switch to DirectML", "2", "directml"),
                                       ("repeat DirectML", "2", "directml"),
                                       ("switch back to CPU", "3", "cpu")):
            print(f"Checking {label} in isolated Unicode path...", flush=True)
            result = subprocess.run(["cmd", "/d", "/c", str(application / "prepare_env.cmd")],
                                    input=choice + "\n", cwd=work, capture_output=True,
                                    text=True, errors="replace", timeout=900,
                                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            if result.returncode:
                raise RuntimeError(f"{label} failed: {result.stdout}\n{result.stderr}")
            metadata = json.loads((application / ".venv" / "retouching-backend.json").read_text(encoding="utf-8"))
            assert metadata["ready"] is True and metadata["backend"] == backend
            current_time = (application / ".venv" / "pyvenv.cfg").stat().st_mtime_ns
            if cfg_time is not None:
                assert current_time == cfg_time, "Repeated setup recreated the environment"
            cfg_time = current_time
            check = subprocess.run(["cmd", "/d", "/c", str(application / "prepare_env.cmd"), "--check"],
                                   cwd=work, capture_output=True, text=True, errors="replace", timeout=120)
            assert check.returncode == 0, check.stdout + check.stderr
            evidence.append({"case": label, "backend": backend, "exit_code": result.returncode,
                             "check_exit_code": check.returncode})
        result = subprocess.run(["cmd", "/d", "/c", str(application / "run.cmd"), "--help"],
                                cwd=work, capture_output=True, text=True, errors="replace", timeout=60)
        assert result.returncode == 0 and "--source" in result.stdout, result.stderr
        evidence.append({"case": "launcher from outside Unicode application path", "exit_code": result.returncode})
    # TemporaryDirectory owns this generated subtree and cleans only that subtree.
    target = work / "clean-setup-result.json"
    target.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"All isolated setup checks passed. Evidence: {target}")


if __name__ == "__main__":
    main()
