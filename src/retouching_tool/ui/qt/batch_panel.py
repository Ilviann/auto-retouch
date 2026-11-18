"""Qt controls for preparing a batch and monitoring its saved job."""

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
                              QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLayout, QLineEdit,
                              QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget,
                              QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from ...errors import RetouchingError
from ...imaging.jpeg import JpegSettings
from .photo_grid import PhotoGrid
from .picture_window import PictureWindow
from .stage_preview_window import StagePreviewWindow


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
        self._plan = None
        self._job_id = None
        self._status = None
        self._run = None
        self._request_sent = None
        self._poll_failed = False
        self._closing = False
        self._picture_window = None
        self._stage_preview = None
        self._preview_target = None
        self._preview_available = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-batch")
        self._task = None

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
        self.source_text = QLineEdit(readOnly=True, placeholderText="Select a folder or JPEG files")
        self.source_text.setObjectName("batchSources")
        self._show_sources()
        form.addRow("Sources", _row(self.source_text,
                    _button("Choose folder…", "chooseSourceFolder", self._choose_folder),
                    _button("Choose JPEGs…", "chooseSourceFiles", self._choose_files)))
        self.recursive = QCheckBox("Include subfolders")
        self.recursive.setObjectName("batchRecursive")
        self.recursive.toggled.connect(self._scan)
        self.scan = _button("Scan sources", "scanSources", self._scan)
        scan_row = _row(self.recursive)
        scan_row.addStretch()
        scan_row.addWidget(self.scan)
        form.addRow("", scan_row)
        self.destination = QLineEdit(placeholderText="Select an existing destination folder")
        self.destination.setObjectName("batchDestination")
        self.destination.textChanged.connect(self._invalidate_plan)
        form.addRow("Destination", _row(self.destination,
                    _button("Browse…", "chooseDestination", self._choose_destination)))
        return self.folders

    def _build_setup(self):
        setup = QWidget()
        layout = QVBoxLayout(setup)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.source_summary = _label("batchSourceSummary")
        self.original = _button("View original", "viewOriginal", self._view_original)
        self.preview = _button("Preview pipeline", "previewPipeline", self._preview_pipeline)
        preview_row = _row(self.source_summary, self.original, self.preview)
        preview_row.setStretch(0, 1)
        layout.addLayout(preview_row)
        self.images = PhotoGrid(self.application.preview_service)
        self.images.itemChanged.connect(self._invalidate_plan)
        self.images.itemSelectionChanged.connect(self._update_controls)
        self.images.itemDoubleClicked.connect(self._view_original)
        layout.addWidget(self.images, 1)

        self.export = QGroupBox("Batch and JPEG settings")
        form = QFormLayout(self.export)
        self.mode = QComboBox()
        self.mode.setObjectName("batchMode")
        self.mode.addItem("Sequential · one photo at a time", "sequential")
        self.mode.addItem("Staged · one operation at a time", "staged")
        self.mode.currentIndexChanged.connect(self._invalidate_plan)
        form.addRow("Schedule", self.mode)
        self.quality = QSpinBox(minimum=1, maximum=95)
        self.quality.setObjectName("jpegQuality")
        self.chroma = QComboBox()
        self.chroma.setObjectName("jpegChroma")
        self.chroma.addItems(("4:4:4", "4:2:2", "4:2:0"))
        self.progressive = QCheckBox("Progressive")
        self.progressive.setObjectName("jpegProgressive")
        defaults = JpegSettings()
        self.quality.setValue(defaults.quality)
        self.chroma.setCurrentText(defaults.subsampling)
        self.progressive.setChecked(defaults.progressive)
        form.addRow("JPEG quality and chroma", _row(self.quality, self.chroma, self.progressive))
        layout.addWidget(self.export)

        self.pipeline_summary = _label("batchPipelineSummary")
        self.space = _label("batchSpace")
        self.setup_message = _label("batchSetupMessage")
        for label in (self.pipeline_summary, self.space, self.setup_message):
            layout.addWidget(label)
        self.check_space = _button("Check disk space", "checkBatchSpace", self._check_space)
        self.start = _button("Start batch", "startBatch", self._create)
        self.job_details = QToolButton(text="Job progress and history", checkable=True, objectName="jobDetails")
        layout.addLayout(_row(self.check_space, self.start, self.job_details))
        self._build_monitor()
        layout.addWidget(self.monitor)
        self.job_details.toggled.connect(self._toggle_monitor)
        self.monitor.hide()
        return setup

    def _toggle_monitor(self, visible):
        self.monitor.setVisible(visible)
        if visible:
            self.monitor.parentWidget().layout().activate()
            self.setup_scroll.ensureWidgetVisible(self.history)

    def _build_monitor(self):
        monitor = QWidget()
        layout = QVBoxLayout(monitor)
        self.history = _button("Open saved job…", "openSavedJob", self._open_history)
        self.refresh = _button("Refresh", "refreshJob", self._refresh)
        self.resume = _button("Resume", "resumeJob", self._start)
        self.pause = _button("Pause", "pauseJob", partial(self._request, "pause"))
        self.cancel = _button("Cancel remaining", "cancelJob", partial(self._request, "cancel"))
        layout.addLayout(_row(self.history, self.refresh, self.resume, self.pause, self.cancel))
        self.job_heading = _label("jobHeading")
        self.job_heading.setText("No job selected.")
        self.job_diagnostics = _button("Job diagnostics…", "openJobDiagnostics",
                                       lambda: self.diagnostics_requested.emit(self._job_id))
        heading = _row(self.job_heading, self.job_diagnostics)
        heading.setStretch(0, 1)
        layout.addLayout(heading)
        self.job_metadata = _label("jobMetadata")
        self.job_counts = _label("jobCounts")
        self.worker_warning = _label("jobWorkerWarning")
        self.job_message = _label("jobMessage")
        self.job_progress = QProgressBar()
        self.job_progress.setObjectName("jobProgress")
        self.job_progress.hide()
        for widget in (self.job_metadata, self.job_progress, self.job_counts,
                       self.worker_warning, self.job_message):
            layout.addWidget(widget)
        self.results = _table("jobImages", ("Source JPEG", "Result", "Attempts", "Warnings", "Stages"), 0)
        layout.addWidget(self.results, 1)
        self.monitor = monitor
        self.results.setMaximumHeight(185)

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
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose source JPEGs", filter="JPEG (*.jpg *.jpeg)")
        if paths:
            self._set_sources(paths)

    def _choose_destination(self):
        path = QFileDialog.getExistingDirectory(self, "Choose destination folder", self.destination.text())
        if path:
            self.destination.setText(path)

    def _invalidate_index(self):
        self._index = None
        self.images.set_index(None)
        self._invalidate_plan()

    def _invalidate_plan(self):
        self._plan = None
        self.setup_message.clear()
        self._show_estimate()
        self._update_controls()

    def _selected_ids(self):
        if self._index is None:
            return ()
        return tuple(image.image_id for row, image in enumerate(self._index.images)
                     if self.images.item(row).checkState() == Qt.CheckState.Checked)

    def _scan(self):
        if self._task is not None or self._run is not None:
            return
        self._invalidate_index()
        if not self._sources:
            return
        self.setup_message.setText("Reading JPEG headers…")
        self._submit("scan", partial(self.application.index_sources, self._sources, recursive=self.recursive.isChecked()))

    def _show_index(self, index):
        self._index = index
        self.images.set_index(index)
        self._show_estimate()
        if not index.images:
            self.setup_message.setText("No JPEGs found. Choose another source or include subfolders.")
        elif any(image.issue for image in index.images):
            self.setup_message.setText("Repair unreadable JPEGs and rescan, or uncheck them to exclude them from this batch.")

    def _plan_args(self):
        return (self._index, Path(self.destination.text().strip())), {
            "mode": self.mode.currentData(), "selected_ids": self._selected_ids(),
        }

    def _check_space(self):
        self._invalidate_plan()
        self.setup_message.setText("Checking paths and disk space…")
        args, options = self._plan_args()
        self._submit("preflight", partial(self.application.batch_service.preflight, *args, **options))

    def _show_estimate(self):
        selected = self._selected_ids()
        budget = None if self._index is None else self.application.batch_service.estimate(
            self._index, mode=self.mode.currentData(), selected_ids=selected)
        if budget is not None:
            self.space.setText(self._estimate_text(budget))
        elif selected:
            self.space.setText("Disk estimates need readable JPEG headers for all included photos.")
        else:
            self.space.clear()

    @staticmethod
    def _estimate_text(budget):
        return f"Estimated temporary space {_size(budget.scratch_bytes)} · JPEG exports {_size(budget.output_bytes)}"

    def _show_plan(self, plan):
        self._plan = plan
        lines = [self._estimate_text(plan.budget)]
        for volume in plan.volumes:
            folders = ", ".join(map(str, volume.paths))
            lines.append(f"{_size(volume.required_bytes)} required · {_size(volume.available_bytes)} free on {folders}")
        self.space.setText("\n".join(lines))
        if not plan.ready:
            self.setup_message.setText("Free disk space or choose a different destination, then check again.")

    def refresh_pipeline(self):
        status = self.application.pipeline_service.status
        enabled = ", ".join(item.title for item in status.stages if item.stage.enabled) or "none (JPEG export only)"
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
            if self.check_space.isEnabled():
                self._check_space()

    def _current_image(self):
        selected = self.images.selectedItems()
        if self._index is not None and selected:
            return self._index.images[self.images.row(selected[0])]
        return None

    def show_diagnostic_photo(self, event):
        if self._index is not None:
            for row, image in enumerate(self._index.images):
                if image.image_id == event.image_id:
                    self.images.setCurrentRow(row)
                    self.images.scrollToItem(self.images.item(row))
                    self.setup_scroll.ensureWidgetVisible(self.images)
                    return True
        if self._status is not None and self._status.job_id == event.job_id:
            for row, image in enumerate(self._status.images):
                if image.image_id == event.image_id:
                    self.job_details.setChecked(True)
                    self.results.selectRow(row)
                    self.results.scrollToItem(self.results.item(row, 0))
                    self.setup_scroll.ensureWidgetVisible(self.results)
                    return True
        return False

    def _view_original(self):
        if self.original.isEnabled():
            self._request_picture(process=False)

    def _preview_pipeline(self):
        if self.preview.isEnabled():
            self._request_picture(process=True)

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

        def submit(parameters):
            return self._executor.submit(comparison.render, uuid4().hex, parameters)

        if self._picture_window is not None:
            self._picture_window.close()
        self._stage_preview = dialog = StagePreviewWindow(
            self.window(), stage, descriptor, image.path.name, through_stage=through_stage, submit=submit,
            save=partial(editor.set_parameters, stage_id, image_id=image.image_id))
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

    def _request_picture(self, *, process):
        image = self._current_image()
        request_id = uuid4().hex
        self._preview_target = self._index, image.image_id, request_id
        if process:
            self.pipeline_locked.emit(True)
        self.setup_message.setText("Processing preview…" if process else "Opening original…")
        self._submit("preview" if process else "original", partial(
            self.application.preview_service.picture, self._index, image.image_id, request_id, process=process))

    def _preview_still_current(self):
        index, image_id, _ = self._preview_target
        image = self._current_image()
        return not self._closing and self._index is index and image is not None and image.image_id == image_id

    def _show_picture(self, preview):
        if preview.request_id != self._preview_target[2]:
            return
        if self._picture_window is not None:
            self._picture_window.close()
        self._picture_window = window = PictureWindow(self.window(), preview)
        window.finished.connect(self._finish_picture)
        window.finished.connect(window.deleteLater)
        window.open()

    def _finish_picture(self):
        self._picture_window = None

    def _create(self):
        args, options = self._plan_args()
        settings = JpegSettings(self.quality.value(), self.chroma.currentText(), self.progressive.isChecked())
        editor = self.application.pipeline_service
        reviewed = editor.status.definition

        def freeze():
            job_id = self.application.job_service.create(*args, **options, settings=settings)
            return job_id, editor.status.definition != reviewed

        self.pipeline_locked.emit(True)
        self.setup_message.setText("Validating the pipeline and saving the job snapshot…")
        self._submit("create", freeze)

    def _start(self):
        self.worker_warning.clear()
        self.job_message.setText("Starting the worker…")
        self._submit("start", partial(self.application.job_worker.start, self._job_id))

    def _open_history(self):
        self.job_message.setText("Looking for saved jobs…")
        self._submit("history", self.application.job_service.saved_jobs)

    def _choose_saved_job(self, jobs):
        if not jobs:
            self.job_message.setText("No saved jobs found.")
            return
        labels = [f"{job.modified.astimezone():%Y-%m-%d %H:%M:%S}  ·  {job.job_id}" for job in jobs]
        label, accepted = QInputDialog.getItem(self, "Open saved job", "Last updated · Job ID", labels, editable=False)
        if accepted:
            self._submit("open", partial(self.application.job_service.status, jobs[labels.index(label)].job_id))

    def _refresh(self):
        self._poll_failed = False
        self._submit("refresh", partial(self.application.job_service.status, self._job_id))

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
        self._status, self._job_id = status, status.job_id
        settings = status.settings
        skipped = ", ".join(f"{stage.stage_id} ({stage.reason})" for stage in status.skipped_stages)
        encoding = f"JPEG {settings.quality}, {settings.subsampling}"
        if settings.progressive:
            encoding += ", progressive"
        details = f"{status.mode.title()} · {status.pipeline_name} · {encoding}\n{status.destination}"
        if skipped:
            details += f"\nSkipped operations: {skipped}"
        self.job_metadata.setText(details)

        images = status.images
        if previous is None or previous.images != images:
            self._show_results(images, previous, status)
        completed = sum(image.status == "completed" for image in images)
        failed = sum(image.status == "failed" for image in images)
        canceled = sum(image.status == "canceled" for image in images)
        warning_count = sum(image.warnings > 0 for image in images)
        waiting = len(images) - completed - failed - canceled
        self.job_counts.setText(f"{completed} exported · {failed} failed · {canceled} canceled · "
                                f"{waiting} unfinished · {warning_count} with warnings")
        units = len(status.stage_ids) + 1 if status.mode == "staged" else 1
        done = sum(units if image.status in ("completed", "failed", "canceled")
                   else (image.completed_stages or 0) for image in images)
        self.job_progress.setRange(0, len(images) * units)
        self.job_progress.setValue(done)
        label = "Work units accounted for" if status.mode == "staged" else "Photos accounted for"
        self.job_progress.setFormat(f"{label}  %v / %m")
        self.job_progress.show()

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
        current = not picture or self._preview_still_current()
        feedback = self.setup_message if action in ("scan", "preflight", "create") or picture else self.job_message
        try:
            result = future.result()
        except Exception as exc:
            if not isinstance(exc, RetouchingError):
                self.application.diagnostics.emit("DESKTOP_BATCH_COMMAND_FAILED",
                    "Batch command failed. Check local diagnostics for the cause.", component="desktop",
                    level="ERROR", exc_info=True, action=action, job_id=self._job_id)
            code = getattr(exc, "code", "DESKTOP_BATCH_COMMAND_FAILED")
            feedback.setText(f"{code}: {exc}" if current else "")
            if action == "poll":
                self._poll_failed = True
        else:
            if action != "poll":
                feedback.clear()
            match action:
                case "scan":
                    self._show_index(result)
                case "preflight":
                    self._show_plan(result)
                case "original" | "preview":
                    if current:
                        self._show_picture(result)
                case "create":
                    self._job_id, pipeline_changed = result
                    self._status = None
                    self.results.setRowCount(0)
                    self.job_counts.clear()
                    self.job_metadata.clear()
                    self.job_progress.hide()
                    self.worker_warning.clear()
                    if not self._closing:
                        self.job_details.setChecked(True)
                        if pipeline_changed:
                            self.job_message.setText("Pipeline availability changed during validation. Review this saved job before starting it.")
                            self._submit("poll", partial(self.application.job_service.status, self._job_id))
                        else:
                            self._start()
                case "start":
                    self._run = result
                    self._poll_failed = False
                    self._request_sent = None
                    if self._closing:
                        self._request("pause")
                case "history":
                    if not self._closing:
                        self._choose_saved_job(result)
                case "open":
                    self.worker_warning.clear()
                    self._show_status(result)
                case "refresh" | "poll":
                    self._show_status(result)
        finally:
            if action == "create":
                self._plan = None
                self._show_estimate()
            if action in ("create", "preview"):
                self.pipeline_locked.emit(False)

    def _tick(self):
        if self._task and self._task[1].done():
            self._finish_task()
        if self._task is None and self._run is not None:
            outcome = self._run.poll()
            if outcome is not None:
                self._run = None
                self._request_sent = None
                messages = []
                if outcome.error_code:
                    messages.append(f"{outcome.error_code}: {outcome.message}")
                if outcome.diagnostics_incomplete:
                    messages.append("Worker diagnostics are incomplete. Review the local logs and saved job status.")
                self.worker_warning.setText("\n".join(messages))
                self.job_message.clear()
                if not self._closing:
                    self._submit("poll", partial(self.application.job_service.status, self._job_id))
            elif not self._closing and not self._poll_failed:
                self._submit("poll", partial(self.application.job_service.status, self._job_id))
        self._update_controls()
        if self._closing and self._task is None and self._run is None and self.images.shutdown():
            self.ready_to_close.emit()

    def _update_controls(self):
        running = self._run is not None and self._run.poll() is None
        idle = not self._closing and self._stage_preview is None and self._task is None and self._run is None
        self.settings_available.emit(not self._closing and self._stage_preview is None
                                     and (self._task is None or self._task[0] == "poll"))
        selected = len(self._selected_ids())
        self.folders.setEnabled(idle)
        self.export.setEnabled(idle)
        choosing_preview = self._task is not None and self._task[0] in ("original", "preview")
        self.images.setEnabled(idle or (not self._closing and choosing_preview))
        self.scan.setEnabled(idle and bool(self._sources))
        self.check_space.setEnabled(idle and selected > 0 and bool(self.destination.text().strip()))
        executable = self.application.pipeline_service.status.executable
        self.start.setEnabled(idle and self._plan is not None and self._plan.ready and executable)
        image = self._current_image()
        can_view = idle and image is not None and image.header is not None
        self.original.setEnabled(can_view)
        self.preview.setEnabled(can_view and executable)
        if can_view != self._preview_available:
            self._preview_available = can_view
            self.operation_preview_available.emit(can_view)
        if self._index is None:
            self.source_summary.setText("Choose a folder or JPEG files." if not self._sources else "No photos indexed.")
        else:
            total = len(self._index.images)
            unreadable = sum(image.issue is not None for image in self._index.images)
            self.source_summary.setText(f"{total} JPEGs found · {selected} included · {total - selected} excluded · {unreadable} unreadable")

        self.history.setEnabled(idle)
        self.job_diagnostics.setEnabled(not self._closing and bool(self._job_id))
        self.refresh.setEnabled(idle and bool(self._job_id))
        self.resume.setEnabled(idle and bool(self._job_id) and (self._status is None or not self._status.terminal))
        self.resume.setText("Start saved job" if self._status is None or self._status.status == "queued" else "Resume")
        self.pause.setEnabled(running and not self._closing and self._request_sent is None)
        self.cancel.setEnabled(running and not self._closing and self._request_sent != "cancel")
        if self._job_id is None:
            return
        if running:
            state = "Running" if self._status and self._status.status == "running" else "Worker active"
        elif self._status and self._status.status == "running":
            state = "Saved as running · resume when the worker has stopped"
        elif self._status and self._status.status == "completed" and any(i.warnings for i in self._status.images):
            state = "Completed with warnings"
        else:
            state = self._status.status.replace("_", " ").capitalize() if self._status else "Saved job"
        self.job_heading.setText(f"{state}  ·  {self._job_id}")

    def request_close(self):
        """Finish pending desktop commands and pause the worker before exit."""
        if self._stage_preview is not None and not self._stage_preview.close():
            return False
        self._closing = True
        thumbnails_stopped = self.images.shutdown()
        if self._run is not None:
            self.job_details.setChecked(True)
            if self._request_sent is None:
                self._request("pause")
        if self._task is not None or self._run is not None or not thumbnails_stopped:
            self._update_controls()
            return False
        if self._picture_window is not None:
            self._picture_window.close()
        self._timer.stop()
        self._executor.shutdown(wait=False)
        return True
