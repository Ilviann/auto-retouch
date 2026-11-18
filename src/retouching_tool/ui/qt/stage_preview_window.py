"""Modal stage comparison, including local draft settings and preview scheduling."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from ...errors import ResizeApprovalRequired, RetouchingError
from .photo_grid import pixmap
from .picture_window import FittedPicture, statistics_text
from .stage_card import ScalingControl, parameter_control


def confirm_resize(parent, message):
    buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    return QMessageBox.question(parent, "Allow temporary resizing?", message, buttons,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


class StagePreviewWindow(QDialog):
    def __init__(self, parent, stage, descriptor, filename, *, through_stage, submit, save):
        super().__init__(parent, objectName="stagePreviewWindow")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle(f"{descriptor.title} · {filename}")
        self.resize(1050, 720)
        self._submit, self._save = submit, save
        self._original = dict(stage.parameters)
        self._draft = self._original.copy()
        self._requested = self._original.copy()
        self._submitted = None
        self._future = None
        self._resize_approvals = ()
        self._failed = False
        self._close_requested = False
        self._live_timer = QTimer(self, interval=120, singleShot=True)
        self._live_timer.timeout.connect(self.start_preview)
        self._poll_timer = QTimer(self, interval=35)
        self._poll_timer.timeout.connect(self._finish_preview)

        layout = QVBoxLayout(self)
        pictures = QHBoxLayout()
        self.before = FittedPicture("stagePreviewBefore")
        self.after = FittedPicture("stagePreviewAfter")
        before_caption = QLabel("Before this stage" if through_stage else "Source photo",
                                alignment=Qt.AlignmentFlag.AlignCenter)
        self.after_caption = QLabel("After this stage", alignment=Qt.AlignmentFlag.AlignCenter)
        for picture, caption in ((self.before, before_caption), (self.after, self.after_caption)):
            column = QVBoxLayout()
            column.addWidget(caption)
            column.addWidget(picture, 1)
            pictures.addLayout(column, 1)
        layout.addLayout(pictures, 1)
        self.statistics = QLabel(objectName="previewStatistics", wordWrap=True)
        self.statistics.hide()
        layout.addWidget(self.statistics)

        if descriptor.editable_parameters:
            settings = QGroupBox("Stage settings")
            form = QFormLayout(settings)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
            for parameter in descriptor.editable_parameters:
                control = parameter_control(parameter)
                control.setValue(self._original[parameter.name])
                control.setMinimumWidth(110)
                hint = ("Updates the preview automatically." if parameter.preview_on_change
                        else "Select Preview changes to update the preview.")
                control.setToolTip(hint)
                signal = control.currentIndexChanged if isinstance(control, ScalingControl) else control.valueChanged
                signal.connect(lambda _, p=parameter, widget=control, initial=control.value():
                               self._change(p, widget.value(), initial))
                form.addRow(parameter.label, control)
            layout.addWidget(settings)

        self.feedback = QLabel(objectName="stagePreviewFeedback", wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        layout.addWidget(self.feedback)
        actions = QHBoxLayout()
        self.pending = QLabel("Settings are waiting for Preview changes.", objectName="stagePreviewPending")
        actions.addWidget(self.pending)
        actions.addStretch()
        self._has_manual_settings = any(not p.preview_on_change for p in descriptor.editable_parameters)
        self.apply_preview = QPushButton("Preview changes", objectName="previewChanges", autoDefault=False)
        self.apply_preview.clicked.connect(self._preview_changes)
        actions.addWidget(self.apply_preview)
        self.close_button = QPushButton("Close", objectName="closeStagePreview", autoDefault=False)
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self._update_actions()
        self.finished.connect(self._discard_images)

    def _discard_images(self):
        self.before.show_pixels(None)
        self.after.show_pixels(None)

    def _change(self, parameter, value, initial):
        name = parameter.name
        self._draft[name] = self._original[name] if value == initial else value
        if parameter.preview_on_change:
            self._requested[name] = self._draft[name]
            if not self._close_requested:
                self._live_timer.start()
        self._update_actions()

    def _preview_changes(self):
        self._requested = self._draft.copy()
        if self._failed:
            self._submitted = None
        self._live_timer.stop()
        self.start_preview()
        self._update_actions()

    def start_preview(self):
        if self._close_requested or self._future is not None or self._requested == self._submitted:
            return
        self._submitted = self._requested.copy()
        self._future = self._submit(self._submitted, self._resize_approvals)
        self._failed = False
        self.feedback.setText("Rendering preview…")
        self._poll_timer.start()
        self._update_actions()

    def _finish_preview(self):
        if not self._future.done():
            return
        future, self._future = self._future, None
        self._poll_timer.stop()
        current = self._requested == self._submitted
        try:
            preview = future.result()
        except RetouchingError as exc:
            self._failed = current
            if current:
                self.feedback.setText(f"{exc.code}: {exc}")
                if (isinstance(exc, ResizeApprovalRequired) and not self._close_requested
                        and confirm_resize(self, f"{exc}\nAllow this for this stage preview?")):
                    self._resize_approvals += (exc.stage_id,)
                    self._submitted = None
        else:
            if current:
                self.before.show_pixels(pixmap(preview.original))
                self.after.show_pixels(pixmap(preview.result))
                self.after_caption.setText(preview.result_caption or "After this stage")
                measurements = statistics_text(preview.context)
                self.statistics.setText(measurements)
                self.statistics.setVisible(bool(measurements))
                self.feedback.clear()
        self._update_actions()
        if self._close_requested:
            self._close_requested = False
            self.close_button.setEnabled(True)
            self.reject()
        elif not self._live_timer.isActive():
            self.start_preview()

    def _update_actions(self):
        unapplied = self._draft != self._requested
        self.pending.setVisible(unapplied)
        self.apply_preview.setVisible(self._has_manual_settings or self._failed)
        self.apply_preview.setEnabled(not self._close_requested and (unapplied or self._failed))

    def reject(self):
        self._live_timer.stop()
        if self._future is not None:
            self._close_requested = True
            self.close_button.setEnabled(False)
            self.feedback.setText("Finishing the current preview before closing…")
            self._update_actions()
            return
        if self._draft != self._original:
            buttons = (QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                       | QMessageBox.StandardButton.Cancel)
            choice = QMessageBox.question(self, "Save stage settings?",
                                          "Save the changed settings for this pipeline stage?",
                                          buttons, QMessageBox.StandardButton.Save)
            if choice == QMessageBox.StandardButton.Cancel:
                self.start_preview()
                return
            if choice == QMessageBox.StandardButton.Save:
                try:
                    self._save(self._draft.copy())
                except RetouchingError as exc:
                    self.feedback.setText(f"{exc.code}: {exc}")
                    return
        super().reject()
