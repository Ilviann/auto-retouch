"""Descriptor-driven stage status and parameters; readiness belongs to services."""

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)


class StageCard(QGroupBox):
    def __init__(self, stage_id, descriptor, *, on_enable, on_parameter, on_recheck, on_move, on_remove):
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

        form = QFormLayout()
        self.parameters = {}
        self.displayed_values = {}
        for parameter in descriptor.parameters:
            control = QDoubleSpinBox()
            control.setObjectName(f"parameter-{parameter.name}")
            control.setRange(parameter.minimum, parameter.maximum)
            control.setDecimals(4)
            control.setSingleStep(.1)
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

    def show_status(self, status, *, can_move_up, can_move_down):
        with QSignalBlocker(self.toggle):
            self.toggle.setChecked(status.stage.enabled)
        self.toggle.setEnabled(not status.issues)
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
