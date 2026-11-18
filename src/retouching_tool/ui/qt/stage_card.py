"""Qt stage cards and shared controls for the stage comparison dialog."""

from PySide6.QtCore import QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QMenu, QPushButton, QSizePolicy, QSlider, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from ...config import SCALING_METHODS
from ...pipeline.definition import BLEND, ScalingParameter
from ...pipeline.image_state import FACE_REGIONS, PERSON_FOREGROUND


class ScalingControl(QComboBox):
    """Keep portable filter names independent of their display labels."""

    def __init__(self, *, inherit=False, **kwargs):
        super().__init__(**kwargs)
        if inherit:
            self.addItem("Use app setting", "inherit")
        for method in SCALING_METHODS:
            self.addItem({"bilinear": "Bilinear", "nearest": "Nearest neighbor"}[method], method)

    def value(self):
        return self.currentData()

    def setValue(self, method):
        self.setCurrentIndex(self.findData(method))


class BlendControl(QWidget):
    valueChanged = Signal(float)

    def __init__(self):
        super().__init__(objectName="parameter-blend")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal, objectName="blendSlider")
        self.slider.setAccessibleName("Blending")
        self.slider.setRange(0, 100)
        self.slider.setTracking(False)
        self.slider.setToolTip("0.00 keeps the stage input. 1.00 uses the full stage result.")
        self.setFocusProxy(self.slider)
        self.readout = QLabel()
        self.readout.setMinimumWidth(self.fontMetrics().horizontalAdvance("0.00"))
        row.addWidget(self.slider, 1)
        row.addWidget(self.readout)
        self.slider.sliderMoved.connect(self._display)
        self.slider.valueChanged.connect(self._changed)
        self.setValue(BLEND.default)

    def _display(self, step):
        self.readout.setText(f"{step / 100:.2f}")

    def _changed(self, step):
        self._display(step)
        self.valueChanged.emit(step / 100)

    def value(self):
        return self.slider.value() / 100

    def setValue(self, value):
        self.slider.setValue(round(value * 100))


def parameter_control(parameter):
    if parameter == BLEND:
        return BlendControl()
    if isinstance(parameter, ScalingParameter):
        return ScalingControl(inherit=True, objectName=f"parameter-{parameter.name}")
    control = QDoubleSpinBox(objectName=f"parameter-{parameter.name}")
    control.setDecimals(4)
    control.setRange(parameter.minimum, parameter.maximum)
    control.setSingleStep(.1)
    return control


def tag_row(label, entries, hint):
    row = QHBoxLayout()
    row.setSpacing(5)
    caption = QLabel(label)
    caption.setToolTip(hint)
    caption.setFixedWidth(caption.fontMetrics().horizontalAdvance("Out") + 4)
    row.addWidget(caption)
    aliases = {FACE_REGIONS.key: "areas:face", PERSON_FOREGROUND.key: "areas:people"}
    for key, version, optional in entries:
        name = aliases.get(key, key)
        tag = QLabel(f"{name} · optional" if optional else name, objectName=f"tag-{label}-{key}")
        tag.setTextFormat(Qt.TextFormat.PlainText)
        kind = "Optional input" if optional else "Required input" if label == "In" else "Output"
        tag.setToolTip(f"{kind} · {key}" + (f" v{version}" if version else ""))
        tag.setAccessibleName(f"{kind}, {name}")
        border = "dashed" if optional else "solid"
        fill = "transparent" if optional else "palette(alternate-base)"
        tag.setStyleSheet(f"border: 1px {border} palette(mid); border-radius: 5px; "
                          f"background: {fill}; padding: 1px 6px;")
        row.addWidget(tag, alignment=Qt.AlignmentFlag.AlignVCenter)
    if not entries:
        row.addWidget(QLabel("None"))
    row.addStretch()
    return row


class StageCard(QFrame):
    def __init__(self, stage_id, descriptor, *, on_enable, on_parameter, on_recheck, on_move, on_remove, on_preview):
        super().__init__(objectName=f"stage-{stage_id}")
        self.descriptor = descriptor
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setProperty("stageCard", True)
        self.setStyleSheet("""
            QFrame[stageCard="true"] {
                background: palette(base); border: 1px solid palette(mid); border-radius: 6px;
            }
            QFrame#stageParameters {
                background: palette(alternate-base); border: 0; border-radius: 4px;
            }
        """)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Minimum)
        self.setAccessibleName(f"{descriptor.title}, version {descriptor.version}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.toggle = QCheckBox(descriptor.title, objectName="stageEnabled")
        self.toggle.setAccessibleName(f"Enable {descriptor.title}")
        font = self.toggle.font()
        font.setBold(True)
        self.toggle.setFont(font)
        self.toggle.setStyleSheet("QCheckBox:disabled { color: palette(text); }")
        self.toggle.setToolTip(f"Enable or disable {descriptor.title}")
        self.toggle.toggled.connect(lambda checked: on_enable(stage_id, checked))
        header.addWidget(self.toggle)
        version = QLabel(f"v{descriptor.version}", objectName="stageVersion")
        version.setToolTip(f"Implementation version {descriptor.version}\nStage ID {stage_id}")
        header.addWidget(version)
        header.addStretch()
        icon = QStyle.StandardPixmap
        self.move_up = self._action("Move up", "moveStageUp", icon.SP_ArrowUp)
        self.move_up.clicked.connect(lambda: on_move(stage_id, -1))
        header.addWidget(self.move_up)
        self.move_down = self._action("Move down", "moveStageDown", icon.SP_ArrowDown)
        self.move_down.clicked.connect(lambda: on_move(stage_id, 1))
        header.addWidget(self.move_down)
        remove = self._action("Remove", "removeStage", icon.SP_TrashIcon)
        remove.clicked.connect(lambda: on_remove(stage_id))
        header.addWidget(remove)
        self.settings = QToolButton(objectName="stageMenu")
        self.settings.setText("Settings")
        self.settings.setAccessibleName("Stage settings menu")
        self.settings.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.settings)
        self.model_page = None
        if descriptor.model_refs:
            self.link = self.menu.addAction("Open model page in browser", self._open_model_page)
            self.link.setObjectName("modelPage")
        check = self.menu.addAction("Check required model files" if descriptor.model_refs else "Check dependencies",
                                    lambda: on_recheck(stage_id))
        check.setObjectName("recheckStage")
        self.settings.setMenu(self.menu)
        header.addWidget(self.settings)
        layout.addLayout(header)

        self.status_label = QLabel(objectName="stageStatus", wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)
        inputs = [("pixels", None, False)]
        inputs.extend((spec.key, spec.version, False) for spec in descriptor.requires)
        inputs.extend((spec.key, spec.version, True) for spec in descriptor.optional)
        outputs = [("pixels", None, False)] if descriptor.changes_pixels else []
        outputs.extend((spec.key, spec.version, False) for spec in descriptor.provides)
        layout.addLayout(tag_row("In", inputs, "Data used by this stage. Dashed tags are optional."))
        effects = ["Source metadata is preserved."]
        if descriptor.changes_geometry:
            effects.append("A geometry change drops upstream context.")
        elif descriptor.changes_pixels:
            effects.append("Pixel edits drop pixel-dependent and unpreserved context.")
        if descriptor.preserves:
            effects.append(f"Can retain {', '.join(descriptor.preserves)}.")
        if descriptor.invalidates:
            effects.append(f"Invalidates {', '.join(descriptor.invalidates)}.")
        layout.addLayout(tag_row("Out", outputs, " ".join(effects)))

        self.parameters = {}
        self.displayed_values = {}
        if descriptor.editable_parameters:
            settings = QFrame(objectName="stageParameters")
            settings.setFrameShape(QFrame.Shape.StyledPanel)
            form = QFormLayout(settings)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            form.setContentsMargins(9, 6, 9, 6)
            for parameter in descriptor.editable_parameters:
                name = parameter.name
                control = parameter_control(parameter)
                self.parameters[name] = control
                form.addRow(parameter.label, control)
                if isinstance(control, BlendControl):
                    control.valueChanged.connect(lambda value: on_parameter(stage_id, BLEND.name, value))
                elif isinstance(control, ScalingControl):
                    control.activated.connect(lambda _, n=name, c=control: on_parameter(stage_id, n, c.value()))
                else:
                    control.editingFinished.connect(lambda n=name, c=control:
                        self._commit_parameter(stage_id, n, c, on_parameter))
            layout.addWidget(settings)

        self.preview = QPushButton("Preview", objectName="previewStage")
        self.preview.setToolTip("Run this operation on the original selected photo.")
        self.preview.clicked.connect(lambda: on_preview(stage_id, False))
        self.preview_pipeline = QPushButton("Preview in pipeline", objectName="previewStagePipeline")
        self.preview_pipeline.setToolTip("Compare this operation's input and output after earlier enabled stages.")
        self.preview_pipeline.clicked.connect(lambda: on_preview(stage_id, True))
        self._preview_ready = False
        self.set_preview_available(False)
        previews = QHBoxLayout()
        previews.addStretch()
        previews.addWidget(self.preview)
        previews.addWidget(self.preview_pipeline)
        layout.addLayout(previews)

    def _action(self, label, name, icon):
        button = QToolButton(objectName=name)
        button.setIcon(self.style().standardIcon(icon))
        button.setAutoRaise(True)
        button.setAccessibleName(label)
        button.setToolTip(label)
        return button

    def _commit_parameter(self, stage_id, name, control, on_parameter):
        if control.value() != self.displayed_values[name]:
            on_parameter(stage_id, name, control.value())

    def set_preview_available(self, available):
        self.preview.setEnabled(available and self._preview_ready)
        self.preview_pipeline.setEnabled(available and self._preview_ready)

    def show_status(self, status, *, can_move_up, can_move_down):
        with QSignalBlocker(self.toggle):
            self.toggle.setChecked(status.stage.enabled)
        self.toggle.setEnabled(not status.issues)
        self._preview_ready = not status.issues
        self.move_up.setEnabled(can_move_up)
        self.move_down.setEnabled(can_move_down)
        text = {"ready": "", "skipped": "Off — settings retained", "blocked": "Error: required analysis context is missing",
                "unavailable": "Error: model or backend unavailable — stage is off"}[status.state]
        details = [text, *(f"{i.code}: {i.message}" for i in status.issues), *status.dependency_errors]
        self.status_label.setText("\n".join(filter(None, details)))
        self.status_label.setVisible(bool(self.status_label.text()))
        for name, value in status.stage.parameters:
            control = self.parameters[name]
            with QSignalBlocker(control):
                control.setValue(value)
            self.displayed_values[name] = control.value()
        self.model_page = status.model_page
        if self.descriptor.model_refs:
            self.link.setEnabled(bool(self.model_page))

    def _open_model_page(self):
        if self.model_page:
            QDesktopServices.openUrl(QUrl(self.model_page))
