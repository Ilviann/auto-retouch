"""A separate picture window for an original or a before/after comparison."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QLabel, QSizePolicy, QVBoxLayout

from ...pipeline.image_state import ImageStatistics
from .photo_grid import pixmap


class FittedPicture(QLabel):
    def __init__(self, name):
        super().__init__(objectName=name, alignment=Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(230, 180)
        self._pixels = None

    def show_pixels(self, pixels):
        self._pixels = pixels
        if pixels is None:
            self.clear()
        else:
            self._fit()

    def _fit(self):
        if self._pixels is not None:
            self.setPixmap(self._pixels.scaled(self.size().boundedTo(self._pixels.size()),
                                                Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


def statistics_text(context):
    return "\n".join(f"Mean linear luminance {value.mean_luminance:.3f} · "
                     f"Range {value.minimum:.3f}–{value.maximum:.3f} · "
                     f"Clipped {value.clipped_low:,} dark / {value.clipped_high:,} bright"
                     for entry in context if isinstance(value := entry.value, ImageStatistics))


class PictureWindow(QDialog):
    def __init__(self, parent, preview):
        super().__init__(parent)
        self.setWindowTitle(preview.title)
        self.resize(850, 650)
        self.original = pixmap(preview.original)
        self.result = pixmap(preview.result) if preview.result is not None else None
        self.picture = FittedPicture("previewPicture")
        self.original_toggle = QCheckBox("Show original", objectName="showPreviewOriginal")
        self.original_toggle.setVisible(self.result is not None)
        self.original_toggle.toggled.connect(self._refresh_picture)
        layout = QVBoxLayout(self)
        layout.addWidget(self.original_toggle)
        layout.addWidget(self.picture, 1)
        if text := statistics_text(preview.context):
            layout.addWidget(QLabel(text, objectName="previewStatistics", wordWrap=True))
        self._refresh_picture()
        self.finished.connect(self._discard_images)

    def _discard_images(self):
        self.original = self.result = None
        self.picture.show_pixels(None)

    def _refresh_picture(self):
        pixels = self.original if self.result is None or self.original_toggle.isChecked() else self.result
        self.picture.show_pixels(pixels)
