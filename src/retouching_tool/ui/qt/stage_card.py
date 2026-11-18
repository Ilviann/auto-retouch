"""Descriptor-driven stage status and parameters; readiness belongs to services."""

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)


def parameter_control(parameter):
    control = QDoubleSpinBox(objectName=f"parameter-{parameter.name}")
    control.setDecimals(4)
    control.setRange(parameter.minimum, parameter.maximum)
    control.setSingleStep(.1)
    return control


class StageCard(QGroupBox):
    def __init__(self, stage_id, descriptor, *, on_enable, on_parameter, on_recheck, on_move, on_remove, on_preview):
        super().__init__(descriptor.title)
        self.descriptor = descriptor
        self.setObjectName(f"stage-{stage_id}")
        self.setToolTip(f"Operation version {descriptor.version}\nStage ID {stage_id}")
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        self.toggle = QCheckBox("Enabled", objectName="stageEnabled")
        self.toggle.toggled.connect(lambda checked: on_enable(stage_id, checked))
        actions.addWidget(self.toggle)
        actions.addStretch()
        self.move_up = QPushButton("Move up", objectName="moveStageUp")
        self.move_up.clicked.connect(lambda: on_move(stage_id, -1))
        actions.addWidget(self.move_up)
        self.move_down = QPushButton("Move down", objectName="moveStageDown")
        self.move_down.clicked.connect(lambda: on_move(stage_id, 1))
        actions.addWidget(self.move_down)
        remove = QPushButton("Remove", objectName="removeStage")
        remove.clicked.connect(lambda: on_remove(stage_id))
        actions.addWidget(remove)
        layout.addLayout(actions)
        self.status_label = QLabel(objectName="stageStatus", wordWrap=True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status_label)
        data = QLabel(self._data_contract(), objectName="stageDataContract", wordWrap=True)
        data.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(data)
        self.preview = QPushButton("Preview", objectName="previewStage")
        self.preview.setToolTip("Run this operation on the original selected photo.")
        self.preview.setEnabled(False)
        self._preview_ready = False
        self.preview.clicked.connect(lambda: on_preview(stage_id, False))
        self.preview_pipeline = QPushButton("Preview pipeline", objectName="previewStagePipeline")
        self.preview_pipeline.setToolTip("Compare the photo before and after this operation with earlier enabled operations applied.")
        self.preview_pipeline.setEnabled(False)
        self.preview_pipeline.clicked.connect(lambda: on_preview(stage_id, True))
        previews = QHBoxLayout()
        previews.addWidget(self.preview)
        previews.addWidget(self.preview_pipeline)
        layout.addLayout(previews)

        form = QFormLayout()
        self.parameters = {}
        self.displayed_values = {}
        for parameter in descriptor.parameters:
            control = parameter_control(parameter)
            self.parameters[parameter.name] = control
            form.addRow(parameter.label, control)

        def commit(name, control, initial):
            if control.value() != initial:
                on_parameter(stage_id, name, control.value())

        for name, control in self.parameters.items():
            control.editingFinished.connect(
                lambda name=name, control=control: commit(name, control, self.displayed_values[name]))
        layout.addLayout(form)
        recheck = QPushButton("Recheck assets and dependencies")
        recheck.setObjectName("recheckStage")
        recheck.clicked.connect(on_recheck)
        layout.addWidget(recheck)
        self.model_page = None
        self.link = QPushButton("Open model page", objectName="modelPage")
        self.link.clicked.connect(self._open_model_page)
        layout.addWidget(self.link)

    def _data_contract(self):
        descriptor = self.descriptor
        inputs = ["pixels", "source metadata"]
        inputs.extend(f"{spec.key} v{spec.version} (required)" for spec in descriptor.requires)
        inputs.extend(f"{spec.key} v{spec.version} (optional)" for spec in descriptor.optional)
        pixel_effect = "May change pixels" if descriptor.changes_pixels else "Passes pixels through"
        outputs = [f"{pixel_effect} and preserves source metadata"]
        if descriptor.changes_geometry:
            outputs.append("may change geometry")
        outputs += [f"produces {spec.key} v{spec.version}" for spec in descriptor.provides]
        if descriptor.preserves and not descriptor.changes_geometry:
            outputs.append(f"can retain {', '.join(descriptor.preserves)}")
        if descriptor.invalidates:
            outputs.append(f"invalidates {', '.join(descriptor.invalidates)}")
        if descriptor.changes_geometry:
            outputs.append("drops upstream context")
        elif descriptor.changes_pixels:
            outputs.append("drops pixel-dependent and unpreserved context")
        else:
            outputs.append("passes other compatible context through")
        return f"Uses {', '.join(inputs)}\n{', '.join(outputs)}"

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
        text = {"ready": "Ready", "skipped": "Off — settings retained", "blocked": "Error: required analysis context is missing",
                "unavailable": "Error: model or backend unavailable — stage is off"}[status.state]
        details = [text, *(f"{i.code}: {i.message}" for i in status.issues), *status.dependency_errors]
        self.status_label.setText("\n".join(details))
        for name, value in status.stage.parameters:
            control = self.parameters[name]
            control.setValue(value)
            self.displayed_values[name] = control.value()
        self.model_page = status.model_page
        self.link.setVisible(bool(self.model_page))

    def _open_model_page(self):
        if self.model_page is not None:
            QDesktopServices.openUrl(QUrl(self.model_page))
