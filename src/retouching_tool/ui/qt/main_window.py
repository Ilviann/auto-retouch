from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from ... import APP_AUTHOR, PRODUCT_NAME, __version__
from ...application.contracts import StartupSnapshot
from .batch_panel import BatchPanel
from .diagnostics_dialog import DiagnosticsDialog
from .pipeline_panel import PipelinePanel
from .settings_dialog import SettingsDialog
from .window_size import WindowSize


class MainWindow(QMainWindow):
    def __init__(self, snapshot: StartupSnapshot, pipeline_service=None, *, application=None):
        super().__init__()
        self.setWindowTitle(f"{PRODUCT_NAME} {__version__}")
        self.resize(1180, 760)
        content = QWidget()
        layout = QVBoxLayout(content)
        title = QLabel(f"{PRODUCT_NAME} · by {APP_AUTHOR}")
        title.setStyleSheet("font-size: 24px; font-weight: 600; padding: 8px 0;")
        heading = QHBoxLayout()
        heading.addWidget(title, 1)
        layout.addLayout(heading)
        self.pipeline_page = PipelinePanel(snapshot, pipeline_service)
        self.batch_panel = None
        if application is None:
            layout.addWidget(self.pipeline_page)
        else:
            WindowSize(self, "main", application)
            self.batch_panel = BatchPanel(snapshot.sources, application, self.pipeline_page)
            diagnostics = QPushButton("Diagnostics…", objectName="openDiagnostics")
            diagnostics.clicked.connect(lambda: self._diagnostics())
            heading.addWidget(diagnostics)
            self.batch_panel.diagnostics_requested.connect(self._diagnostics)
            settings = QPushButton("Settings…", objectName="openSettings")
            settings.clicked.connect(self._settings)
            self.batch_panel.settings_available.connect(settings.setEnabled)
            settings.setEnabled(self.batch_panel.folders.isEnabled())
            heading.addWidget(settings)
            self.batch_panel.pipeline_locked.connect(self.pipeline_page.set_locked)
            self.batch_panel.operation_preview_available.connect(self.pipeline_page.set_preview_available)
            self.pipeline_page.preview_requested.connect(self.batch_panel.preview_stage)
            self.pipeline_page.changed.connect(self.batch_panel.refresh_pipeline)
            self.batch_panel.ready_to_close.connect(self.close)
            layout.addWidget(self.batch_panel)
            warning = QLabel()
            warning.setObjectName("diagnosticsWarning")
            warning.setTextFormat(Qt.TextFormat.PlainText)
            warning.setWordWrap(True)
            layout.addWidget(warning)
            diagnostics_timer = QTimer(self, interval=500)
            def update_warning():
                warning.setText("\n".join(filter(None, (application.diagnostics.failure, application.window_state_warning))))
            diagnostics_timer.timeout.connect(update_warning)
            diagnostics_timer.start()
            update_warning()
        self.setCentralWidget(content)

    def _diagnostics(self, job_id=""):
        monitor = self.batch_panel.monitor

        def show_operation(event):
            found = self.pipeline_page.show_diagnostic_operation(event)
            if found and monitor.isVisible():
                monitor.reject()
            return found

        dialog = DiagnosticsDialog(monitor if monitor.isVisible() else self, self.batch_panel.application,
                                   self.batch_panel.show_diagnostic_photo, show_operation,
                                   job_id=job_id)
        if monitor.isVisible() and not monitor.close_button.isEnabled():
            dialog.operation.hide()
        dialog.finished.connect(dialog.deleteLater)
        dialog.open()

    def _settings(self):
        application = self.batch_panel.application
        previous = application.config
        dialog = SettingsDialog(self, application)
        dialog.accepted.connect(lambda: self.pipeline_page.show_configuration(application.config))
        dialog.accepted.connect(lambda: self.batch_panel.configuration_changed(previous))
        dialog.finished.connect(dialog.deleteLater)
        dialog.open()

    def closeEvent(self, event):
        if any(dialog.pending is not None for dialog in self.findChildren(SettingsDialog)):
            event.ignore()
            return
        if self.batch_panel is not None and not self.batch_panel.request_close():
            event.ignore()
            return
        for dialog in self.findChildren(DiagnosticsDialog):
            dialog.reject()
        super().closeEvent(event)
