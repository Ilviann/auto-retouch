from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

from ... import APP_AUTHOR, PRODUCT_NAME, __version__
from ...application.contracts import StartupSnapshot
from .batch_panel import BatchPanel
from .pipeline_panel import PipelinePanel


class MainWindow(QMainWindow):
    def __init__(self, snapshot: StartupSnapshot, pipeline_service=None, *, application=None):
        super().__init__()
        self.setWindowTitle(f"{PRODUCT_NAME} {__version__}")
        self.resize(920, 740)
        content = QWidget()
        layout = QVBoxLayout(content)
        title = QLabel(f"{PRODUCT_NAME} · by {APP_AUTHOR}")
        title.setStyleSheet("font-size: 24px; font-weight: 600; padding: 8px 0;")
        layout.addWidget(title)
        self.pipeline_page = PipelinePanel(snapshot, pipeline_service)
        self.batch_panel = None
        if application is None:
            layout.addWidget(self.pipeline_page)
        else:
            self.batch_panel = BatchPanel(snapshot.sources, application)
            self.batch_panel.addTab(self.pipeline_page, "Pipeline")
            self.batch_panel.pipeline_locked.connect(self.pipeline_page.set_locked)
            self.pipeline_page.changed.connect(self.batch_panel.refresh_pipeline)
            self.batch_panel.ready_to_close.connect(self.close)
            layout.addWidget(self.batch_panel)
            warning = QLabel()
            warning.setObjectName("diagnosticsWarning")
            warning.setTextFormat(Qt.TextFormat.PlainText)
            warning.setWordWrap(True)
            layout.addWidget(warning)
            diagnostics_timer = QTimer(self, interval=500)
            diagnostics_timer.timeout.connect(lambda: warning.setText(application.diagnostics.failure or ""))
            diagnostics_timer.start()
            warning.setText(application.diagnostics.failure or "")
        self.setCentralWidget(content)

    def closeEvent(self, event):
        if self.batch_panel is not None and not self.batch_panel.request_close():
            event.ignore()
            return
        super().closeEvent(event)
