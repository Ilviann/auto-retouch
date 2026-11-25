"""Scrollable catalog of operations available to add to a pipeline."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QGridLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ...pipeline.definition import MaskTargetParameter
from ...pipeline.image_state import EXPOSED_SKIN, FACE_REGIONS, FACIAL_FEATURES, PERSON_FOREGROUND


CONTEXT_NAMES = {
    EXPOSED_SKIN.key: "Exposed skin mask",
    FACE_REGIONS.key: "Face regions",
    FACIAL_FEATURES.key: "Facial feature protection mask",
    PERSON_FOREGROUND.key: "Person foreground mask",
}
RUNTIME_NAMES = {"onnxruntime": "ONNX Runtime", "pytorch": "PyTorch"}
DEVICE_NAMES = {"directml": "DirectML", "cpu": "CPU", "cuda": "CUDA"}


def _context_name(spec):
    return CONTEXT_NAMES.get(spec.key, spec.key)


def _details(descriptor, model_ref):
    representation = descriptor.representation
    pixels = "Pixels (linear sRGB, float32)" if representation == "linear-srgb-float32" else f"Pixels ({representation})"
    inputs = [pixels, *map(_context_name, descriptor.requires)]
    outputs = ([pixels] if descriptor.changes_pixels else []) + list(map(_context_name, descriptor.provides))
    yield "Inputs", ", ".join(inputs)
    if descriptor.optional:
        yield "Optional inputs", ", ".join(map(_context_name, descriptor.optional))
    for parameter in descriptor.parameters:
        if isinstance(parameter, MaskTargetParameter):
            yield "Conditional input", f"{_context_name(parameter.mask)} (required with “{parameter.label}”)"
    yield "Outputs", ", ".join(outputs) or "None"
    if model_ref is None:
        yield "Processing", "Conventional (no AI)"
    else:
        runtime = RUNTIME_NAMES.get(descriptor.inference_runtime, descriptor.inference_runtime)
        devices = ", ".join(DEVICE_NAMES.get(device, device) for device in descriptor.backends)
        yield "AI engine", f"{runtime}  ·  Supports {devices}"
        yield "Model", model_ref


class OperationChoice(QPushButton):
    def __init__(self, descriptor, model_ref):
        super().__init__()
        self.setAccessibleName(f"{descriptor.title} · v{descriptor.version}")
        self.setToolTip(descriptor.operation)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)
        grid.setColumnStretch(1, 1)
        title = self._label(descriptor.title)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        grid.addWidget(title, 0, 0, 1, 2)
        grid.addWidget(self._label(f"v{descriptor.version}"), 0, 2, Qt.AlignmentFlag.AlignTop)
        details = list(_details(descriptor, model_ref))
        for row, (caption, value) in enumerate(details, 1):
            grid.addWidget(self._label(caption), row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(self._label(value), row, 1, 1, 2)
        self.setAccessibleDescription("\n".join(f"{caption}: {value}" for caption, value in details))

    def _label(self, text):
        label = QLabel(text, textFormat=Qt.TextFormat.PlainText, wordWrap=True)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        return label

    def sizeHint(self):
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().minimumSize()


class OperationPicker(QDialog):
    chosen = Signal(str, int, object)

    def __init__(self, descriptors, parent):
        super().__init__(parent, windowTitle="Add operation")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        search = QLineEdit(placeholderText="Search by operation name…", accessibleName="Search by operation name")
        search.setClearButtonEnabled(True)
        layout.addWidget(search)
        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        cards = QVBoxLayout(content)
        cards.setContentsMargins(2, 2, 2, 2)
        cards.setSpacing(6)
        self.choices = []
        for descriptor in sorted(descriptors, key=lambda d: (d.title.casefold(), d.version, d.operation)):
            for model_ref in sorted(descriptor.model_refs, key=str.casefold) or (None,):
                card = OperationChoice(descriptor, model_ref)
                card.clicked.connect(lambda checked, d=descriptor, ref=model_ref: self._choose(d, ref))
                cards.addWidget(card)
                self.choices.append((descriptor.title.casefold(), card))
        self.no_matches = QLabel("No matching operations.", alignment=Qt.AlignmentFlag.AlignCenter)
        cards.addWidget(self.no_matches)
        cards.addStretch()
        content.setStyleSheet("""
            QPushButton { background: palette(base); border: 1px solid palette(mid); border-radius: 6px; }
            QPushButton:hover { background: palette(alternate-base); border-color: palette(highlight); }
            QPushButton:focus { border: 2px solid palette(highlight); }
            QLabel { color: palette(text); background: transparent; }
        """)
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        available = self.screen().availableGeometry()
        self.resize(min(560, available.width() * 9 // 10), min(640, available.height() * 9 // 10))
        search.textChanged.connect(self._filter)
        self._filter("")
        search.setFocus()

    def _filter(self, text):
        query = text.strip().casefold()
        first_match = None
        for title, card in self.choices:
            matches = query in title
            card.setVisible(matches)
            if matches and first_match is None:
                first_match = card
            card.setDefault(card is first_match)
        self.no_matches.setVisible(first_match is None)
        self.scroll.verticalScrollBar().setValue(0)

    def _choose(self, descriptor, model_ref):
        self.accept()
        self.chosen.emit(descriptor.operation, descriptor.version, model_ref)
