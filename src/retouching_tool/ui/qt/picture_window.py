"""A separate picture window for an original or a before/after comparison."""

from math import floor, isclose

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ...pipeline.image_state import ImageStatistics
from .photo_grid import display_image, pixmap


class PicturePane(QLabel):
    pixel_picked = Signal(int, int)

    def __init__(self, name, comparison):
        super().__init__(objectName=name)
        self.comparison = comparison
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(230, 180)
        self.setToolTip("Scroll to zoom. Drag to pan.")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._drag_position = None
        self.picking = False
        self._painted_target = QRectF()
        self._native = QImage()

    def show_pixels(self, display):
        self._native = QImage()
        if display is None:
            self._drag_position = None
            self._painted_target = QRectF()
            self.clear()
        else:
            if display.overview is not None:
                self._native = display_image(display)
            self.setPixmap(pixmap(display.overview or display))

    def image_size(self):
        return self.pixmap().size() if self._native.isNull() else self._native.size()

    def paintEvent(self, event):
        pixels = self.pixmap()
        if pixels.isNull():
            return
        scale = self.comparison.scale()
        center = self.comparison.center
        size = self.image_size()
        width, height = size.width() * scale, size.height() * scale
        with QPainter(self) as painter:
            dpr = self.devicePixelRatioF()
            one_to_one = isclose(scale * dpr, 1.0)
            middle = QPointF(self.width() / 2, self.height() / 2)
            corner = middle - QPointF(center.x() * width, center.y() * height)
            if one_to_one:
                # Align the inspected pixel to the physical grid, including a fractional window offset.
                transform = painter.deviceTransform()
                midpoint = transform.map(middle)
                corner = QPointF((floor(midpoint.x()) - floor(center.x() * size.width()) - transform.dx()) / dpr,
                                 (floor(midpoint.y()) - floor(center.y() * size.height()) - transform.dy()) / dpr)
            target = QRectF(corner.x(), corner.y(), width, height)
            self._painted_target = target
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not one_to_one)
            if self._native.isNull() or (width * dpr <= pixels.width() and height * dpr <= pixels.height()):
                painter.drawPixmap(target, pixels, QRectF(pixels.rect()))
            else:
                visible = target.intersected(QRectF(self.rect()))
                source = QRectF((visible.x() - corner.x()) / scale, (visible.y() - corner.y()) / scale,
                                visible.width() / scale, visible.height() / scale)
                painter.drawImage(visible, self._native, source)

    def _relative_offset(self, offset):
        size = self.image_size()
        return QPointF(offset.x() / size.width(), offset.y() / size.height())

    def wheelEvent(self, event):
        steps = (event.angleDelta().y() or event.pixelDelta().y()) / 120
        if steps and not self.pixmap().isNull():
            offset = event.position() - QPointF(self.width() / 2, self.height() / 2)
            self.comparison.zoom(steps, self._relative_offset(offset))
            event.accept()
        else:
            event.ignore()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.pixmap().isNull():
            if self.picking:
                point, target, size = event.position(), self._painted_target, self.image_size()
                if not target.isEmpty():
                    x = floor((point.x() - target.x()) * size.width() / target.width())
                    y = floor((point.y() - target.y()) * size.height() / target.height())
                    if 0 <= x < size.width() and 0 <= y < size.height():
                        self.pixel_picked.emit(x, y)
                event.accept()
                return
            self._drag_position = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_position is not None:
            delta = self._drag_position - event.position()
            self._drag_position = event.position()
            self.comparison.pan(self._relative_offset(delta))
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_position is not None:
            self._drag_position = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


def statistics_text(context):
    return "\n".join(f"Mean linear luminance {value.mean_luminance:.3f} · "
                     f"Range {value.minimum:.3f}–{value.maximum:.3f} · "
                     f"Clipped {value.clipped_low:,} dark / {value.clipped_high:,} bright"
                     for entry in context if isinstance(value := entry.value, ImageStatistics))


class PictureComparison(QWidget):
    SIDE_BY_SIDE, SOURCE, RESULT = range(3)

    def __init__(self, source_caption, result_caption, *, source_only=False):
        super().__init__()
        self._magnification = None  # Physical display pixels per image pixel, or Fit.
        self.center = QPointF(.5, .5)
        self._result_caption = result_caption
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.controls = QWidget()
        toolbar = QHBoxLayout(self.controls)
        toolbar.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox(accessibleName="Comparison view")
        self.mode.addItems(["Side by side", source_caption, "Result"])
        toolbar.addWidget(QLabel("View"))
        toolbar.addWidget(self.mode)
        toolbar.addWidget(QLabel("Space to switch"))
        actions = QHBoxLayout()
        actions.addWidget(self.controls)
        actions.addStretch()
        self.zoom_controls = QWidget()
        zoom_row = QHBoxLayout(self.zoom_controls)
        zoom_row.setContentsMargins(0, 0, 0, 0)
        for label, hint, action in (("−", "Zoom out", lambda: self.zoom(-1)),
                                    ("+", "Zoom in", lambda: self.zoom(1)),
                                    ("100%", "Show one image pixel per screen pixel", self.actual_size),
                                    ("Fit", "Fit and center the image", self.fit)):
            button = QToolButton(text=label, accessibleName=hint, toolTip=hint)
            button.clicked.connect(action)
            zoom_row.addWidget(button)
        self.zoom_controls.setEnabled(False)
        actions.addWidget(self.zoom_controls)
        layout.addLayout(actions)

        pictures = QHBoxLayout()
        self.before = PicturePane("previewBefore", self)
        self.after = PicturePane("previewAfter", self)
        source_label = QLabel(source_caption, alignment=Qt.AlignmentFlag.AlignCenter)
        self.result_label = QLabel(result_caption, alignment=Qt.AlignmentFlag.AlignCenter)
        self.source_pane = QWidget()
        self.result_pane = QWidget()
        for pane, caption, picture in ((self.source_pane, source_label, self.before),
                                       (self.result_pane, self.result_label, self.after)):
            column = QVBoxLayout(pane)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(caption)
            column.addWidget(picture, 1)
            pictures.addWidget(pane, 1)
        layout.addLayout(pictures, 1)
        self.shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self, autoRepeat=False)
        self.shortcut.activated.connect(self._switch_picture)
        self.mode.currentIndexChanged.connect(self._change_mode)
        self.controls.setVisible(not source_only)
        self.controls.setEnabled(False)
        self.shortcut.setEnabled(False)
        self._show_mode(self.SOURCE if source_only else self.SIDE_BY_SIDE)

    def _switch_picture(self):
        self.mode.setCurrentIndex(self.RESULT if self.mode.currentIndex() == self.SOURCE else self.SOURCE)

    def scale(self):
        if self._magnification is not None:
            return self._magnification / self.devicePixelRatioF()
        scale = 1 / self.devicePixelRatioF()
        for pane in (self.before, self.after):
            size = pane.image_size()
            if not pane.parentWidget().isHidden() and not size.isEmpty():
                scale = min(scale, pane.width() / size.width(), pane.height() / size.height())
        return scale

    def _move_center(self, delta):
        point = self.center + delta
        self.center = QPointF(max(0, min(1, point.x())), max(0, min(1, point.y())))
        self.before.update()
        self.after.update()

    def zoom(self, steps, offset=QPointF()):
        old = self.scale()
        physical = old * self.devicePixelRatioF()
        requested = physical * 1.25 ** max(-32, min(32, steps))
        self._magnification = max(min(.05, physical), min(16.0, requested))
        # Keep the image point beneath the cursor fixed as magnification changes.
        self._move_center(offset * (1 / old - 1 / self.scale()))

    def pan(self, delta):
        scale = self.scale()
        self._magnification = scale * self.devicePixelRatioF()
        self._move_center(delta / scale)

    def actual_size(self):
        self._magnification = 1.0
        self.before.update()
        self.after.update()

    def fit(self):
        self._magnification = None
        self.center = QPointF(.5, .5)
        self.before.update()
        self.after.update()

    def _change_mode(self, index):
        self._magnification = self.scale() * self.devicePixelRatioF()
        self._show_mode(index)

    def _show_mode(self, index):
        self.source_pane.setVisible(index != self.RESULT)
        self.result_pane.setVisible(index != self.SOURCE)

    def display(self, preview):
        self.before.show_pixels(preview.original)
        self.zoom_controls.setEnabled(True)
        if preview.result is not None:
            self.after.show_pixels(preview.result)
            self.result_label.setText(preview.result_caption or self._result_caption)
            self.controls.show()
            self.controls.setEnabled(True)
            self.shortcut.setEnabled(True)
            self._show_mode(self.mode.currentIndex())

    def clear(self):
        self.before.show_pixels(None)
        self.after.show_pixels(None)


class RenderProgressDialog(QDialog):
    def __init__(self, preview):
        super().__init__(preview)
        self.setWindowTitle(preview.windowTitle())
        self.setWindowModality(Qt.WindowModality.WindowModal)
        layout = QVBoxLayout(self)
        self.message = QLabel()
        layout.addWidget(self.message)
        progress = QProgressBar(accessibleName="Preview progress")
        progress.setRange(0, 0)
        progress.setMinimumWidth(260)
        layout.addWidget(progress)
        self.close_preview = QPushButton("Close preview", autoDefault=False)
        self.close_preview.clicked.connect(self.reject)
        layout.addWidget(self.close_preview, alignment=Qt.AlignmentFlag.AlignRight)

    def reject(self):
        self.parentWidget().reject()


class PictureWindow(QDialog):
    def __init__(self, parent, preview):
        super().__init__(parent)
        self.resize(850, 650)
        layout = QVBoxLayout(self)
        self.comparison = PictureComparison("Source", "Pipeline result", source_only=True)
        layout.addWidget(self.comparison, 1)
        self.statistics = QLabel(objectName="previewStatistics", wordWrap=True)
        layout.addWidget(self.statistics)
        actions = QHBoxLayout()
        self.feedback = QLabel(wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        actions.addWidget(self.feedback, 1)
        self.run_pipeline = QPushButton("Run pipeline", objectName="runPreviewPipeline", autoDefault=False)
        actions.addWidget(self.run_pipeline)
        layout.addLayout(actions)
        self.display(preview)
        self.render_dialog = RenderProgressDialog(self)
        self.render_dialog.setWindowTitle("Pipeline preview")
        self.render_dialog.message.setText("Running pipeline…")
        self.finished.connect(self.render_dialog.hide)
        self.finished.connect(self.comparison.clear)

    def display(self, preview):
        self.setWindowTitle(preview.title)
        self.comparison.display(preview)
        measurements = statistics_text(preview.context)
        self.statistics.setText(measurements)
        self.statistics.setVisible(bool(measurements))
