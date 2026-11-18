"""Search local diagnostic events and inspect their original technical details."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...config import LOG_LEVELS
from ...diagnostics.history import DiagnosticQuery


class DiagnosticsDialog(QDialog):
    def __init__(self, parent, history, show_photo, show_operation, *, job_id=""):
        super().__init__(parent)
        self.history = history
        self.show_photo = show_photo
        self.show_operation = show_operation
        self.setWindowTitle("Diagnostics")
        self.resize(880, 640)
        layout = QVBoxLayout(self)
        self.filters = QWidget()
        filters = QGridLayout(self.filters)
        filters.setContentsMargins(0, 0, 0, 0)
        self.text = QLineEdit(placeholderText="Message, code, or details", objectName="diagnosticSearch")
        self.severity = QComboBox(objectName="diagnosticSeverity")
        self.severity.addItem("All levels", LOG_LEVELS[0])
        for severity in LOG_LEVELS[1:]:
            self.severity.addItem(severity.title(), severity)
        self.severity.setCurrentIndex(self.severity.findData("WARNING") if job_id else 0)
        self.job_id = QLineEdit(job_id, placeholderText="Any job", objectName="diagnosticJobId")
        self.image_id = QLineEdit(placeholderText="Any photo", objectName="diagnosticImageId")
        self.stage_id = QLineEdit(placeholderText="Any operation", objectName="diagnosticStageId")
        for row, column, title, field in ((0, 0, "Search", self.text), (0, 2, "Minimum severity", self.severity),
                                          (1, 0, "Job ID", self.job_id), (1, 2, "Photo ID", self.image_id),
                                          (1, 4, "Stage ID", self.stage_id)):
            label = QLabel(title)
            label.setBuddy(field)
            filters.addWidget(label, row, column)
            filters.addWidget(field, row, column + 1)
        search = QPushButton("Search / Refresh", objectName="refreshDiagnostics", default=True)
        search.clicked.connect(self._search)
        filters.addWidget(search, 0, 5)
        layout.addWidget(self.filters)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 4, objectName="diagnosticEvents")
        self.table.setHorizontalHeaderLabels(("Local time", "Severity", "Event code", "Message"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._select_event)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        splitter.addWidget(self.table)
        self.details = QPlainTextEdit(readOnly=True, objectName="diagnosticDetails",
                                      accessibleName="Technical details for the selected event")
        splitter.addWidget(self.details)
        splitter.setSizes([280, 220])
        layout.addWidget(splitter, 1)

        self.status = QLabel(wordWrap=True, textFormat=Qt.TextFormat.PlainText, objectName="diagnosticStatus")
        self.feedback = QLabel(wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        layout.addWidget(self.feedback)
        actions = QHBoxLayout()
        self.copy = QPushButton("Copy details", objectName="copyDiagnosticDetails")
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.details.toPlainText()))
        self.photo = QPushButton("Show photo", objectName="showDiagnosticPhoto")
        self.photo.clicked.connect(lambda: self._reveal(self.show_photo, "The photo is outside the current grid or selected job."))
        self.operation = QPushButton("Show operation", objectName="showDiagnosticOperation")
        self.operation.clicked.connect(lambda: self._reveal(self.show_operation, "The operation is outside the current pipeline."))
        self.open_folder = QPushButton("Open log folder", objectName="openDiagnosticFolder")
        self.open_folder.setEnabled(False)
        self.open_folder.clicked.connect(self._open_folder)
        for button in (self.copy, self.photo, self.operation, self.open_folder):
            button.setAutoDefault(False)
            actions.addWidget(button)
        actions.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)

        self.events = ()
        self.directory = None
        self.pending = None
        self.cancelled = Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="diagnostic-history")
        self.timer = QTimer(self, interval=50)
        self.timer.timeout.connect(self._finish_search)
        self.finished.connect(self._stop)
        self._search()

    def _search(self):
        if self.pending is not None:
            return
        query = DiagnosticQuery(self.text.text(), self.severity.currentData(), self.job_id.text(),
                                self.image_id.text(), self.stage_id.text())
        self.events = ()
        self.table.setRowCount(0)
        self._select_event()
        self.filters.setEnabled(False)
        self.status.setText("Searching retained logs…")
        self.pending = self.executor.submit(self.history.search, query, cancelled=self.cancelled.is_set)
        self.timer.start()

    def _finish_search(self):
        if not self.pending.done():
            return
        self.timer.stop()
        future, self.pending = self.pending, None
        self.filters.setEnabled(True)
        try:
            result = future.result()
        except Exception as exc:
            self.history.diagnostics.emit("DIAGNOSTIC_SEARCH_FAILED", "Could not search retained diagnostics.",
                                          level="ERROR", component="diagnostics", exc_info=True,
                                          job_id=self.job_id.text() or None, image_id=self.image_id.text() or None,
                                          stage_instance_id=self.stage_id.text() or None)
            self.status.setText(f"DIAGNOSTIC_SEARCH_FAILED · {exc}")
            return

        self.directory = result.directory
        self.open_folder.setEnabled(True)
        self.events = result.events
        self.table.setRowCount(len(self.events))
        for row, event in enumerate(self.events):
            timestamp = event.timestamp.astimezone().isoformat(sep=" ", timespec="milliseconds")
            for column, value in enumerate((timestamp, event.severity, event.code, event.message)):
                self.table.setItem(row, column, QTableWidgetItem(value))
        if self.events:
            self.table.selectRow(0)
        match_text = f"{len(self.events)} matching events" if self.events else "No matching events in available logs."
        if result.has_more:
            match_text = f"Showing the latest {len(self.events)} matches. Refine the filters for older events."
        messages = [match_text]
        if result.skipped_files or result.invalid_records:
            messages.append(f"Some history could not be read ({result.skipped_files} files or folders, "
                            f"{result.invalid_records} invalid records).")
        if result.storage_warning:
            messages.append(result.storage_warning)
        self.status.setText("\n".join(messages))

    def _selected_event(self):
        row = self.table.currentRow()
        return self.events[row] if 0 <= row < len(self.events) and self.table.selectedItems() else None

    def _select_event(self):
        event = self._selected_event()
        self.details.setPlainText(event.details if event else "")
        self.copy.setEnabled(event is not None)
        self.photo.setEnabled(bool(event and event.image_id))
        self.operation.setEnabled(bool(event and event.stage_id))
        self.feedback.clear()

    def _reveal(self, show, unavailable):
        event = self._selected_event()
        if event and show(event):
            self.accept()
        else:
            self.feedback.setText(unavailable)

    def _open_folder(self):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.directory))):
            message = f"Could not open {self.directory}. Check the Logs folder in Settings."
            self.history.diagnostics.emit("DIAGNOSTIC_FOLDER_OPEN_FAILED", message, level="WARNING",
                                          component="desktop", log_path=self.directory, job_id=self.job_id.text() or None)
            self.feedback.setText(message)

    def _stop(self):
        self.timer.stop()
        self.cancelled.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
