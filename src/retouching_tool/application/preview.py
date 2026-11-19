"""Local, toolkit-independent display reads and processing previews."""

from contextlib import nullcontext
from dataclasses import dataclass, replace
from functools import partial

from ..errors import ResizeApprovalRequired, RetouchingError
from ..imaging.jpeg import JpegRuntime, decode_jpeg, encode_rgb_samples, read_srgb_jpeg
from ..jobs.execution import ImageDiagnostics
from ..jobs.journal import runner_lock
from ..pipeline.definition import parse_pipeline
from ..pipeline.executor import StageRuntime, StageSession
from ..pipeline.image_state import EXPOSED_SKIN, FACE_REGIONS, PERSON_FOREGROUND, ContextEntry
from ..imaging.skin_matte import matte_strips
from ..operations.white_balance import sample_neutral
from ..pipeline.validation import validate_pipeline
from ..storage.batch_paths import current_file_stamp
from .batch import IndexedImage, SourceIndex

THUMBNAIL_SIZE = 160
OVERVIEW_SIZE = 1600


@dataclass(frozen=True)
class DisplayImage:
    width: int
    height: int
    rgb: bytes
    overview: DisplayImage | None = None


@dataclass(frozen=True)
class PicturePreview:
    request_id: str
    title: str
    original: DisplayImage
    result: DisplayImage | None = None
    context: tuple[ContextEntry, ...] = ()
    result_caption: str | None = None
    stage_warning: str | None = None


class _PreviewDiagnostics(ImageDiagnostics):
    def __init__(self, diagnostics, stage_id):
        super().__init__(diagnostics)
        self.stage_id = stage_id
        self.stage_warning = None

    def emit(self, code, message, **details):
        if self.stage_id and details.get("stage_instance_id") == self.stage_id and details.get("level") == "WARNING":
            self.stage_warning = message
        super().emit(code, message, **details)


class StageComparison:
    """Render one comparison serially and retain its most recent stage input."""

    def __init__(self, render):
        self._render = render
        self._baseline = None

    def render(self, request_id, parameters, *, resize_approvals=()):
        return self._render(request_id=request_id, parameters=parameters, _comparison=self,
                            resize_approvals=resize_approvals)

    def clear(self):
        self._baseline = None

    def neutral_settings(self, x, y):
        if self._baseline is None:
            raise RetouchingError("PREVIEW_INPUT_REQUIRED", "Render the stage preview before picking a neutral.")
        return sample_neutral(self._baseline[1], x, y)


def _display(rgb, limit):
    from PIL.Image import Resampling

    rgb.thumbnail((limit, limit), Resampling.LANCZOS)
    return DisplayImage(rgb.width, rgb.height, rgb.tobytes())


def _full_display(rgb):
    full = DisplayImage(rgb.width, rgb.height, rgb.tobytes())
    if max(rgb.size) <= OVERVIEW_SIZE:
        return full
    return replace(full, overview=_display(rgb, OVERVIEW_SIZE))


def _foreground_overlay(display, foreground):
    from PIL import Image

    size = (display.width, display.height)
    opacity = foreground.labels.replace(b"\x01", b"\x70")
    with (Image.frombytes("RGB", size, display.rgb) as image,
          Image.frombytes("L", (foreground.width, foreground.height), opacity) as labels,
          labels.resize(size, Image.Resampling.NEAREST) as matte):
        image.paste((0, 224, 160), mask=matte)
        return _full_display(image)


def _check_source(image):
    if image.header is None:
        raise RetouchingError("INPUT_HEADER_INVALID", image.issue or "Re-index a readable JPEG.")
    try:
        if current_file_stamp(image.path) == image.stamp:
            return
    except OSError as exc:
        raise RetouchingError("SOURCE_CHANGED", "Cannot reopen the indexed JPEG. Scan the sources again.") from exc
    raise RetouchingError("SOURCE_CHANGED", "The indexed JPEG changed. Scan the sources again.")


def _skin_overlay(display, mask):
    from PIL import Image

    with Image.frombytes("RGB", (display.width, display.height), display.rgb) as image:
        for label, color in ((1, (0, 224, 160)), (2, (232, 100, 255))):
            for start, stop, strip in matte_strips(mask, image.size, (label,)):
                with strip.point(lambda x: round(x * .48)) as opacity:
                    image.paste(color, (0, start, image.width, stop), opacity)
        return _full_display(image)


def _face_overlay(display, regions):
    from PIL import Image, ImageDraw

    if not regions:
        return display
    with Image.frombytes("RGB", (display.width, display.height), display.rgb) as image:
        draw = ImageDraw.Draw(image)
        width = max(1, max(image.size) // 600)
        for region in regions:
            draw.rectangle((region.left, region.top, region.right - 1, region.bottom - 1),
                           outline=(0, 224, 160), width=width)
        return _full_display(image)


class PreviewService:
    def __init__(self, root, editor, diagnostics):
        self.root, self.editor, self.diagnostics = root, editor, diagnostics

    def _source_display(self, image, runtime, limit=None):
        _check_source(image)
        with read_srgb_jpeg(image.path, source_id=image.image_id, runtime=runtime, draft_size=limit) as (
                rgb, _, _, _):
            display = _full_display(rgb) if limit is None else _display(rgb, limit)
        _check_source(image)
        return display

    def thumbnail(self, image: IndexedImage) -> DisplayImage:
        log = ImageDiagnostics(self.diagnostics)
        runtime = JpegRuntime(log, image.image_id)
        try:
            return self._source_display(image, runtime, THUMBNAIL_SIZE)
        except Exception as exc:
            runtime.emit("THUMBNAIL_FAILED", "Cannot display the source thumbnail. Inspect the JPEG or scan again.",
                         level="WARNING", exc_info=not log.traced_error, source_path=image.path)
            raise RetouchingError("THUMBNAIL_FAILED", "Cannot display this JPEG.") from exc

    def stage_comparison(self, index, image_id, stage_id, *, through_stage):
        return StageComparison(partial(self.picture, index, image_id, stage_id=stage_id,
                                       through_stage=through_stage))

    def picture(self, index: SourceIndex, image_id, request_id, *, process=False, stage_id=None,
                through_stage=False, parameters=None, _comparison=None, resize_approvals=()) -> PicturePreview:
        """Display the original, run the enabled pipeline, or compare a selected stage in memory."""
        log = _PreviewDiagnostics(self.diagnostics, stage_id)
        runtime = JpegRuntime(log, image_id, preview_request_id=request_id)
        image = next((item for item in index.images if item.image_id == image_id), None)
        try:
            if image is None:
                raise RetouchingError("PREVIEW_SELECTION_INVALID", "Select a photo from the current index.")
            if stage_id is None and (through_stage or parameters is not None):
                raise RetouchingError("PREVIEW_STAGE_REQUIRED", "Select a stage to preview with these settings.")
            if not process and stage_id is None:
                original = self._source_display(image, runtime)
                return PicturePreview(request_id, f"Original · {image.path.name}", original)
            title, stages = self._stages(stage_id, through_stage, parameters)
            lock = runner_lock(self.root) if any(stage.model_ref is not None for stage in stages) else nullcontext()
            with lock:
                return self._render(image, stages, runtime, title, stage_id, through_stage, _comparison, resize_approvals)
        except ResizeApprovalRequired:
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, RetouchingError) else "PREVIEW_FAILED"
            message = str(exc) if isinstance(exc, RetouchingError) else "Cannot produce the preview. Check local diagnostics."
            runtime.emit(code, message, level="ERROR", exc_info=not log.traced_error, index_id=index.index_id,
                         source_path=image.path if image else None, stage_instance_id=log.failed_stage or stage_id)
            if isinstance(exc, RetouchingError):
                raise
            raise RetouchingError(code, message) from exc

    def _render(self, image, stages, runtime, title, stage_id, through_stage, comparison, resize_approvals):
        from PIL import Image

        prefix, remaining = (stages[:-1], stages[-1:]) if through_stage else ((), stages)
        baseline_key = (self.editor.backend, self.editor.pytorch_backend, self.editor.checkpoints, prefix,
                        tuple(resize_approvals))
        _check_source(image)
        baseline = comparison._baseline if comparison is not None else None
        if baseline is not None and baseline[0] == baseline_key:
            _, state, original = baseline
        else:
            state = decode_jpeg(image.path, source_id=image.image_id, runtime=runtime)
            _check_source(image)
            for stage in prefix:
                state = self._process_stage(state, stage, runtime, resize_approvals)
            original = _full_display(Image.fromarray(encode_rgb_samples(state)))
            if comparison is not None:
                comparison._baseline = (baseline_key, state, original)
        initial_revision = state.pixel_revision
        for stage in remaining:
            state = self._process_stage(state, stage, runtime, resize_approvals)
        _check_source(image)
        result = (original if state.pixel_revision == initial_revision else
                  _full_display(Image.fromarray(encode_rgb_samples(state))))
        caption = None
        skin = state.entry(EXPOSED_SKIN.key)
        if skin is not None and skin.producer_id == stage_id:
            result = _skin_overlay(result, skin.value)
            caption = "Exposed skin · body in green · face in magenta"
        foreground = state.entry(PERSON_FOREGROUND.key)
        if foreground is not None and foreground.producer_id == stage_id:
            result = _foreground_overlay(result, foreground.value)
            caption = "Detected person foreground"
        faces = state.entry(FACE_REGIONS.key)
        if faces is not None and faces.producer_id == stage_id:
            result = _face_overlay(result, faces.value.regions)
            caption = f"Detected faces · {len(faces.value.regions)}"
        return PicturePreview(runtime.preview_request_id, f"{title} · {image.path.name}", original, result,
                              state.context, caption, runtime.diagnostics.stage_warning)

    def _process_stage(self, state, stage, runtime, resize_approvals):
        stage_runtime = StageRuntime(runtime.diagnostics, self.editor.stage_backend(stage), self.editor.checkpoints,
                                     None, stage, runtime.image_id, preview_request_id=runtime.preview_request_id,
                                     resize_approved=stage.id in resize_approvals)
        with StageSession(self.editor.registry, self.editor.catalog, stage_runtime) as session:
            return session.process(state, image_id=runtime.image_id)

    def _stages(self, stage_id, through_stage, parameters):
        editor = self.editor
        if stage_id is None:
            reviewed = editor.status.definition
            pipeline = editor.preflight(write_unchanged=False)
            if pipeline != reviewed:
                raise RetouchingError("PREVIEW_PIPELINE_CHANGED",
                                      "Operation availability changed. Review the pipeline cards and preview again.")
            return pipeline.name, tuple(stage for stage in editor.resolve_scaling(pipeline).stages if stage.enabled)

        reviewed = editor.status.definition
        status = editor.recheck(write_unchanged=False)
        position = next((i for i, item in enumerate(status.stages) if item.stage.id == stage_id), None)
        if position is None:
            raise RetouchingError("STAGE_UNKNOWN", "Select an operation in the current pipeline.")
        selected = status.stages[position]
        if selected.issues:
            raise RetouchingError("STAGE_UNAVAILABLE", "Resolve the model or backend error on the operation card.")
        prefix = status.definition.stages[:position] if through_stage else ()
        if through_stage and prefix != reviewed.stages[:position]:
            raise RetouchingError("PREVIEW_PIPELINE_CHANGED",
                                  "Upstream operation availability changed. Review the pipeline cards and preview again.")
        preview = replace(status.definition, stages=tuple(s for s in prefix if s.enabled)
                          + (replace(selected.stage, enabled=True),))
        if parameters is not None:
            data = preview.to_json()
            data["stages"][-1]["parameters"] = {**dict(selected.stage.parameters), **parameters}
            preview = parse_pipeline(data, editor.registry)
        checked = validate_pipeline(preview, editor.registry, editor.catalog, editor.checkpoints, editor.backend, editor.pytorch_backend)
        unavailable = next((item for item in checked.stages if item.issues), None)
        if unavailable:
            raise RetouchingError("STAGE_UNAVAILABLE", f"Resolve the model or backend error for {unavailable.stage.id}.")
        if not checked.executable:
            dependency = next(item.dependency_errors[0] for item in checked.stages if item.dependency_errors)
            hint = "Enable or reorder the required producer." if through_stage else "Use Preview in pipeline with the required producer."
            raise RetouchingError("PREVIEW_CONTEXT_REQUIRED", f"{dependency}. {hint}")
        return selected.title, editor.resolve_scaling(checked.definition).stages
