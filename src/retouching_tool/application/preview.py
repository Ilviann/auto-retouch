"""Local, toolkit-independent display reads and processing previews."""

from dataclasses import dataclass, replace

from ..errors import RetouchingError
from ..imaging.jpeg import JpegRuntime, decode_jpeg, encode_rgb_samples, read_srgb_jpeg
from ..jobs.execution import ImageDiagnostics
from ..pipeline.executor import StageRuntime, StageSession
from ..pipeline.image_state import ContextEntry
from ..pipeline.validation import validate_pipeline
from ..storage.batch_paths import current_file_stamp
from .batch import IndexedImage, SourceIndex

THUMBNAIL_SIZE = 160
PICTURE_SIZE = 1600


@dataclass(frozen=True)
class DisplayImage:
    width: int
    height: int
    rgb: bytes


@dataclass(frozen=True)
class PicturePreview:
    request_id: str
    title: str
    original: DisplayImage
    result: DisplayImage | None = None
    context: tuple[ContextEntry, ...] = ()


def _display(rgb, limit):
    from PIL.Image import Resampling

    rgb.thumbnail((limit, limit), Resampling.LANCZOS)
    return DisplayImage(rgb.width, rgb.height, rgb.tobytes())


def _check_source(image):
    if image.header is None:
        raise RetouchingError("INPUT_HEADER_INVALID", image.issue or "Re-index a readable JPEG.")
    try:
        if current_file_stamp(image.path) == image.stamp:
            return
    except OSError as exc:
        raise RetouchingError("SOURCE_CHANGED", "Cannot reopen the indexed JPEG. Scan the sources again.") from exc
    raise RetouchingError("SOURCE_CHANGED", "The indexed JPEG changed. Scan the sources again.")


class PreviewService:
    def __init__(self, batch, editor, diagnostics):
        self.batch, self.editor, self.diagnostics = batch, editor, diagnostics

    def _source_display(self, image, runtime, limit):
        _check_source(image)
        with read_srgb_jpeg(image.path, source_id=image.image_id, runtime=runtime, draft_size=limit) as (
                rgb, _, _, _):
            display = _display(rgb, limit)
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

    def picture(self, index: SourceIndex, image_id, request_id, *, destination=None, stage_id=None) -> PicturePreview:
        """A destination requests processing. A stage ID requests isolation on the original."""
        from PIL import Image

        log = ImageDiagnostics(self.diagnostics)
        runtime = JpegRuntime(log, image_id, preview_request_id=request_id)
        image = next((item for item in index.images if item.image_id == image_id), None)
        try:
            if image is None:
                raise RetouchingError("PREVIEW_SELECTION_INVALID", "Select a photo from the current index.")
            if destination is None:
                if stage_id is not None:
                    raise RetouchingError("DESTINATION_INVALID", "Choose a destination before previewing an operation.")
                original = self._source_display(image, runtime, PICTURE_SIZE)
                return PicturePreview(request_id, f"Original · {image.path.name}", original)
            try:
                plan = self.batch.preflight(index, destination, mode="sequential", selected_ids=(image_id,),
                                            preview_request_id=request_id)
            except RetouchingError:
                log.traced_error = True
                raise
            if not plan.ready:
                raise RetouchingError("PREVIEW_SPACE_INSUFFICIENT", "Free disk space or choose a different destination.")
            title, stages = self._stages(stage_id)
            state = decode_jpeg(image.path, source_id=image_id, runtime=runtime)
            _check_source(image)
            original = _display(Image.fromarray(encode_rgb_samples(state)), PICTURE_SIZE)
            initial_revision = state.pixel_revision
            for stage in stages:
                stage_runtime = StageRuntime(log, self.editor.backend, self.editor.checkpoints, None,
                                             stage, image_id, preview_request_id=request_id)
                with StageSession(self.editor.registry, self.editor.catalog, stage_runtime) as session:
                    state = session.process(state, image_id=image_id)
            result = (original if state.pixel_revision == initial_revision else
                      _display(Image.fromarray(encode_rgb_samples(state)), PICTURE_SIZE))
            return PicturePreview(request_id, f"{title} · {image.path.name}", original, result, state.context)
        except Exception as exc:
            code = exc.code if isinstance(exc, RetouchingError) else "PREVIEW_FAILED"
            message = str(exc) if isinstance(exc, RetouchingError) else "Cannot produce the preview. Check local diagnostics."
            runtime.emit(code, message, level="ERROR", exc_info=not log.traced_error, index_id=index.index_id,
                         source_path=image.path if image else None, stage_instance_id=log.failed_stage or stage_id)
            if isinstance(exc, RetouchingError):
                raise
            raise RetouchingError(code, message) from exc

    def _stages(self, stage_id):
        editor = self.editor
        if stage_id is None:
            reviewed = editor.status.definition
            pipeline = editor.preflight()
            if pipeline != reviewed:
                raise RetouchingError("PREVIEW_PIPELINE_CHANGED",
                                      "Operation availability changed. Review the pipeline cards and preview again.")
            return pipeline.name, tuple(stage for stage in pipeline.stages if stage.enabled)

        status = editor.recheck()
        selected = next((item for item in status.stages if item.stage.id == stage_id), None)
        if selected is None:
            raise RetouchingError("STAGE_UNKNOWN", "Select an operation in the current pipeline.")
        if selected.issues:
            raise RetouchingError("STAGE_UNAVAILABLE", "Resolve the model or backend error on the operation card.")
        standalone = replace(status.definition, stages=(replace(selected.stage, enabled=True),))
        checked = validate_pipeline(standalone, editor.registry, editor.catalog, editor.checkpoints, editor.backend)
        if checked.stages[0].issues:
            raise RetouchingError("STAGE_UNAVAILABLE", "Resolve the model or backend error on the operation card.")
        if not checked.executable:
            dependency = checked.stages[0].dependency_errors[0]
            raise RetouchingError("PREVIEW_CONTEXT_REQUIRED", f"{dependency}. Use Preview pipeline with the required producer.")
        return selected.title, standalone.stages
