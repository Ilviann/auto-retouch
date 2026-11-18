"""Visible JPEG thumbnails, with one background decode at a time."""

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtGui import QColorSpace, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QListWidget, QListWidgetItem, QStyle

from ...errors import RetouchingError


SRGB = QColorSpace(QColorSpace.NamedColorSpace.SRgb)


def pixmap(display):
    image = QImage(display.rgb, display.width, display.height, display.width * 3, QImage.Format.Format_RGB888)
    image.setColorSpace(SRGB)
    return QPixmap.fromImage(image.copy())


class PhotoGrid(QListWidget):
    def __init__(self, preview_service):
        super().__init__(objectName="batchImages")
        self.preview_service = preview_service
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setWrapping(True)
        self.setWordWrap(False)
        self.setUniformItemSizes(True)
        self.setGridSize(QSize(150, 130))
        self.setIconSize(QSize(108, 88))
        self.setMinimumHeight(150)
        self.setAccessibleName("Source photos. Select one photo to preview. Check photos to include them in the batch.")
        self._placeholder = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self._warning = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        self._index = None
        self._loaded = {}
        self._errors = set()
        self._future = None
        self._closing = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-thumbnail")
        self._timer = QTimer(self, interval=45, singleShot=True)
        self._timer.timeout.connect(self._load_visible)
        self.verticalScrollBar().valueChanged.connect(self._schedule)
        self.model().layoutChanged.connect(self._schedule)

    def set_index(self, index):
        with QSignalBlocker(self):
            self.clear()
            self._index = index
            self._loaded.clear()
            self._errors.clear()
            if index is not None:
                for image in index.images:
                    item = QListWidgetItem(self._warning if image.issue else self._placeholder, image.path.name, self)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
                    item.setData(Qt.ItemDataRole.UserRole, image.image_id)
                    item.setCheckState(Qt.CheckState.Checked)
                    details = image.issue or f"{image.header.width} × {image.header.height}"
                    item.setToolTip(f"{image.path}\n{details}")
            if self.count():
                self.setCurrentRow(0)
        self._schedule()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule()

    def _schedule(self):
        if not self._closing and not self._timer.isActive():
            self._timer.start()

    def _thumbnail_icon(self, display):
        size = self.iconSize()
        # Leave a gutter for the native icon-view check indicator.
        photo = pixmap(display).scaled(size - QSize(16, 8), Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
        tile = QPixmap(size)
        tile.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tile)
        painter.drawPixmap((size.width() - photo.width()) // 2, (size.height() - photo.height()) // 2, photo)
        painter.end()
        return QIcon(tile)

    def _load_visible(self):
        if self._future is not None:
            index, row, future = self._future
            if not future.done():
                self._schedule()
                return
            self._future = None
            item = self.item(row) if index is self._index else None
            try:
                display = future.result()
            except RetouchingError as exc:
                if item is not None:
                    self._errors.add(row)
                    with QSignalBlocker(self):
                        item.setIcon(self._warning)
                        item.setToolTip(f"{item.toolTip()}\n{exc}")
            else:
                if item is not None:
                    with QSignalBlocker(self):
                        item.setIcon(self._thumbnail_icon(display))
                    self._loaded[row] = item
        if self._index is None or not self.isVisible():
            return
        viewport = self.viewport().rect()
        visible = [row for row in range(self.count()) if viewport.intersects(self.visualItemRect(self.item(row)))]
        with QSignalBlocker(self):
            for row in self._loaded.keys() - set(visible):
                self._loaded.pop(row).setIcon(self._placeholder)
        for row in visible:
            image = self._index.images[row]
            if row not in self._loaded and row not in self._errors and image.header is not None:
                self._future = (self._index, row, self._executor.submit(self.preview_service.thumbnail, image))
                self._schedule()
                break

    def shutdown(self):
        if not self._closing:
            self._closing = True
            self._timer.stop()
            self._executor.shutdown(wait=False, cancel_futures=True)
        return self._future is None or self._future[2].done()
