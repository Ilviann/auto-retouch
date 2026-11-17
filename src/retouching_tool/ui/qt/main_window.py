from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QMainWindow, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget

from ... import APP_AUTHOR, PRODUCT_NAME, __version__
from ...application.contracts import StartupSnapshot
from ...errors import RetouchingError
from .stage_card import StageCard


class MainWindow(QMainWindow):
    def __init__(self, snapshot: StartupSnapshot, pipeline_service=None):
        super().__init__()
        self.pipeline_service = pipeline_service
        self.setWindowTitle(f"{PRODUCT_NAME} {__version__}")
        self.resize(780, 520)
        content = QWidget()
        layout = QVBoxLayout(content)
        title = QLabel(f"{PRODUCT_NAME} · by {APP_AUTHOR}")
        title.setStyleSheet("font-size: 24px; font-weight: 600; padding: 8px 0;")
        layout.addWidget(title)
        status = QLabel("Pipeline contracts ready. Photo import, previews, and batch export will be added in later milestones.")
        status.setWordWrap(True)
        layout.addWidget(status)
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
        layout.addWidget(details)
        self.command_error = QLabel()
        self.command_error.setObjectName("pipelineCommandError")
        self.command_error.setWordWrap(True)
        layout.addWidget(self.command_error)
        self.cards = QVBoxLayout()
        card_content = QWidget()
        card_content.setLayout(self.cards)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(card_content)
        layout.addWidget(scroll)
        if pipeline_service is not None:
            self._refresh_cards()
        self.setCentralWidget(content)

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
        # Rebuild after the originating control's signal has returned.
        QTimer.singleShot(0, self._refresh_cards)
