"""Environment readiness and a generated, weight-free ONNX provider probe."""

from dataclasses import dataclass, replace
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import struct
import sys
import tempfile

from . import __version__
from .errors import RetouchingError
from .json_io import read_json

RUNTIMES = {"cpu": "onnxruntime", "directml": "onnxruntime-directml", "cuda": "onnxruntime-gpu"}
PROVIDERS = {"cpu": "CPUExecutionProvider", "directml": "DmlExecutionProvider", "cuda": "CUDAExecutionProvider"}


@dataclass(frozen=True)
class BackendInfo:
    backend: str
    runtime_version: str
    available_providers: tuple[str, ...]
    executed_providers: tuple[str, ...]
    result: tuple[float, ...]
    pytorch_backend: str | None = None
    pytorch_version: str | None = None


def validate_python() -> None:
    if (sys.version_info[:2] != (3, 14) or struct.calcsize("P") != 8
            or platform.machine().lower() not in ("amd64", "x86_64") or platform.python_implementation() != "CPython"):
        raise RetouchingError("PYTHON_INCOMPATIBLE", "Use standard 64-bit x86 CPython 3.14; no Python downgrade is supported")
    if sys.platform != "win32" or sys.getwindowsversion().build < 22000:
        raise RetouchingError("PLATFORM_UNSUPPORTED", "Auto ReTouch requires Windows 11 (build 22000 or later)")
    if hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled():
        raise RetouchingError("PYTHON_INCOMPATIBLE", "Use standard CPython 3.14 rather than the free-threaded build")


def validate_venv(root: Path) -> None:
    validate_python()
    environment = root / ".venv"
    if environment.is_symlink() or environment.is_junction() or environment.resolve().parent != root.resolve():
        raise RetouchingError("ENVIRONMENT_INVALID", ".venv must be a real project-local directory, not a link/junction")
    if Path(sys.prefix).resolve() != (root / ".venv").resolve() or sys.prefix == sys.base_prefix:
        raise RetouchingError("ENVIRONMENT_INVALID", f"Use {root / '.venv' / 'Scripts' / 'python.exe'}; rerun prepare_env.cmd")
    cfg = (root / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8")
    if "include-system-site-packages = false" not in cfg.lower():
        raise RetouchingError("ENVIRONMENT_INVALID", "Environment must not inherit system site packages; recreate it explicitly")


def lock_files(backend, pytorch_backend):
    if backend not in RUNTIMES:
        raise RetouchingError("BACKEND_INVALID", "Unknown prepared backend; rerun prepare_env.cmd")
    if pytorch_backend not in ("cpu", "cuda"):
        raise RetouchingError("BACKEND_INVALID", "Choose CPU or CUDA for PyTorch in prepare_env.cmd")
    return ("shared.lock", f"{backend}.lock", "pytorch-common.lock", f"pytorch-{pytorch_backend}.lock")


def lock_identity(root: Path, backend: str, pytorch_backend: str) -> str:
    digest = hashlib.sha256()
    for name in lock_files(backend, pytorch_backend):
        digest.update(name.encode())
        digest.update((root / "requirements" / name).read_bytes())
    return digest.hexdigest()


def locked_versions(root: Path, backend: str, pytorch_backend: str) -> dict[str, str]:
    result = {}
    for name in lock_files(backend, pytorch_backend):
        for line in (root / "requirements" / name).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "==" in line and not line.startswith("#"):
                package, version = line.split()[0].split("==")
                result[package] = version
    return result


def validate_packages(root: Path, backend: str, pytorch_backend: str) -> None:
    expected = locked_versions(root, backend, pytorch_backend)
    for package, version in expected.items():
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError as exc:
            raise RetouchingError("DEPENDENCY_MISSING", f"Missing {package}=={version}; rerun prepare_env.cmd") from exc
        if actual != version:
            raise RetouchingError("DEPENDENCY_MISMATCH", f"{package}: installed {actual}, expected {version}; rerun prepare_env.cmd")
    for package in set(RUNTIMES.values()) - {RUNTIMES[backend]}:
        try:
            metadata.version(package)
        except metadata.PackageNotFoundError:
            continue
        raise RetouchingError("BACKEND_CONFLICT", f"Conflicting {package} installed; rerun prepare_env.cmd")
    try:
        app_version = metadata.version("retouching-tool")
    except metadata.PackageNotFoundError as exc:
        raise RetouchingError("DEPENDENCY_MISSING", "Application package is not installed; rerun prepare_env.cmd") from exc
    if app_version != __version__:
        raise RetouchingError("DEPENDENCY_MISMATCH", "Application package version changed; rerun prepare_env.cmd")


def _varint(value: int) -> bytes:
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _number(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _message(field: int, value: bytes | str) -> bytes:
    value = value.encode() if isinstance(value, str) else value
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def probe_graph() -> bytes:
    """ONNX IR 8/opset 13: z = Add(x,y), float32[2]. No trained assets.

    Encode the tiny public protobuf schema directly to avoid a build-time ONNX
    compiler dependency. All inputs are supplied at runtime, so it cannot fold.
    """
    def value_info(name):
        shape = _message(1, _number(1, 2))
        tensor_type = _number(1, 1) + _message(2, shape)
        return _message(1, name) + _message(2, _message(1, tensor_type))

    node = _message(1, "x") + _message(1, "y") + _message(2, "z") + _message(4, "Add")
    graph = _message(1, node) + _message(2, "auto-retouch-provider-probe")
    graph += _message(11, value_info("x")) + _message(11, value_info("y")) + _message(12, value_info("z"))
    return _number(1, 8) + _message(2, "Auto ReTouch") + _message(7, graph) + _message(8, _number(2, 13))


def probe_backend(backend: str) -> BackendInfo:
    import numpy as np
    import onnxruntime as ort

    provider = PROVIDERS[backend]
    available = tuple(ort.get_available_providers())
    if provider not in available:
        raise RetouchingError("BACKEND_UNAVAILABLE", f"{provider} is unavailable (found {available}); rerun setup for a supported backend")
    with tempfile.TemporaryDirectory(prefix="auto-retouch-provider-") as folder:
        options = ort.SessionOptions()
        options.enable_profiling = True
        options.profile_file_prefix = str(Path(folder) / "probe")
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_mem_pattern = False
        if backend != "cpu":
            options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
        try:
            if backend == "cuda":
                ort.preload_dlls()
            session = ort.InferenceSession(probe_graph(), sess_options=options, providers=[provider])
            session.disable_fallback()
            actual = tuple(session.get_providers())
            if provider not in actual:
                raise RetouchingError("BACKEND_UNAVAILABLE", f"Runtime did not create {provider}; CPU fallback is not accepted")
            output = session.run(None, {"x": np.array([1, 2], dtype=np.float32), "y": np.array([3, 4], dtype=np.float32)})[0]
            profile = Path(session.end_profiling())
            del session
            events = json.loads(profile.read_text(encoding="utf-8"))
            executed = tuple(sorted({event.get("args", {}).get("provider") for event in events
                                     if event.get("args", {}).get("provider")}))
            if not np.array_equal(output, np.array([4, 6], dtype=np.float32)) or executed != (provider,):
                raise RetouchingError("BACKEND_PROBE_FAILED", f"Expected Add on {provider}, got {output.tolist()} on {executed}")
        except RetouchingError:
            raise
        except Exception as exc:
            raise RetouchingError("BACKEND_PROBE_FAILED", f"Cannot execute Add on {provider}: {exc}. Check drivers/runtime libraries or rerun setup") from exc
    return BackendInfo(backend, ort.__version__, available, executed, tuple(float(v) for v in output))


def probe_backends(onnx_backend, pytorch_backend):
    onnx_info = probe_backend(onnx_backend)
    try:
        import torch
        if pytorch_backend not in ("cuda", "cpu") or (torch.version.cuda is not None) != (pytorch_backend == "cuda"):
            raise ValueError(f"Installed torch {torch.__version__} does not match the selected build")
        x = torch.tensor([1., 2.], device=pytorch_backend)
        result = x + torch.tensor([3., 4.], device=pytorch_backend)
        if result.device.type != pytorch_backend or result.tolist() != [4., 6.]:
            raise ValueError("Unexpected PyTorch Add result or execution device")
    except Exception as exc:
        raise RetouchingError("PYTORCH_PROBE_FAILED", f"Cannot run PyTorch on {pytorch_backend}: {exc}. "
                              "Check the driver and selected PyTorch build in setup.") from exc
    return replace(onnx_info, pytorch_backend=pytorch_backend, pytorch_version=torch.__version__)


def read_ready_backends(root: Path) -> tuple[str, str]:
    try:
        data = read_json(root / ".venv" / "retouching-backend.json")
    except RetouchingError as exc:
        raise RetouchingError("BACKEND_NOT_READY", "Cannot read backend metadata; rerun prepare_env.cmd") from exc
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data.get("schema_version") != 2 or data.get("ready") is not True):
        raise RetouchingError("BACKEND_NOT_READY", "Run prepare_env.cmd to select both ONNX Runtime and PyTorch backends.")
    backend, pytorch_backend = data.get("onnx_backend"), data.get("pytorch_backend")
    if (not isinstance(backend, str) or backend not in RUNTIMES or pytorch_backend not in ("cuda", "cpu")
            or data.get("lock_sha256") != lock_identity(root, backend, pytorch_backend) or data.get("app_version") != __version__):
        raise RetouchingError("BACKEND_METADATA_INVALID", "Backend metadata is inconsistent with dependency locks/application; rerun prepare_env.cmd")
    return backend, pytorch_backend


def validate_ready(root: Path) -> BackendInfo:
    validate_venv(root)
    backends = read_ready_backends(root)
    validate_packages(root, *backends)
    return probe_backends(*backends)
