"""Descriptor-driven stage status and parameters; readiness belongs to services."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel, QPushButton, QVBoxLayout


class StageCard(QGroupBox):
    def __init__(self, status, descriptor, on_enable, on_parameters, on_recheck):
        super().__init__(f"{status.title} · {status.stage.id}")
        self.setObjectName(f"stage-{status.stage.id}")
        layout = QVBoxLayout(self)
        toggle = QCheckBox("Enabled")
        toggle.setObjectName("stageEnabled")
        toggle.setChecked(status.stage.enabled)
        toggle.setEnabled(not status.issues)
        toggle.toggled.connect(lambda checked: on_enable(status.stage.id, checked))
        layout.addWidget(toggle)
        text = {"ready": "Ready", "skipped": "Off — settings retained", "blocked": "Error: required analysis context is missing",
                "unavailable": "Error: model or backend unavailable — stage is off"}[status.state]
        details = [text, *(f"{i.code}: {i.message}" for i in status.issues), *status.dependency_errors]
        label = QLabel("\n".join(details))
        label.setObjectName("stageStatus")
        label.setWordWrap(True)
        layout.addWidget(label)
        form = QFormLayout()
        controls = {}
        for parameter in descriptor.parameters:
            control = QDoubleSpinBox()
            control.setObjectName(f"parameter-{parameter.name}")
            control.setRange(parameter.minimum, parameter.maximum)
            control.setDecimals(4)
            control.setSingleStep(.1)
            control.setValue(status.stage.parameter(parameter.name))
            controls[parameter.name] = control
            form.addRow(parameter.label, control)
        def commit(name, control, initial):
            if control.value() != initial:
                on_parameters(status.stage.id, {**dict(status.stage.parameters), name: control.value()})
        for name, control in controls.items():
            control.editingFinished.connect(lambda name=name, control=control, initial=control.value(): commit(name, control, initial))
        layout.addLayout(form)
        recheck = QPushButton("Recheck assets and dependencies")
        recheck.setObjectName("recheckStage")
        recheck.clicked.connect(on_recheck)
        layout.addWidget(recheck)
        if status.model_page:
            link = QPushButton("Open model page")
            link.setObjectName("modelPage")
            link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(status.model_page)))
            layout.addWidget(link)
