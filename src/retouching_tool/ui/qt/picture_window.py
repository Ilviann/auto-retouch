"""A separate picture window for an original or a before/after comparison."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QLabel, QSizePolicy, QVBoxLayout

from ...pipeline.image_state import ImageStatistics
from .photo_grid import pixmap


class PictureWindow(QDialog):
    def __init__(self, parent, preview):
        super().__init__(parent)
        self.setWindowTitle(preview.title)
        self.resize(850, 650)
        self.original = pixmap(preview.original)
        self.result = pixmap(preview.result) if preview.result is not None else None
        self.picture = QLabel(objectName="previewPicture", alignment=Qt.AlignmentFlag.AlignCenter)
        self.picture.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.picture.setMinimumSize(250, 200)
        self.original_toggle = QCheckBox("Show original", objectName="showPreviewOriginal")
        self.original_toggle.setVisible(self.result is not None)
        self.original_toggle.toggled.connect(self._refresh_picture)
        layout = QVBoxLayout(self)
        layout.addWidget(self.original_toggle)
        layout.addWidget(self.picture, 1)
        for entry in preview.context:
            if isinstance(value := entry.value, ImageStatistics):
                label = QLabel(f"Mean linear luminance {value.mean_luminance:.3f} · "
                               f"Range {value.minimum:.3f}–{value.maximum:.3f} · "
                               f"Clipped {value.clipped_low:,} dark / {value.clipped_high:,} bright",
                               objectName="previewStatistics", wordWrap=True)
                layout.addWidget(label)

    def _refresh_picture(self):
        pixels = self.original if self.result is None or self.original_toggle.isChecked() else self.result
        self.picture.setPixmap(pixels.scaled(self.picture.size().boundedTo(pixels.size()),
                                            Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_picture()
