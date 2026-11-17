from PySide6.QtWidgets import QLabel, QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget

from ... import PRODUCT_NAME, __version__
from ...application.contracts import StartupSnapshot


class MainWindow(QMainWindow):
    def __init__(self, snapshot: StartupSnapshot):
        super().__init__()
        self.setWindowTitle(f"{PRODUCT_NAME} {__version__}")
        self.resize(780, 520)
        content = QWidget()
        layout = QVBoxLayout(content)
        title = QLabel(PRODUCT_NAME)
        title.setStyleSheet("font-size: 24px; font-weight: 600; padding: 8px 0;")
        layout.addWidget(title)
        status = QLabel("Runtime foundation ready. Photo processing will be added in later milestones.")
        status.setWordWrap(True)
        layout.addWidget(status)
        details = QPlainTextEdit()
        details.setObjectName("startupDetails")
        details.setReadOnly(True)
        details.setPlainText("\n".join([
            f"Backend: {snapshot.backend}", f"Pipeline: {snapshot.pipeline_name}",
            f"Loaded from: {snapshot.pipeline_origin}", "", "Selected sources:",
            *([str(p) for p in snapshot.sources] or ["No sources selected"]), "",
            f"Checkpoints: {snapshot.checkpoints}", f"Temporary artifacts: {snapshot.temporary}",
            f"Logs: {snapshot.logs}", "", *snapshot.notices,
        ]))
        layout.addWidget(details)
        self.setCentralWidget(content)
