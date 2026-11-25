"""Create a stage-owned ONNX session on the prepared inference backend."""

from ..environment import PROVIDERS
from ..errors import RetouchingError


def open_session(path, backend, *, cuda_options=None, free_dimensions=None):
    import onnxruntime as ort

    provider = PROVIDERS[backend]
    if provider not in ort.get_available_providers():
        raise RetouchingError("AI_PROVIDER_UNAVAILABLE", f"{provider} is unavailable. Check the prepared runtime and driver.")
    options = ort.SessionOptions()
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.enable_mem_pattern = False
    options.enable_cpu_mem_arena = False
    for name, size in (free_dimensions or {}).items():
        options.add_free_dimension_override_by_name(name, size)
    if backend != "cpu":
        options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    providers = [provider]
    try:
        if backend == "cuda":
            ort.preload_dlls()
            providers = [(provider, {"arena_extend_strategy": "kSameAsRequested", **(cuda_options or {})})]
        source = path if isinstance(path, bytes) else str(path)
        session = ort.InferenceSession(source, sess_options=options, providers=providers, enable_fallback=False)
    except Exception as exc:
        raise RetouchingError("AI_SESSION_FAILED", f"Cannot load the ONNX graph on {provider}. "
                              "Check graph compatibility, device memory, and the driver.") from exc
    if provider not in session.get_providers():
        raise RetouchingError("AI_PROVIDER_UNAVAILABLE", f"The ONNX session did not activate {provider}. Check the driver.")
    return session
