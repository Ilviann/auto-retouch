"""Create a stage-owned ONNX session on the prepared inference backend."""

from ..environment import PROVIDERS
from ..errors import RetouchingError


def open_session(path, backend):
    import onnxruntime as ort

    provider = PROVIDERS[backend]
    if provider not in ort.get_available_providers():
        raise RetouchingError("AI_PROVIDER_UNAVAILABLE", f"{provider} is unavailable. Check the prepared runtime and driver.")
    options = ort.SessionOptions()
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.enable_mem_pattern = False
    options.enable_cpu_mem_arena = False
    if backend != "cpu":
        options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    providers = [(provider, {"arena_extend_strategy": "kSameAsRequested"})] if backend == "cuda" else [provider]
    try:
        if backend == "cuda":
            ort.preload_dlls()
        session = ort.InferenceSession(str(path), sess_options=options, providers=providers, enable_fallback=False)
    except Exception as exc:
        raise RetouchingError("AI_SESSION_FAILED", f"Cannot load the ONNX checkpoint on {provider}. "
                              "Check checkpoint compatibility, device memory, and the driver.") from exc
    if provider not in session.get_providers():
        raise RetouchingError("AI_PROVIDER_UNAVAILABLE", f"The ONNX session did not activate {provider}. Check the driver.")
    return session
