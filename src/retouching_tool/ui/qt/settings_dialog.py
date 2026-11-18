"""Machine-local settings for the active configuration file."""

from concurrent.futures import ThreadPoolExecutor
from functools import partial

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout)

from ...config import FolderSettings, LoggingSettings, ScalingSettings, LOG_LEVELS, LOG_LIMITS
from ...errors import RetouchingError
from .stage_card import ScalingControl


def _label(text):
    label = QLabel(text, wordWrap=True, textFormat=Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class SettingsDialog(QDialog):
    def __init__(self, parent, application):
        super().__init__(parent)
        self.application = application
        self.setWindowTitle("Settings")
        self.resize(580, 440)
        layout = QVBoxLayout(self)
        config = application.config
        location = QFormLayout()
        location.addRow("Configuration file", _label(str(config.file)))
        if config.using_defaults:
            location.addRow("", _label("Save creates this file."))
        location.addRow("ONNX Runtime backend", _label(application.pipeline_service.backend))
        location.addRow("PyTorch backend", _label(application.pipeline_service.pytorch_backend or "Unconfigured"))
        layout.addLayout(location)

        self.folder_group = QGroupBox("Folders")
        folders = QFormLayout(self.folder_group)
        self.fields = {}
        for name, title in (("checkpoints", "Checkpoints"), ("pipelines", "Pipeline presets"),
                            ("temporary", "Temporary artifacts"), ("logs", "Logs")):
            field = QLineEdit(getattr(config.folders, name), accessibleName=title)
            self.fields[name] = field
            browse = QPushButton("Browse…", accessibleName=f"Browse for {title.lower()}")
            browse.clicked.connect(partial(self._browse, name))
            row = QHBoxLayout()
            row.addWidget(field)
            row.addWidget(browse)
            folders.addRow(title, row)
            folders.addRow("", _label(f"In use: {getattr(config, name)}"))
        layout.addWidget(self.folder_group)

        self.scaling_group = QGroupBox("Image scaling")
        scaling_form = QFormLayout(self.scaling_group)
        self.scaling = {}
        for name, title in (("downscale", "Downscaling"), ("upscale", "Upscaling")):
            control = ScalingControl(objectName=f"default-{name}")
            control.setValue(getattr(config.scaling, name))
            self.scaling[name] = control
            scaling_form.addRow(title, control)
        layout.addWidget(self.scaling_group)

        self.log_group = QGroupBox("Logging")
        log = QFormLayout(self.log_group)
        self.level = QComboBox()
        self.level.addItems(LOG_LEVELS)
        self.level.setCurrentText(config.logging.level)
        log.addRow("Minimum severity", self.level)
        self.limits = {}
        for name, title, unit in (("max_file_mb", "Maximum file size", " MB"),
                                  ("max_total_mb", "Total storage", " MB"),
                                  ("retention_days", "Retention", " days")):
            control = QSpinBox(minimum=1, maximum=LOG_LIMITS[name], suffix=unit)
            control.setValue(getattr(config.logging, name))
            self.limits[name] = control
            log.addRow(title, control)
        layout.addWidget(self.log_group)
        self.message = _label("")
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.pending = None
        self.timer = QTimer(self, interval=50)
        self.timer.timeout.connect(self._finish_save)

    def _browse(self, name):
        field = self.fields[name]
        start = self.application.config.file.parent / field.text()
        path = QFileDialog.getExistingDirectory(self, "Choose folder", str(start))
        if path:
            field.setText(path)

    def _save(self):
        folders = FolderSettings(**{name: field.text() for name, field in self.fields.items()})
        logging = LoggingSettings(self.level.currentText(), **{name: spin.value() for name, spin in self.limits.items()})
        # Folder probes and model checksums can take time. Keep the event loop and job monitor responsive.
        self.folder_group.setEnabled(False)
        self.scaling_group.setEnabled(False)
        self.log_group.setEnabled(False)
        self.buttons.setEnabled(False)
        self.message.setText("Validating and saving settings…")
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="settings")
        scaling = ScalingSettings(**{name: control.value() for name, control in self.scaling.items()})
        self.pending = executor.submit(self.application.save_settings, folders, logging, scaling)
        executor.shutdown(wait=False)
        self.timer.start()

    def _finish_save(self):
        if not self.pending.done():
            return
        self.timer.stop()
        future, self.pending = self.pending, None
        try:
            future.result()
        except Exception as exc:
            if not isinstance(exc, RetouchingError):
                self.application.diagnostics.emit("CONFIG_SAVE_FAILED", "Cannot apply settings.", level="ERROR",
                                                  component="configuration", exc_info=True,
                                                  config_path=self.application.config.file)
            self.message.setText(f"{getattr(exc, 'code', 'CONFIG_SAVE_FAILED')}: {exc}")
            self.folder_group.setEnabled(True)
            self.scaling_group.setEnabled(True)
            self.log_group.setEnabled(True)
            self.buttons.setEnabled(True)
        else:
            self.accept()

    def reject(self):
        if self.pending is None:
            super().reject()
