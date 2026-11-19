"""Qt controls for preparing and monitoring the current batch."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from math import ceil
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QGroupBox,
                              QHBoxLayout, QHeaderView, QLabel, QLayout, QLineEdit,
                              QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter,
                              QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...application.batch import ExistingOutputDecision, ExistingOutputs
from ...errors import ResizeApprovalRequired, RetouchingError
from ...imaging.source import SOURCE_EXTENSIONS
from ...imaging.export_settings import FORMATS, BmpSettings, JpegSettings, JxlSettings, PngSettings, TiffSettings, encode_settings
from .photo_grid import PhotoGrid
from .picture_window import PictureWindow
from .stage_preview_window import StagePreviewWindow, confirm_resize
from .window_size import WindowSize


def _label(name):
    label = QLabel()
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _button(text, name, action):
    button = QPushButton(text)
    button.setObjectName(name)
    button.clicked.connect(action)
    return button


def _row(*widgets):
    row = QHBoxLayout()
    for widget in widgets:
        row.addWidget(widget)
    return row


def _table(name, headers, path_column):
    table = QTableWidget(0, len(headers))
    table.setObjectName(name)
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(path_column, QHeaderView.ResizeMode.Stretch)
    return table


def _size(byte_count):
    value = byte_count
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:,} B" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024


def _duration(seconds):
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}"


def confirm_existing_outputs(parent, collisions):
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Output files already exist")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(str(collisions))
    dialog.setInformativeText("Skip the affected photos, or overwrite the listed files with verified exports?")
    dialog.setDetailedText("\n".join(str(path) for path, _ in collisions.files))
    stop = dialog.addButton("Stop processing", QMessageBox.ButtonRole.RejectRole)
    skip = dialog.addButton("Skip existing files", QMessageBox.ButtonRole.AcceptRole)
    overwrite = dialog.addButton("Overwrite existing files", QMessageBox.ButtonRole.DestructiveRole)
    dialog.setDefaultButton(stop)
    dialog.setEscapeButton(stop)
    dialog.exec()
    if dialog.clickedButton() is skip:
        return ExistingOutputDecision("skip", collisions.files)
    if dialog.clickedButton() is overwrite:
        return ExistingOutputDecision("overwrite", collisions.files)
    return None


class ProcessingDialog(QDialog):
    def __init__(self, parent, confirm_break):
        super().__init__(parent)
        self.setWindowTitle("Processing")
        self.resize(720, 460)
        self.break_button = _button("Break", "cancelJob", confirm_break)
        self.close_button = _button("Close", "closeProcessing", self.reject)

    def reject(self):
        if self.close_button.isEnabled():
            super().reject()
        elif self.break_button.isEnabled():
            self.break_button.click()


class BatchPanel(QWidget):
    pipeline_locked = Signal(bool)
    settings_available = Signal(bool)
    operation_preview_available = Signal(bool)
    diagnostics_requested = Signal(str)
    ready_to_close = Signal()

    def __init__(self, sources, application, pipeline):
        super().__init__()
        self.application = application
        self._sources = sources
        self._index = None
        self._indexed_sources = ()
        self._scan_selection = None
        self._picture_id = None
        self._navigating = False
        self.pipeline = pipeline
        self._plan = None
        self._plan_revision = 0
        self._space_check_after = None
        self._space_check = None
        self._job_id = None
        self._status = None
        self._run = None
        self._elapsed = None
        self._started_at = None
        self._initial_work = None
        self._estimated_finish = None
        self._request_sent = None
        self._poll_failed = False
        self._closing = False
        self._picture_window = None
        self._stage_preview = None
        self._preview_target = None
        self._preview_available = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-batch")
        self._task = None
        self._report = None
        self.preferences_warning = ""
        self._preferences_dirty = False

        workspace = QVBoxLayout(self)
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.addWidget(self._build_folders())
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(pipeline)
        self.setup_scroll = QScrollArea(widgetResizable=True)
        self.setup_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.setup_scroll.setMinimumWidth(380)
        self.setup_scroll.setWidget(self._build_setup())
        splitter.addWidget(self.setup_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([430, 790])
        workspace.addWidget(splitter, 1)
        self.readiness = _label("batchReadiness")
        self.choose_preset = _button("Choose a preset…", "choosePreset", pipeline.presets.showPopup)
        self.start = _button("Start processing", "startBatch", self._create)
        footer = _row(self.readiness, self.choose_preset, self.start)
        footer.setStretch(0, 1)
        workspace.addLayout(footer)
        self._restore_preferences()
        self._update_export_summary()
        self._preferences_timer = QTimer(self, singleShot=True, interval=300)
        self._preferences_timer.timeout.connect(self._save_preferences)
        self.recursive.toggled.connect(self._scan)
        self.recursive.toggled.connect(self._preferences_changed)
        for signal in (self.destination.textChanged, self.mode.currentIndexChanged, self.format.currentIndexChanged,
                       self.quality.valueChanged, self.chroma.currentIndexChanged, self.progressive.toggled,
                       self.jxl_quality.valueChanged, self.jxl_lossless.toggled, self.jxl_effort.valueChanged,
                       self.png_compression.valueChanged, self.tiff_compression.currentIndexChanged, self.bmp_dpi.valueChanged):
            signal.connect(self._preferences_changed)
        for signal in (self.destination.textChanged, self.mode.currentIndexChanged, self.format.currentIndexChanged):
            signal.connect(self._invalidate_plan)
        self.refresh_pipeline()
        self._update_controls()
        self._timer = QTimer(self, interval=400)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        if self._sources:
            self._scan()

    def _build_folders(self):
        self.folders = QGroupBox("Sources and destination")
        form = QFormLayout(self.folders)
        self.source_text = QLineEdit(readOnly=True, placeholderText="Select a folder or photo files")
        self.source_text.setObjectName("batchSources")
        self._show_sources()
        form.addRow("Sources", _row(self.source_text,
                    _button("Choose folder…", "chooseSourceFolder", self._choose_folder),
                    _button("Choose photos…", "chooseSourceFiles", self._choose_files)))
        self.recursive = QCheckBox("Include subfolders")
        self.recursive.setObjectName("batchRecursive")
        self.scan = _button("Rescan source folder", "scanSources", self._scan)
        scan_row = _row(self.recursive)
        scan_row.addStretch()
        scan_row.addWidget(self.scan)
        form.addRow("", scan_row)
        self.destination = QLineEdit(placeholderText="Select an existing destination folder")
        self.destination.setObjectName("batchDestination")
        form.addRow("Destination", _row(self.destination,
                    _button("Browse…", "chooseDestination", self._choose_destination)))
        return self.folders

    def _build_setup(self):
        setup = QWidget()
        layout = QVBoxLayout(setup)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.source_summary = _label("batchSourceSummary")
        self.check_all = _button("Check all", "checkAllImages", partial(self._check_all, Qt.CheckState.Checked))
        self.uncheck_all = _button("Uncheck all", "uncheckAllImages", partial(self._check_all, Qt.CheckState.Unchecked))
        selection = _row(self.source_summary, self.check_all, self.uncheck_all)
        selection.setStretch(0, 1)
        layout.addLayout(selection)
        self.name_error = QPlainTextEdit(readOnly=True, objectName="sourceNameError")
        self.name_error.setAccessibleName("Duplicate source names")
        self.name_error.setMaximumHeight(84)
        self.name_error.hide()
        layout.addWidget(self.name_error)
        self.images = PhotoGrid(self.application.preview_service)
        self.images.itemChanged.connect(self._invalidate_plan)
        self.images.itemSelectionChanged.connect(self._update_controls)
        self.images.itemActivated.connect(self._view_original)
        layout.addWidget(self.images, 1)

        self.export_toggle = QPushButton(checkable=True, objectName="toggleExportSettings")
        self.export_toggle.setAccessibleName("Expand or collapse batch and export settings")
        layout.addWidget(self.export_toggle)
        self.export = QGroupBox("Batch and export settings")
        self.export_toggle.toggled.connect(self.export.setVisible)
        self.export.hide()
        form = QFormLayout(self.export)
        self.mode = QComboBox()
        self.mode.setObjectName("batchMode")
        self.mode.addItem("Sequential · one photo at a time", "sequential")
        self.mode.addItem("Staged · one operation at a time", "staged")
        form.addRow("Schedule", self.mode)
        self.format = QComboBox()
        self.format.setObjectName("exportFormat")
        self.format.addItems(FORMATS)
        form.addRow("Export format", self.format)

        self.quality = QSpinBox(minimum=1, maximum=95, value=JpegSettings().quality)
        self.quality.setObjectName("jpegQuality")
        self.chroma = QComboBox()
        self.chroma.setObjectName("jpegChroma")
        self.chroma.addItems(("4:4:4", "4:2:2", "4:2:0"))
        self.progressive = QCheckBox("Progressive")
        self.progressive.setObjectName("jpegProgressive")
        self.jxl_quality = QSpinBox(minimum=1, maximum=99, value=JxlSettings().quality)
        self.jxl_lossless = QCheckBox("Lossless")
        self.jxl_lossless.toggled.connect(self.jxl_quality.setDisabled)
        self.jxl_effort = QSpinBox(minimum=1, maximum=9, value=JxlSettings().effort)
        self.png_compression = QSpinBox(minimum=0, maximum=9, value=PngSettings().compression)
        self.tiff_compression = QComboBox()
        self.tiff_compression.addItems(("Deflate", "None"))
        self.bmp_dpi = QSpinBox(minimum=1, maximum=2400, value=BmpSettings().dpi, suffix=" DPI")
        first_option = form.rowCount()
        options = (
            ("JPEG", "Quality and chroma", _row(self.quality, self.chroma, self.progressive)),
            ("JPEG XL", "Quality", _row(self.jxl_quality, self.jxl_lossless)),
            ("JPEG XL", "Encoding effort", self.jxl_effort),
            ("PNG", "Compression level", self.png_compression),
            ("TIFF", "Compression", self.tiff_compression),
            ("BMP", "Print resolution", self.bmp_dpi),
            ("BMP", "", QLabel("24-bit RGB. EXIF and other photo metadata are omitted.", wordWrap=True)),
        )
        for _, label, control in options:
            form.addRow(label, control)

        def show_options(fmt):
            for row, (name, _, _) in enumerate(options, first_option):
                form.setRowVisible(row, name == fmt)

        show_options(self.format.currentText())
        self.format.currentTextChanged.connect(show_options)
        layout.addWidget(self.export)

        self.pipeline_summary = _label("batchPipelineSummary")
        self.space = _label("batchSpace")
        self.setup_message = _label("batchSetupMessage")
        for label in (self.pipeline_summary, self.space, self.setup_message):
            layout.addWidget(label)
        self._build_monitor()
        return setup

    def _build_monitor(self):
        self.monitor = monitor = ProcessingDialog(self, self._confirm_break)
        WindowSize(monitor, "processing", self.application)
        monitor.finished.connect(self._monitor_closed)
        layout = QVBoxLayout(monitor)
        self.refresh = _button("Refresh", "refreshJob", self._refresh)
        self.resume = _button("Continue", "resumeJob", self._start)
        self.pause = _button("Pause", "pauseJob", partial(self._request, "pause"))
        self.cancel = monitor.break_button
        self.job_heading = _label("jobHeading")
        self.job_heading.setText("No job selected.")
        self.job_diagnostics = _button("Job diagnostics…", "openJobDiagnostics",
                                       lambda: self.diagnostics_requested.emit(self._job_id))
        self.export_issues = _button("Export warnings and errors…", "exportJobIssues", self._export_issues)
        heading = _row(self.job_heading, self.job_diagnostics, self.export_issues)
        heading.setStretch(0, 1)
        layout.addLayout(heading)
        self.job_metadata = _label("jobMetadata")
        self.job_counts = _label("jobCounts")
        self.job_timing = _label("jobTiming")
        self.job_timing.setToolTip("Elapsed counts active processing time observed for this job in this window. "
                                   "The estimate uses progress since the worker last started.")
        self.worker_warning = _label("jobWorkerWarning")
        self.job_message = _label("jobMessage")
        self.job_progress = QProgressBar()
        self.job_progress.setObjectName("jobProgress")
        self.job_progress.hide()
        for widget in (self.job_metadata, self.job_progress, self.job_timing, self.job_counts,
                       self.worker_warning, self.job_message):
            layout.addWidget(widget)
        self.result_filter = "all"
        self.result_buttons = {}
        filters = QHBoxLayout()
        for key, title in (("all", "All photos"), ("failed", "Failed"), ("warnings", "With warnings")):
            button = QPushButton(title, checkable=True, autoDefault=False)
            button.clicked.connect(lambda _, value=key: self._filter_results(value))
            self.result_buttons[key] = button
            filters.addWidget(button)
        self.result_buttons["all"].setChecked(True)
        filters.addStretch()
        self.open_destination = _button("Open destination folder", "openDestination", self._open_destination)
        filters.addWidget(self.open_destination)
        layout.addLayout(filters)
        self.results = _table("jobImages", ("Source photo", "Result", "Attempts", "Warnings", "Stages"), 0)
        layout.addWidget(self.results, 1)
        layout.addLayout(_row(self.refresh, self.pause, self.resume, self.cancel, monitor.close_button))
        for button in monitor.findChildren(QPushButton):
            button.setAutoDefault(False)

    def _filter_results(self, value):
        self.result_filter = value
        for key, button in self.result_buttons.items():
            button.setChecked(key == value)
        if self._status:
            for row, image in enumerate(self._status.images):
                hidden = (value == "failed" and image.status != "failed") or (value == "warnings" and not image.warnings)
                self.results.setRowHidden(row, hidden)

    def _open_destination(self):
        destination = self._status.destination
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(destination))):
            message = f"Cannot open {destination}. Check that the folder is accessible."
            self.job_message.setText(message)
            self.application.diagnostics.emit("OUTPUT_FOLDER_OPEN_FAILED", message, component="desktop",
                                              level="WARNING", job_id=self._job_id, destination=destination)

    def _inspect_job_detail(self, title, details, icon=None):
        dialog = QDialog(self.monitor)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        if icon is not None:
            thumbnail = QLabel()
            thumbnail.setPixmap(icon.pixmap(self.images.iconSize()))
            layout.addWidget(thumbnail)
        text = QPlainTextEdit(readOnly=True)
        text.setPlainText(details)
        layout.addWidget(text)
        back = QPushButton("Back to batch")
        back.clicked.connect(dialog.accept)
        layout.addWidget(back)
        dialog.resize(500, 320)
        dialog.finished.connect(dialog.deleteLater)
        dialog.open()

    def inspect_job_operation(self, event):
        if self._status is None or self._status.job_id != event.job_id:
            return False
        for stage in self._status.stages:
            if stage.id == event.stage_id:
                descriptor = self.application.pipeline_service.registry.descriptor(stage.operation, stage.operation_version)
                details = [f"{descriptor.title} · v{descriptor.version}", f"Pipeline: {self._status.pipeline_name}",
                           f"Stage: {stage.id}", "Enabled" if stage.enabled else "Off"]
                values = dict(stage.parameters)
                details.extend(f"{param.label}: {values[param.name]}" for param in descriptor.editable_parameters
                               if param.name in values)
                self._inspect_job_detail("Batch operation", "\n".join(details))
                return True
        return False

    def _monitor_closed(self):
        if self.application.job_service.current_issues is not None and self._task is None:
            self._discard()
            if not self._closing:
                self._invalidate_plan()

    def _discard(self):
        self._submit("discard", self.application.job_service.discard_current)

    def _export_issues(self):
        filename, _ = QFileDialog.getSaveFileName(self.monitor, "Export warnings and errors",
            "Auto ReTouch batch report.txt", "Text files (*.txt)", options=QFileDialog.Option.DontConfirmOverwrite)
        if not filename:
            return
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(".txt")
        issues = self.application.job_service.current_issues
        self._report = (path, self._executor.submit(issues.export, path, self._status.images if self._status else ()))
        self.job_message.setText("Writing batch report…")
        self._update_controls()

    def _show_sources(self):
        self.source_text.setText("; ".join(map(str, self._sources)))
        self.source_text.setToolTip("\n".join(map(str, self._sources)))

    def _set_sources(self, paths):
        self._sources = tuple(map(Path, paths))
        self._show_sources()
        self._scan()

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose source folder")
        if path:
            self._set_sources((path,))

    def _choose_files(self):
        patterns = " ".join(f"*{suffix}" for suffix in SOURCE_EXTENSIONS)
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose source photos", filter=f"Photos ({patterns})")
        if paths:
            self._set_sources(paths)

    def _choose_destination(self):
        initial_folder = self.destination.text().strip()
        if not initial_folder and self._sources:
            source = self._sources[0]
            initial_folder = str(source.parent if source.is_file() else source)
        path = QFileDialog.getExistingDirectory(self, "Choose destination folder", initial_folder)
        if path:
            if path == self.destination.text():
                self._invalidate_plan()
            else:
                self.destination.setText(path)

    def _invalidate_index(self):
        self._index = None
        self.name_error.hide()
        self.images.set_index(None)
        self._invalidate_plan()

    def _invalidate_plan(self):
        self._plan = None
        self._plan_revision += 1
        can_check = (self._index is not None and not self._index.name_conflicts
                     and self.destination.text().strip() and self._selected_ids())
        self._space_check_after = monotonic() + 0.3 if can_check else None
        self.setup_message.clear()
        self._show_estimate()
        if can_check:
            self.space.setText(f"{self.space.text()}\nChecking paths and disk space…")
        self._update_controls()

    def _selected_ids(self):
        if self._index is None:
            return ()
        return tuple(image.image_id for row, image in enumerate(self._index.images)
                     if self.images.item(row).checkState() == Qt.CheckState.Checked)

    def _check_all(self, state):
        with QSignalBlocker(self.images):
            for row in range(self.images.count()):
                self.images.item(row).setCheckState(state)
        self._invalidate_plan()

    def _scan(self):
        if self._task is not None or self._run is not None:
            return
        if self._index is not None:
            self._scan_selection = None
            if self._sources == self._indexed_sources:
                states = {image.path: self.images.item(i).checkState() for i, image in enumerate(self._index.images)}
                current = self._current_image()
                self._scan_selection = states, current.path if current else None, self.images.verticalScrollBar().value()
        if self._sources != self._indexed_sources:
            self._scan_selection = None
        self._invalidate_index()
        if not self._sources:
            return
        self.setup_message.setText("Reading image headers…")
        self._submit("scan", partial(self.application.index_sources, self._sources, recursive=self.recursive.isChecked()))

    def _show_index(self, index):
        self._index = index
        self._indexed_sources = self._sources
        self.images.set_index(index)
        changes = ""
        if self._scan_selection is not None:
            states, current, scroll = self._scan_selection
            paths = {image.path for image in index.images}
            with QSignalBlocker(self.images):
                for row, image in enumerate(index.images):
                    if image.path in states:
                        self.images.item(row).setCheckState(states[image.path])
                    if image.path == current:
                        self.images.setCurrentRow(row)
                self.images.doItemsLayout()
                self.images.verticalScrollBar().setValue(scroll)
            changes = f"Rescan complete · {len(paths - states.keys())} added · {len(states.keys() - paths)} removed"
        self._scan_selection = None
        self.name_error.setPlainText(index.name_error)
        self.name_error.setVisible(bool(index.name_conflicts))
        self._invalidate_plan()
        messages = [changes]
        if not index.images:
            messages.append("No photos found. Choose another source or include subfolders.")
        elif any(image.issue for image in index.images):
            messages.append("Repair unreadable photos and rescan, or uncheck them to exclude them from this batch.")
        self.setup_message.setText("\n".join(filter(None, messages)))

    def _restore_preferences(self):
        try:
            recursive, destination, mode, fmt, settings = self.application.state.batch_preferences()
        except (OSError, RetouchingError) as exc:
            self.preferences_warning = f"Could not restore batch settings: {exc}"
            self.application.diagnostics.emit("BATCH_SETTINGS_RESTORE_FAILED", self.preferences_warning,
                component="desktop", level="WARNING", exc_info=True, state_path=self.application.state.path)
            return
        self.recursive.setChecked(recursive)
        self.destination.setText(destination)
        self.mode.setCurrentIndex(self.mode.findData(mode))
        self.format.setCurrentText(fmt)
        jpeg, jxl = settings["JPEG"], settings["JPEG XL"]
        self.quality.setValue(jpeg.quality)
        self.chroma.setCurrentText(jpeg.subsampling)
        self.progressive.setChecked(jpeg.progressive)
        self.jxl_quality.setValue(jxl.quality)
        self.jxl_lossless.setChecked(jxl.lossless)
        self.jxl_effort.setValue(jxl.effort)
        self.png_compression.setValue(settings["PNG"].compression)
        self.tiff_compression.setCurrentText(settings["TIFF"].compression)
        self.bmp_dpi.setValue(settings["BMP"].dpi)

    def _update_export_summary(self):
        settings = self._export_settings()
        self.export_toggle.setText(f"{self.mode.currentData().title()} · {settings.description()} · Export settings…")

    def _preferences_changed(self):
        self._update_export_summary()
        self._preferences_dirty = True
        self._preferences_timer.start()

    def _save_preferences(self):
        if not self._preferences_dirty:
            return
        self._preferences_timer.stop()
        try:
            self.application.state.update(batch={
                "recursive": self.recursive.isChecked(), "destination": self.destination.text().strip(),
                "mode": self.mode.currentData(), "format": self.format.currentText(),
                "options": {fmt: encode_settings(self._export_settings(fmt)) for fmt in FORMATS},
            })
        except (OSError, RetouchingError) as exc:
            self.preferences_warning = f"Could not save batch settings: {exc}"
            self.application.diagnostics.emit("BATCH_SETTINGS_SAVE_FAILED", self.preferences_warning,
                component="desktop", level="WARNING", exc_info=True, state_path=self.application.state.path)
        else:
            self._preferences_dirty = False
            self.preferences_warning = ""

    def _export_settings(self, fmt=None):
        match fmt or self.format.currentText():
            case "JPEG":
                return JpegSettings(self.quality.value(), self.chroma.currentText(), self.progressive.isChecked())
            case "JPEG XL":
                return JxlSettings(self.jxl_quality.value(), self.jxl_lossless.isChecked(), self.jxl_effort.value())
            case "PNG":
                return PngSettings(self.png_compression.value())
            case "TIFF":
                return TiffSettings(self.tiff_compression.currentText())
            case "BMP":
                return BmpSettings(self.bmp_dpi.value())

    def _plan_args(self):
        return (self._index, Path(self.destination.text().strip())), {
            "mode": self.mode.currentData(), "selected_ids": self._selected_ids(), "settings": self._export_settings(),
        }

    def _update_space_check(self):
        if self._space_check is not None:
            revision, future = self._space_check
            if not future.done():
                return
            self._space_check = None
            try:
                plan = future.result()
            except Exception as exc:
                if not isinstance(exc, RetouchingError):
                    self.application.diagnostics.emit("DESKTOP_BATCH_COMMAND_FAILED", "Cannot check batch disk space.",
                        component="desktop", action="preflight", level="ERROR", exc_info=True)
                if revision == self._plan_revision:
                    self._show_estimate()
                    code = getattr(exc, "code", "DESKTOP_BATCH_COMMAND_FAILED")
                    self.space.setText(f"{self.space.text()}\n{code}: {exc}")
            else:
                if revision == self._plan_revision:
                    self._show_plan(plan)
        if (self._space_check_after is not None and monotonic() >= self._space_check_after and not self._closing
                and self._task is None and self._run is None and self._stage_preview is None):
            self._space_check_after = None
            args, options = self._plan_args()
            future = self._executor.submit(self.application.batch_service.preflight, *args, **options, check_outputs=False)
            self._space_check = self._plan_revision, future

    def _show_estimate(self):
        selected = self._selected_ids()
        budget = None if self._index is None else self.application.batch_service.estimate(
            self._index, mode=self.mode.currentData(), selected_ids=selected, settings=self._export_settings())
        if budget is not None:
            self.space.setText(self._estimate_text(budget))
        elif selected:
            self.space.setText("Disk estimates need readable image headers for all included photos.")
        else:
            self.space.clear()

    def _estimate_text(self, budget):
        return f"Estimated temporary space {_size(budget.scratch_bytes)} · {self.format.currentText()} exports {_size(budget.output_bytes)}"

    def _show_plan(self, plan):
        self._plan = plan
        lines = [self._estimate_text(plan.budget)]
        for volume in plan.volumes:
            folders = ", ".join(map(str, volume.paths))
            lines.append(f"{_size(volume.required_bytes)} required · {_size(volume.available_bytes)} free on {folders}")
        if not plan.ready:
            lines.append("The full selection exceeds available space. Start processing checks space again after any existing files are skipped.")
        self.space.setText("\n".join(lines))

    def refresh_pipeline(self):
        status = self.application.pipeline_service.status
        enabled = ", ".join(item.title for item in status.stages if item.stage.enabled) or "none (export only)"
        skipped = ", ".join(item.title for item in status.stages if not item.stage.enabled) or "none"
        problem = "\nResolve the blocked operations before starting." if not status.executable else ""
        self.pipeline_summary.setText(f"{status.definition.name} · Enabled operations: {enabled} · Skipped: {skipped}{problem}")
        self._update_controls()

    def configuration_changed(self, previous):
        config = self.application.config
        self.refresh_pipeline()
        if previous.checkpoints != config.checkpoints and self._picture_window is not None:
            self._picture_window.close()
        if previous.temporary != config.temporary:
            self._invalidate_plan()

    def _current_image(self):
        selected = self.images.selectedItems()
        if self._index is not None and selected:
            return self._index.images[self.images.row(selected[0])]
        return None

    def show_diagnostic_photo(self, event):
        if self.monitor.isVisible() and self._status and self._status.job_id == event.job_id:
            for image in self._status.images:
                if image.image_id == event.image_id:
                    matches = self.images.findItems(image.source.name, Qt.MatchFlag.MatchExactly)
                    icon = next((item.icon() for item in matches if item.data(Qt.ItemDataRole.UserRole) == image.image_id), None)
                    details = [str(image.source), f"Output: {image.output}", f"Status: {image.status}",
                               f"Warnings: {image.warnings}", image.message or ""]
                    self._inspect_job_detail("Batch photo", "\n".join(details), icon)
                    return True
            return False
        if self._index is not None and not self.monitor.isVisible():
            for row, image in enumerate(self._index.images):
                if image.image_id == event.image_id:
                    self.images.setCurrentRow(row)
                    self.images.scrollToItem(self.images.item(row))
                    self.setup_scroll.ensureWidgetVisible(self.images)
                    return True
        if self._status is not None and self._status.job_id == event.job_id:
            for row, image in enumerate(self._status.images):
                if image.image_id == event.image_id:
                    if not self.monitor.isVisible():
                        self.monitor.open()
                    self.results.selectRow(row)
                    self.results.scrollToItem(self.results.item(row, 0))
                    return True
        return False

    def _view_original(self):
        if self._preview_available and self._picture_window is None:
            self._request_picture(self._index, self._current_image().image_id, process=False)

    def preview_stage(self, stage_id, through_stage):
        if not self._preview_available:
            return
        editor = self.application.pipeline_service
        selected = next((item for item in editor.status.stages if item.stage.id == stage_id), None)
        if selected is None or selected.issues:
            return
        stage, image = selected.stage, self._current_image()
        descriptor = editor.registry.descriptor(stage.operation, stage.operation_version)
        comparison = self.application.preview_service.stage_comparison(self._index, image.image_id, stage_id,
                                                                       through_stage=through_stage)

        def submit(parameters, approvals, mode):
            return self._executor.submit(comparison.render, uuid4().hex, parameters, resize_approvals=approvals,
                                         mode=mode)

        if self._picture_window is not None:
            self._picture_window.close()
        self._stage_preview = dialog = StagePreviewWindow(
            self.window(), stage, descriptor, image.path.name, through_stage=through_stage, submit=submit,
            neutral_settings=comparison.neutral_settings if descriptor.neutral_picker else None,
            save=partial(editor.set_parameters, stage_id, image_id=image.image_id))
        WindowSize(dialog, "stage_preview", self.application)
        dialog.finished.connect(comparison.clear)
        dialog.finished.connect(self._finish_stage_preview)
        dialog.finished.connect(dialog.deleteLater)
        self.pipeline_locked.emit(True)
        self._update_controls()
        dialog.open()
        dialog.start_preview()

    def _finish_stage_preview(self):
        self._stage_preview = None
        self.pipeline_locked.emit(False)
        self._update_controls()

    def _navigate_picture(self, offset):
        target = self._picture_row + offset
        if self._task is None and 0 <= target < len(self._viewable):
            self._navigating = True
            self._request_picture(self._picture_index, self._viewable[target],
                                  process=self._picture_window.processed)

    def _edit_preview_stage(self, stage_id):
        if self._task is None and self._picture_index is self._index:
            self.images.setCurrentRow(self._picture_grid_rows[self._picture_row])
            self.preview_stage(stage_id, True)

    def _request_picture(self, index, image_id, *, process, resize_approvals=()):
        request_id = uuid4().hex
        self._preview_target = index, image_id, request_id
        self._resize_approvals = resize_approvals
        on_progress = None
        if process:
            self.pipeline_locked.emit(True)
            self._picture_window.feedback.clear()
            if not resize_approvals:
                self._preview_started_at = monotonic()
            # The worker writes only to this request's mailbox, even if the window closes.
            self._preview_messages = deque(["Preparing preview…"], maxlen=1)
            on_progress = self._preview_messages.append
            self._update_preview_progress()
            self._picture_window.render_dialog.open()
        elif self._picture_window is not None:
            self._picture_window.feedback.setText("Opening photo…")
        else:
            self.setup_message.setText("Opening original…")
        self._submit("preview" if process else "original", partial(
            self.application.preview_service.picture, index, image_id, request_id, process=process,
            resize_approvals=resize_approvals, on_pipeline_progress=on_progress))

    def _update_preview_progress(self):
        elapsed = _duration(int(monotonic() - self._preview_started_at))
        self._picture_window.render_dialog.message.setText(f"{self._preview_messages[-1]}\nElapsed: {elapsed}")

    def _preview_still_current(self, action):
        if self._closing:
            return False
        if action == "preview" or self._navigating:
            return self._picture_window is not None
        index, image_id, _ = self._preview_target
        image = self._current_image()
        return self._index is index and image is not None and image.image_id == image_id

    def _show_picture(self, preview):
        if preview.request_id != self._preview_target[2]:
            return
        index, image_id, _ = self._preview_target
        if self._picture_window is None:
            self._picture_index = index
            self._picture_grid_rows = [row for row, img in enumerate(index.images) if img.header is not None]
            self._viewable = [index.images[row].image_id for row in self._picture_grid_rows]
        self._picture_id = image_id
        self._picture_row = self._viewable.index(image_id)
        if self._picture_window is not None:
            self._picture_window.display(preview)
            self._picture_window.feedback.clear()
            if self._navigating and index is self._index:
                self.images.setCurrentRow(self._picture_grid_rows[self._picture_row])
            return
        self._picture_window = window = PictureWindow(self.window(), preview)
        WindowSize(window, "photo_preview", self.application)
        window.run_pipeline.clicked.connect(lambda: self._request_picture(self._picture_index, self._picture_id, process=True))
        window.previous.clicked.connect(lambda: self._navigate_picture(-1))
        window.next.clicked.connect(lambda: self._navigate_picture(1))
        window.edit_stage.connect(self._edit_preview_stage)
        if not self.application.pipeline_service.status.executable:
            window.feedback.setText("Resolve blocked operations in the pipeline to preview its result.")
        window.finished.connect(self._finish_picture)
        window.finished.connect(window.deleteLater)
        window.open()

    def _finish_picture(self):
        self._picture_window = None
        self._picture_index = self._picture_id = None
        self._viewable = self._picture_grid_rows = ()

    def _create(self, *, resize_approvals=(), existing_decision=None):
        args, options = self._plan_args()
        self._resize_approvals = resize_approvals
        self._existing_decision = existing_decision
        editor = self.application.pipeline_service
        reviewed = editor.status.definition

        def freeze():
            job_id = self.application.job_service.create_current(*args, **options, resize_approvals=resize_approvals,
                                                         existing_decision=existing_decision)
            return job_id, editor.status.definition != reviewed

        self.pipeline_locked.emit(True)
        self._status = self._job_id = None
        self._filter_results("all")
        self.result_buttons["failed"].setText("Failed")
        self.result_buttons["warnings"].setText("With warnings")
        self._elapsed = None
        self._update_timing()
        self.job_heading.setText("Preparing job")
        self.job_message.setText("Validating the pipeline and saving the job snapshot…")
        self.job_counts.clear()
        self.job_metadata.clear()
        self.worker_warning.clear()
        self.results.setRowCount(0)
        self.job_progress.setRange(0, 0)
        self.job_progress.reset()
        self.job_progress.show()
        if not self.monitor.isVisible():
            self.monitor.open()
        self._submit("create", freeze)

    def _start(self):
        self.worker_warning.clear()
        self.job_message.setText("Starting the worker…")
        self._initial_work = self.job_progress.maximum() - self.job_progress.value() if self._status else None
        self._estimated_finish = None
        self._started_at = monotonic()
        self._update_timing()
        self._submit("start", partial(self.application.job_worker.start, self._job_id))

    def _refresh(self):
        self._poll_failed = False
        self._submit("refresh", partial(self.application.job_service.status, self._job_id))

    def _confirm_break(self):
        if not self.cancel.isEnabled():
            return
        answer = QMessageBox.question(self.monitor, "Break processing?",
            "Cancel the remaining work for this job? Completed exports will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._run is not None:
            self._request("cancel")
        elif self._status and self._status.status == "paused" and self._task is None:
            self._submit("break", partial(self.application.job_service.cancel_remaining, self._job_id))

    def _request(self, command):
        if self._run is None:
            return
        if not getattr(self._run, command)():
            self.job_message.setText("The worker did not accept the request. Waiting for its current status.")
            return
        self._request_sent = command
        boundary = "photo" if self._status and self._status.mode == "sequential" else "processing unit"
        action = "Pause" if command == "pause" else "Cancel"
        self.job_message.setText(f"{action} requested · waiting for the current {boundary} to finish.")
        self._update_controls()

    def _show_status(self, status):
        previous = self._status
        if self._job_id != status.job_id:
            self._elapsed = None
        self._status, self._job_id = status, status.job_id
        settings = status.settings
        skipped = ", ".join(f"{stage.stage_id} ({stage.reason})" for stage in status.skipped_stages)
        encoding = settings.description()
        details = f"{status.mode.title()} · {status.pipeline_name} · {encoding}\n{status.destination}"
        if skipped:
            details += f"\nSkipped operations: {skipped}"
        self.job_metadata.setText(details)

        images = status.images
        if previous is None or previous.images != images:
            self._show_results(images, previous, status)
            self._filter_results(self.result_filter)
        completed = sum(image.status == "completed" for image in images)
        failed = sum(image.status == "failed" for image in images)
        canceled = sum(image.status == "canceled" for image in images)
        warning_count = sum(image.warnings > 0 for image in images)
        skipped_images = sum(image.status == "skipped" for image in images)
        waiting = len(images) - completed - failed - canceled - skipped_images
        self.job_counts.setText(f"{completed} exported · {skipped_images} skipped · {canceled} canceled · {waiting} unfinished")
        self.result_buttons["failed"].setText(f"Failed ({failed})")
        self.result_buttons["warnings"].setText(f"With warnings ({warning_count})")
        units = len(status.stage_ids) + 1 if status.mode == "staged" else 1
        done = sum(units if image.status in ("completed", "failed", "canceled", "skipped")
                   else (image.completed_stages or 0) for image in images)
        total = len(images) * units
        if self._started_at is not None and done != self.job_progress.value():
            if self._initial_work is None:
                self._initial_work = total - skipped_images * units
            remaining = total - done
            processed = self._initial_work - remaining
            self._estimated_finish = None
            if processed > 0:
                now = monotonic()
                self._estimated_finish = now + (now - self._started_at) * remaining / processed
        self.job_progress.setRange(0, total)
        self.job_progress.setValue(done)
        label = "Work units accounted for" if status.mode == "staged" else "Photos accounted for"
        self.job_progress.setFormat(f"{label}  %v / %m")
        self.job_progress.show()

    def _stop_timing(self):
        self._elapsed = (self._elapsed or 0) + (monotonic() - self._started_at)
        self._started_at = None

    def _update_timing(self):
        elapsed = self._elapsed
        estimate = "—"
        if self._started_at is not None:
            now = monotonic()
            elapsed = (elapsed or 0) + (now - self._started_at)
            estimate = "Estimating…"
            if self._estimated_finish is not None and self._estimated_finish > now:
                estimate = _duration(ceil(self._estimated_finish - now))
        if self._status and self._status.status in ("completed", "completed_with_errors"):
            estimate = _duration(0)
        elif self._status and self._status.status == "canceled":
            estimate = "—"
        active = "—" if elapsed is None else _duration(int(elapsed))
        self.job_timing.setText(f"Elapsed: {active}    ·    Estimated remaining: {estimate}")

    def _show_results(self, images, previous, status):
        previous_images = previous.images if previous and previous.job_id == status.job_id else ()
        self.results.setRowCount(len(images))
        self.results.setColumnHidden(4, status.mode != "staged")
        for row, image in enumerate(images):
            if row < len(previous_images) and image == previous_images[row]:
                continue
            outcome = image.code or image.status.replace("_", " ").capitalize()
            stages = "" if image.completed_stages is None else f"{image.completed_stages}/{len(status.stage_ids)}"
            for column, value in enumerate((str(image.source), outcome, str(image.attempts), str(image.warnings), stages)):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setToolTip(f"{image.source}\nExport: {image.output}")
                elif column == 1 and image.message:
                    item.setToolTip(image.message)
                self.results.setItem(row, column, item)

    def _submit(self, action, command):
        self._task = (action, self._executor.submit(command))
        self._update_controls()

    def _finish_task(self):
        action, future = self._task
        self._task = None
        picture = action in ("original", "preview")
        current = not picture or self._preview_still_current(action)
        retrying = False
        feedback = self.setup_message if action in ("scan", "create", "discard") or picture else self.job_message
        if self._navigating and self._picture_window is not None:
            feedback = self._picture_window.feedback
        if action == "preview" and self._picture_window is not None:
            self._picture_window.render_dialog.accept()
            if current:
                self._picture_window.activateWindow()
            feedback = self._picture_window.feedback
        try:
            if action == "discard":
                self._status = self._job_id = None
                self.results.setRowCount(0)
                for label in (self.job_heading, self.job_counts, self.job_metadata, self.job_message, self.worker_warning):
                    label.clear()
            result = future.result()
        except Exception as exc:
            if action == "start":
                self._stop_timing()
            if not isinstance(exc, RetouchingError):
                self.application.diagnostics.emit("DESKTOP_BATCH_COMMAND_FAILED",
                    "Batch command failed. Check local diagnostics for the cause.", component="desktop",
                    level="ERROR", exc_info=True, action=action, job_id=self._job_id)
            code = getattr(exc, "code", "DESKTOP_BATCH_COMMAND_FAILED")
            feedback.setText(f"{code}: {exc}" if current else "")
            if isinstance(exc, ExistingOutputs) and action == "create" and not self._closing:
                decision = confirm_existing_outputs(self.monitor, exc)
                if decision is None:
                    feedback.setText("Processing stopped before the job started.")
                else:
                    retrying = True
                    self._create(resize_approvals=self._resize_approvals, existing_decision=decision)
            elif isinstance(exc, ResizeApprovalRequired) and current and not self._closing:
                prompt = str(exc) if action == "create" else f"{exc}\nAllow this for this preview?"
                if confirm_resize(self.monitor if action == "create" else self._picture_window, prompt):
                    retrying = True
                    approvals = (*self._resize_approvals, exc.stage_id)
                    if action == "create":
                        self._create(resize_approvals=approvals, existing_decision=self._existing_decision)
                    elif action == "preview":
                        index, image_id, _ = self._preview_target
                        self._request_picture(index, image_id, process=True, resize_approvals=approvals)
            if action == "poll":
                self._poll_failed = True
            if action == "break" and not self._closing:
                self._submit("poll", partial(self.application.job_service.status, self._job_id))
            if action == "create" and not retrying:
                if not isinstance(exc, (ResizeApprovalRequired, ExistingOutputs)):
                    self._plan = None
                    self._show_estimate()
                if self.application.job_service.current_issues is None:
                    self.monitor.done(QDialog.DialogCode.Rejected)
                else:
                    self.job_heading.setText("Batch could not start")
                    self.job_message.setText(feedback.text())
                    self.job_progress.hide()
        else:
            if action != "poll":
                feedback.clear()
            match action:
                case "scan":
                    self._show_index(result)
                case "original" | "preview":
                    if current:
                        self._show_picture(result)
                case "create":
                    self._plan = None
                    self._show_estimate()
                    self._job_id, pipeline_changed = result
                    if not self._closing:
                        if pipeline_changed:
                            self.job_message.setText("Pipeline availability changed during validation. Review the batch before starting it.")
                            self._submit("poll", partial(self.application.job_service.status, self._job_id))
                        else:
                            self._start()
                case "start":
                    self._run = result
                    self._poll_failed = False
                    self._request_sent = None
                    self.job_message.clear()
                    if self._closing:
                        self._request("cancel")
                case "refresh" | "poll" | "break":
                    self._show_status(result)
        finally:
            if picture and not retrying:
                self._navigating = False
            if action in ("create", "preview") and not retrying:
                self.pipeline_locked.emit(False)

    def _tick(self):
        if self._report is not None and self._report[1].done():
            path, report = self._report
            self._report = None
            try:
                count = report.result()
            except Exception as exc:
                issues = self.application.job_service.current_issues
                self.application.diagnostics.emit("JOB_REPORT_FAILED", "Could not save the batch report.", level="ERROR",
                    component="desktop", job_id=issues.job_id, report_path=path, exc_info=True)
                self.job_message.setText(f"Cannot save report. Choose a new .txt file in a writable folder. {exc}")
            else:
                warning = self.application.job_service.current_issues.failure
                self.job_message.setText(f"Saved {count} warnings and errors to {path}" + (f"\n{warning}" if warning else ""))
        self._update_space_check()
        if self._task and self._task[1].done():
            self._finish_task()
        elif self._task and self._task[0] == "preview" and self._picture_window is not None:
            self._update_preview_progress()
        if self._task is None and self._run is not None:
            outcome = self._run.poll()
            if outcome is not None:
                self._stop_timing()
                self._run = None
                self._request_sent = None
                messages = []
                if outcome.error_code:
                    messages.append(f"{outcome.error_code}: {outcome.message}")
                if outcome.diagnostics_incomplete:
                    messages.append("Worker diagnostics are incomplete. Review the batch report and local logs.")
                self.worker_warning.setText("\n".join(messages))
                self.job_message.clear()
                if not self._closing:
                    self._submit("poll", partial(self.application.job_service.status, self._job_id))
            elif not self._closing and not self._poll_failed:
                self._submit("poll", partial(self.application.job_service.status, self._job_id))
        self._update_timing()
        self._update_controls()
        if self._closing and self._task is None and self._run is None and self._space_check is None and self.images.shutdown():
            self.ready_to_close.emit()

    def _update_controls(self):
        running = self._run is not None and self._run.poll() is None
        idle = not self._closing and self._stage_preview is None and self._task is None and self._run is None
        self.settings_available.emit(not self._closing and self._stage_preview is None
                                     and (self._task is None or self._task[0] == "poll"))
        selected = len(self._selected_ids())
        self.folders.setEnabled(idle)
        self.export_toggle.setEnabled(idle)
        self.export.setEnabled(idle)
        choosing_preview = self._task is not None and self._task[0] in ("original", "preview")
        self.images.setEnabled(idle or (not self._closing and choosing_preview))
        self.check_all.setEnabled(self.images.isEnabled() and selected < self.images.count())
        self.uncheck_all.setEnabled(self.images.isEnabled() and selected > 0)
        self.scan.setEnabled(idle and bool(self._sources))
        executable = self.application.pipeline_service.status.executable
        self.start.setEnabled(idle and self._plan is not None and executable)
        export_only = not any(stage.stage.enabled for stage in self.application.pipeline_service.status.stages)
        self.choose_preset.setVisible(export_only)
        self.choose_preset.setEnabled(idle and self.pipeline.presets.isEnabled())
        if not selected:
            readiness = "Choose photos to include"
        elif self._index.name_conflicts:
            readiness = "Resolve duplicate names and rescan"
        elif not self.destination.text().strip():
            readiness = "Choose a destination"
        elif not executable:
            readiness = "Resolve blocked operations"
        elif self._plan is None:
            readiness = "Checking batch…" if self._space_check_after or self._space_check else "Review batch setup"
        else:
            readiness = "Ready" if self._plan.ready else "Review available disk space"
        intent = " · Export only · 0 enabled stages" if export_only else ""
        self.readiness.setText(f"{selected} included · {readiness}{intent}")
        image = self._current_image()
        can_view = idle and image is not None and image.header is not None
        if self._picture_window is not None:
            window = self._picture_window
            window.run_pipeline.setEnabled(idle and executable)
            navigable = idle and (executable or not window.processed)
            window.previous.setEnabled(navigable and self._picture_row > 0)
            window.next.setEnabled(navigable and self._picture_row + 1 < len(self._viewable))
            window.position.setText(f"{self._picture_row + 1} of {len(self._viewable)}")
        if can_view != self._preview_available:
            self._preview_available = can_view
            self.operation_preview_available.emit(can_view)
        if self._index is None:
            self.source_summary.setText("Choose a folder or photo files." if not self._sources else "No photos indexed.")
        else:
            total = len(self._index.images)
            unreadable = sum(image.issue is not None for image in self._index.images)
            self.source_summary.setText(f"{total} photos found · {selected} included · {total - selected} excluded · {unreadable} unreadable")

        creating = self._task is not None and self._task[0] in ("create", "discard")
        self.export_issues.setEnabled(self.application.job_service.current_issues is not None and not creating
                                      and not self._closing and self._report is None)
        self.open_destination.setVisible(bool(self._status and self._status.terminal and self._status.destination))
        self.job_diagnostics.setEnabled(not self._closing and bool(self._job_id))
        self.refresh.setVisible(idle or self._poll_failed)
        self.refresh.setEnabled(bool(self._job_id) and (idle or (running and self._poll_failed and self._task is None)))
        self.resume.setEnabled(idle and bool(self._job_id) and (self._status is None or not self._status.terminal))
        queued = idle and self._status is not None and self._status.status == "queued"
        self.resume.setText("Start batch" if queued else "Continue")
        self.pause.setEnabled(running and not self._closing and self._request_sent is None)
        paused = idle and self._status is not None and self._status.status == "paused"
        self.cancel.setEnabled(paused or self.pause.isEnabled())
        self.monitor.close_button.setEnabled(idle and self._report is None)
        self.monitor.close_button.setText("Discard batch" if self._status and not self._status.terminal else "Close")
        if self._job_id is None:
            return
        if running:
            state = "Running" if self._status and self._status.status == "running" else "Worker active"
        elif self._status and self._status.status == "running":
            state = "Interrupted · continue or close the batch"
        elif self._status and self._status.status == "completed" and any(i.warnings for i in self._status.images):
            state = "Completed with warnings"
        else:
            state = self._status.status.replace("_", " ").capitalize() if self._status else "Batch"
        self.job_heading.setText(f"{state}  ·  {self._job_id}")

    @property
    def closing(self):
        return self._closing

    def request_close(self):
        """Stop the worker at a safe boundary and release the current batch."""
        if self._stage_preview is not None and not self._stage_preview.close():
            return False
        self._closing = True
        self._save_preferences()
        thumbnails_stopped = self.images.shutdown()
        if self._run is not None:
            if self._request_sent is None:
                self._request("cancel")
        if (self._task is None and self._run is None and self._report is None
                and self.application.job_service.current_issues is not None):
            self._discard()
        if (self._task is not None or self._run is not None or self._report is not None
                or self._space_check is not None or not thumbnails_stopped):
            self._update_controls()
            return False
        if self._picture_window is not None:
            self._picture_window.close()
        if self.monitor.isVisible():
            self.monitor.done(QDialog.DialogCode.Rejected)
        self._timer.stop()
        self._executor.shutdown(wait=False)
        return True
