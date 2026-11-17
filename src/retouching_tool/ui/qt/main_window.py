from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QMainWindow, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget

from ... import APP_AUTHOR, PRODUCT_NAME, __version__
from ...application.contracts import StartupSnapshot
from ...errors import RetouchingError
from .batch_panel import BatchPanel
from .stage_card import StageCard


class MainWindow(QMainWindow):
    def __init__(self, snapshot: StartupSnapshot, pipeline_service=None, *, application=None):
        super().__init__()
        self.pipeline_service = pipeline_service
        self.setWindowTitle(f"{PRODUCT_NAME} {__version__}")
        self.resize(920, 740)
        content = QWidget()
        layout = QVBoxLayout(content)
        title = QLabel(f"{PRODUCT_NAME} · by {APP_AUTHOR}")
        title.setStyleSheet("font-size: 24px; font-weight: 600; padding: 8px 0;")
        layout.addWidget(title)
        self.pipeline_page = QWidget()
        pipeline_layout = QVBoxLayout(self.pipeline_page)
        details = QPlainTextEdit()
        details.setObjectName("startupDetails")
        details.setReadOnly(True)
        details.setMaximumHeight(200)
        details.setPlainText("\n".join([
            f"Backend: {snapshot.backend}", f"Pipeline: {snapshot.pipeline_name}",
            f"Loaded from: {snapshot.pipeline_origin}", "", "Selected sources:",
            *([str(p) for p in snapshot.sources] or ["No sources selected"]), "",
            f"Checkpoints: {snapshot.checkpoints}", f"Temporary artifacts: {snapshot.temporary}",
            f"Logs: {snapshot.logs}", "", *snapshot.notices,
        ]))
        pipeline_layout.addWidget(details)
        self.command_error = QLabel()
        self.command_error.setObjectName("pipelineCommandError")
        self.command_error.setWordWrap(True)
        pipeline_layout.addWidget(self.command_error)
        self.cards = QVBoxLayout()
        card_content = QWidget()
        card_content.setLayout(self.cards)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(card_content)
        pipeline_layout.addWidget(scroll)
        self.batch_panel = None
        if application is None:
            layout.addWidget(self.pipeline_page)
        else:
            self.batch_panel = BatchPanel(snapshot.sources, application)
            self.batch_panel.addTab(self.pipeline_page, "Pipeline")
            self.batch_panel.pipeline_locked.connect(self._set_pipeline_locked)
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
        if pipeline_service is not None:
            self._refresh_cards()
        self.setCentralWidget(content)

    def closeEvent(self, event):
        if self.batch_panel is not None and not self.batch_panel.request_close():
            event.ignore()
            return
        super().closeEvent(event)

    def _set_pipeline_locked(self, locked):
        self.pipeline_page.setEnabled(not locked)
        if not locked:
            self._refresh_cards()
            self.batch_panel.refresh_pipeline()

    def _refresh_cards(self):
        while self.cards.count():
            widget = self.cards.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        service = self.pipeline_service
        for status in service.status.stages:
            descriptor = service.registry.descriptor(status.stage.operation, status.stage.operation_version)
            self.cards.addWidget(StageCard(status, descriptor,
                lambda key, enabled: self._command(lambda: service.set_enabled(key, enabled)),
                lambda key, parameters: self._command(lambda: service.set_parameters(key, parameters)),
                lambda: self._command(service.recheck)))
        self.cards.addStretch()

    def _command(self, action):
        try:
            action()
            self.command_error.clear()
        except RetouchingError as exc:
            self.command_error.setText(f"{exc.code}: {exc}")
        if self.pipeline_service.diagnostics.failure:
            self.command_error.setText("\n".join(filter(None, (self.command_error.text(), self.pipeline_service.diagnostics.failure))))
        if self.batch_panel is not None:
            self.batch_panel.refresh_pipeline()
        # Rebuild after the originating control's signal has returned.
        QTimer.singleShot(0, self._refresh_cards)
