"""Histogram, tone ranges and numeric controls shared by both Qt Levels editors."""

from math import log2

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget


INPUT = ("input_black", "gamma", "input_white")
OUTPUT = ("output_black", "output_white")
CHANNEL_COLORS = {"RGB": "#dedede", "Red": "#ef9892", "Green": "#96cca1", "Blue": "#91b3e5"}


class LevelsRamp(QWidget):
    def __init__(self, controls, names, *, preview):
        super().__init__()
        self.controls = controls
        self.names = names
        self.bins = ()
        self.channel = "RGB"
        self.empty_label = "Waiting for preview" if preview else "No source histogram"
        self.plot_height = (116 if preview else 66) if names == INPUT else 0
        self.setFixedHeight(self.plot_height + 26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Input levels histogram and handles" if names == INPUT else "Output levels handles")
        self.setToolTip("Drag a triangle or enter its value below.")
        self.dragging = None
        self.offset = 0
        for name in names:
            controls[name].valueChanged.connect(lambda _: self.update())

    def position(self, name):
        value = self.controls[name].value()
        if name == "gamma":
            black, white = (self.controls[key].value() for key in ("input_black", "input_white"))
            value = black + (white - black) * .5 ** value
        return 8 + (self.width() - 16) * value / 255

    def paintEvent(self, event):
        painter = QPainter(self)
        width = self.width() - 16
        top = self.plot_height
        if top:
            rect = QRectF(8, 1, width, top - 2)
            painter.fillRect(rect, QColor("#262626"))
            painter.setPen(QColor("#616161"))
            for division in range(1, 5):
                x = rect.left() + width * division / 5
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            if self.bins:
                path = QPainterPath(QPointF(rect.left(), rect.bottom()))
                scale = rect.height() / max(1, max(self.bins))
                for i, count in enumerate(self.bins):
                    y = rect.bottom() - count * scale
                    path.lineTo(8 + i * width / 256, y)
                    path.lineTo(8 + (i + 1) * width / 256, y)
                path.lineTo(rect.right(), rect.bottom())
                painter.fillPath(path, QColor(CHANNEL_COLORS[self.channel]))
            else:
                painter.setPen(QColor("#dedede"))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.empty_label)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor("#828282"))
            painter.drawRect(rect)

        gradient = QLinearGradient(8, 0, 8 + width, 0)
        gradient.setColorAt(0, Qt.GlobalColor.black)
        gradient.setColorAt(1, Qt.GlobalColor.white)
        painter.fillRect(QRectF(8, top, width, 11), gradient)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for name in self.names:
            x = self.position(name)
            color = "#161616" if name.endswith("black") else "#fafafa" if name.endswith("white") else "#888888"
            painter.setBrush(QColor(color))
            painter.setPen(QPen(self.palette().text().color(), 1))
            painter.drawPolygon(QPolygonF([QPointF(x, top + 11), QPointF(x - 6, top + 22),
                                           QPointF(x + 6, top + 22)]))

    def _move(self, x):
        value = max(0., min(255., (x - self.offset - 8) * 255 / (self.width() - 16)))
        control = self.controls[self.dragging]
        if self.dragging == "gamma":
            black, white = (self.controls[key].value() for key in ("input_black", "input_white"))
            fraction = max(.5 ** control.maximum(), min(.5 ** control.minimum(), (value - black) / (white - black)))
            control.setValue(-log2(fraction))
        else:
            control.setValue(round(value))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or event.position().y() < self.plot_height:
            return super().mousePressEvent(event)
        x = event.position().x()
        names = self.names
        # Flat output ranges have coincident handles. Approach from either side.
        if names == OUTPUT and self.position(names[0]) == self.position(names[1]) and x > self.position(names[0]):
            names = names[::-1]
        self.dragging = min(names, key=lambda name: abs(self.position(name) - x))
        distance = x - self.position(self.dragging)
        self.offset = distance if abs(distance) <= 6 else 0
        if not self.offset:
            self._move(x)
        event.accept()

    def mouseMoveEvent(self, event):
        if self.dragging is not None:
            self._move(event.position().x())
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.dragging is not None:
            self._move(event.position().x())
            control = self.controls[self.dragging]
            self.dragging = None
            control.editingFinished.emit()
            event.accept()


class LevelsEditor(QWidget):
    pick_requested = Signal(str)

    def __init__(self, controls, *, preview):
        super().__init__(objectName="levelsEditor")
        self.controls = controls
        self.histogram = ()
        self.pickers = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        for title, names in (("Input Levels", INPUT), ("Output Levels", OUTPUT)):
            heading = QHBoxLayout()
            label = QLabel(title)
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            heading.addWidget(label)
            heading.addStretch()
            if names == INPUT:
                heading.addWidget(QLabel("Stage input" if preview else "Source thumbnail"))
            layout.addLayout(heading)
            ramp = LevelsRamp(controls, names, preview=preview)
            if names == INPUT:
                self.input_ramp = ramp
            else:
                self.output_ramp = ramp
            layout.addWidget(ramp)
            row = QHBoxLayout()
            for i, name in enumerate(names):
                if i:
                    row.addStretch()
                control = controls[name]
                control.setKeyboardTracking(False)
                control.setMinimumWidth(0)
                short = "Gamma" if name == "gamma" else name.split("_")[1].title()
                label = QLabel(short)
                label.setBuddy(control)
                control.setAccessibleName("Midtone gamma" if name == "gamma" else name.replace("_", " ").capitalize())
                row.addWidget(label)
                row.addWidget(control)
            layout.addLayout(row)
            if names == INPUT:
                if preview:
                    picks = QHBoxLayout()
                    for name, caption in (("input_black", "Pick black"), ("input_white", "Pick white")):
                        button = QToolButton(text=caption, checkable=True, enabled=False)
                        button.setToolTip(f"Click the input image to set {name.replace('_', ' ')}.")
                        button.clicked.connect(lambda checked, n=name: self._arm(n if checked else ""))
                        self.pickers[name] = button
                        if name == "input_white":
                            picks.addStretch()
                        picks.addWidget(button)
                    layout.addLayout(picks)
                layout.addSpacing(3)
        controls["channel"].currentIndexChanged.connect(self.refresh)
        for name in ("input_black", "input_white"):
            controls[name].valueChanged.connect(self._limit_inputs)
        self._limit_inputs()

    def set_limits(self, black, white):
        lower, upper = self.controls["input_black"], self.controls["input_white"]
        with QSignalBlocker(lower), QSignalBlocker(upper):
            lower.setMaximum(white - 1)
            upper.setMinimum(black + 1)

    def _limit_inputs(self):
        self.set_limits(self.controls["input_black"].value(), self.controls["input_white"].value())

    def _arm(self, name):
        for key, button in self.pickers.items():
            button.setChecked(key == name)
        self.pick_requested.emit(name)

    def clear_picker(self):
        self._arm("")

    def set_histogram(self, bins):
        self.histogram = bins
        self.refresh()

    def refresh(self):
        channel = self.controls["channel"].value()
        ramp = self.input_ramp
        ramp.channel = channel
        if channel == "RGB":
            ramp.bins = tuple(map(sum, zip(self.histogram[:256], self.histogram[256:512], self.histogram[512:])))
        else:
            start = ("Red", "Green", "Blue").index(channel) * 256
            ramp.bins = self.histogram[start:start + 256]
        ramp.update()
        self.output_ramp.update()
