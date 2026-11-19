"""One prepared stage pass, reusable by future schedulers and preview workers."""

from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from time import perf_counter

from ..errors import ResizeApprovalRequired, RetouchingError
from .validation import retained
from .definition import BLEND, PipelineDefinition, StageDefinition, parse_pipeline


def _blend_result(before, result, amount, produced):
    if amount == 1 or result.pixels is before.pixels:
        return result
    if result.geometry_revision != before.geometry_revision:
        raise RetouchingError("STAGE_BLEND_GEOMETRY", "Blending requires aligned stage input and output. "
                              "Use full blending for this geometry change.")
    if any(spec.pixel_sensitive for spec in produced):
        raise RetouchingError("STAGE_BLEND_CONTEXT", "This stage measures its edited pixels. "
                              "Use full blending to keep those measurements valid.")
    if amount == 0:
        return replace(result, pixels=before.pixels, pixel_revision=before.pixel_revision,
                       context=tuple(replace(e, pixel_revision=before.pixel_revision) if e.spec in produced else e
                                     for e in result.context))

    import numpy as np

    # Freeze the blended RGB in a single bytes buffer, with only strip-sized arithmetic temporaries.
    with BytesIO() as output:
        for start in range(0, before.height, 64):
            original = before.pixels[start:start + 64]
            edited = result.pixels[start:start + 64]
            mixed = original + np.float32(amount) * (edited - original)
            output.write(np.ascontiguousarray(mixed))
        pixels = np.frombuffer(output.getvalue(), dtype=np.float32).reshape(before.pixels.shape)
    return replace(result, pixels=pixels)


@dataclass(frozen=True)
class StageRuntime:
    diagnostics: object
    backend: str
    checkpoints: Path
    job_id: str | None
    stage: StageDefinition
    image_id: str | None = None
    attempt: int = 1
    preview_request_id: str | None = None
    resize_approved: bool = False

    def emit(self, code, message, *, level="INFO", exc_info=False, **details):
        self.diagnostics.emit(code, message, level=level, exc_info=exc_info, component="pipeline",
            job_id=self.job_id, image_id=self.image_id, stage_instance_id=self.stage.id,
            operation=self.stage.operation, operation_version=self.stage.operation_version,
            backend=self.backend, attempt=self.attempt, model_ref=self.stage.model_ref,
            preview_request_id=self.preview_request_id, **details)


class StageSession:
    """Always releases attempted preparation, preserving the original failure."""

    def __init__(self, registry, catalog, runtime):
        validated = parse_pipeline(PipelineDefinition("Execution validation", (runtime.stage,)).to_json(),
                                   registry, frozen=True).stages[0]
        self.registry, self.catalog, self.runtime = registry, catalog, replace(runtime, stage=validated)
        self.descriptor = registry.descriptor(validated.operation, validated.operation_version)
        self.operation = None

    def __enter__(self):
        stage = self.runtime.stage
        if not stage.enabled:
            self.runtime.emit("STAGE_SKIPPED", "Stage is disabled; state will pass through unchanged")
            return self
        try:
            issues = self.catalog.check(stage.model_ref, self.runtime.checkpoints, self.runtime.backend)
            if issues or self.runtime.backend not in self.descriptor.backends:
                self.runtime.emit("STAGE_ASSET_CHECK_FAILED", "Execution snapshot cannot use the required local assets/backend", level="ERROR",
                                  issues=[{"code": i.code, "message": i.message, "technical_cause": i.technical_cause} for i in issues])
                raise RetouchingError("STAGE_UNAVAILABLE", "; ".join(i.message for i in issues) or "Operation does not support the prepared backend")
            self.operation = self.registry.create(stage)
            self.operation.prepare(self.runtime)
            self.runtime.emit("STAGE_PREPARED", "Stage resources prepared")
        except BaseException:
            self.runtime.emit("STAGE_PREPARE_FAILED", "Cannot prepare stage; check model files/backend and technical details", level="ERROR", exc_info=True)
            self._release(primary_failure=True)
            raise
        return self

    def process(self, state, *, image_id, attempt=1, diagnostics=None):
        runtime = replace(self.runtime, image_id=image_id, attempt=attempt,
                          diagnostics=self.runtime.diagnostics if diagnostics is None else diagnostics)
        if not runtime.stage.enabled:
            runtime.emit("STAGE_IMAGE_SKIPPED", "Disabled stage passed this image through unchanged")
            return state
        started = perf_counter()
        try:
            if self.operation is None:
                raise RetouchingError("STAGE_NOT_PREPARED", "Prepare stage resources before processing")
            desc = self.descriptor
            if state.representation != desc.representation:
                raise RetouchingError("IMAGE_REPRESENTATION_INVALID", f"{desc.title} requires {desc.representation}")
            keys = [entry.spec.key for entry in state.context]
            if len(keys) != len(set(keys)):
                raise RetouchingError("CONTEXT_INVALID", "Duplicate image context keys")
            for spec in desc.active_optional_inputs(runtime.stage):
                entry = state.entry(spec.key)
                if desc.changes_pixels and not spec.pixel_sensitive and entry is not None and (
                        entry.spec != spec or not entry.compatible(state)):
                    raise RetouchingError("CONTEXT_INVALID", f"Regenerate {spec.key} for this image before editing pixels.")
            # Stale optional measurements are absent.
            current = replace(state, context=tuple(e for e in state.context if e.compatible(state)))
            for spec in desc.requires:
                entry = current.entry(spec.key)
                if entry is None or entry.spec != spec:
                    raise RetouchingError("CONTEXT_REQUIRED", f"{runtime.stage.id} requires current {spec.key} version {spec.version}; run its analysis producer first")
            self.operation.runtime = runtime
            result = self.operation.process(current)
            self._validate_result(current, result)
            if desc.changes_pixels:
                result = _blend_result(current, result, runtime.stage.parameter(BLEND.name), desc.provides)
            provided = {spec.key for spec in desc.provides}
            kept = tuple(e for e in current.context if e.spec.key not in provided and retained(e.spec, desc) and e.compatible(result))
            produced = tuple(e for e in result.context if e.spec.key in provided)
            result = replace(result, context=kept + produced)
            runtime.emit("STAGE_COMPLETED", "Image-stage unit completed", duration_ms=(perf_counter() - started) * 1000,
                         pixel_revision=result.pixel_revision)
            return result
        except ResizeApprovalRequired:
            raise
        except Exception:
            runtime.emit("STAGE_PROCESS_FAILED", "Image-stage unit failed; inspect technical details before retrying", level="ERROR", exc_info=True,
                         duration_ms=(perf_counter() - started) * 1000)
            raise
        finally:
            if self.operation is not None:
                self.operation.runtime = self.runtime

    def _validate_result(self, before, result):
        desc, stage = self.descriptor, self.runtime.stage
        if result.metadata is not before.metadata or result.representation != before.representation:
            raise RetouchingError("STAGE_RESULT_INVALID", "Stage must preserve immutable source metadata and working representation")
        changed = result.pixels is not before.pixels
        if (changed and (not desc.changes_pixels or result.pixel_revision != before.pixel_revision + 1)
                or not changed and result.pixel_revision != before.pixel_revision
                or result.pixels.flags.writeable):
            raise RetouchingError("STAGE_RESULT_INVALID", "Undeclared pixel edit, invalid revision, or writable output")
        if result.pixels.dtype != before.pixels.dtype or result.pixels.ndim != 3 or result.pixels.shape[2] != 3:
            raise RetouchingError("STAGE_RESULT_INVALID", "Stage changed sample precision or channel layout")
        if changed:
            import numpy as np
            for start in range(0, result.height, 64):
                strip = result.pixels[start:start + 64]
                if not np.isfinite(strip).all() or strip.min() < 0 or strip.max() > 1:
                    raise RetouchingError("STAGE_RESULT_INVALID", "Stage returned nonfinite or out-of-range samples")
        geometry_changed = (result.width, result.height) != (before.width, before.height) or result.geometry_revision != before.geometry_revision
        if geometry_changed and (not desc.changes_geometry or result.geometry_revision != before.geometry_revision + 1):
            raise RetouchingError("STAGE_RESULT_INVALID", "Undeclared geometry change or invalid geometry revision")
        keys = [e.spec.key for e in result.context]
        if len(keys) != len(set(keys)):
            raise RetouchingError("STAGE_RESULT_INVALID", "Stage returned duplicate context keys")
        known = {e.spec.key for e in before.context} | {s.key for s in desc.provides}
        if set(keys) - known:
            raise RetouchingError("STAGE_RESULT_INVALID", "Stage returned undeclared context")
        provided = {s.key for s in desc.provides}
        for entry in result.context:
            if entry.spec.key not in provided and entry != before.entry(entry.spec.key):
                raise RetouchingError("STAGE_RESULT_INVALID", f"Stage modified undeclared context {entry.spec.key}")
        for spec in desc.provides:
            entry = result.entry(spec.key)
            if (entry is None or entry.spec != spec or not entry.compatible(result)
                    or entry.producer_id != stage.id or entry.producer_operation != stage.operation
                    or entry.producer_version != stage.operation_version or entry.model_ref != stage.model_ref
                    or entry.pixel_revision != result.pixel_revision or entry.producer_settings_hash != stage.identity):
                raise RetouchingError("STAGE_RESULT_INVALID", f"Missing/invalid declared output context {spec.key}")

    def _release(self, *, primary_failure=False):
        if self.operation is not None:
            try:
                self.operation.release()
                self.runtime.emit("STAGE_RELEASED", "Stage resources released")
            except Exception:
                self.runtime.emit("STAGE_RELEASE_FAILED", "Stage resource release failed; inspect cause before continuing", level="ERROR", exc_info=True)
                if not primary_failure:
                    raise
            finally:
                self.operation = None

    def __exit__(self, exc_type, exc, traceback):
        self._release(primary_failure=exc_type is not None)
        return False
