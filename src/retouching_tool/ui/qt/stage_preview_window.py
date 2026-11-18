"""Modal stage comparison, including local draft settings and preview scheduling."""

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QVBoxLayout)

from ...errors import ResizeApprovalRequired, RetouchingError
from .picture_window import PictureComparison, RenderProgressDialog, statistics_text
from .stage_card import ChoiceControl, parameter_control


def confirm_resize(parent, message):
    buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    return QMessageBox.question(parent, "Allow temporary resizing?", message, buttons,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


class StagePreviewWindow(QDialog):
    def __init__(self, parent, stage, descriptor, filename, *, through_stage, submit, save, neutral_settings=None):
        super().__init__(parent, objectName="stagePreviewWindow")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle(f"{descriptor.title} · {filename}")
        self.resize(1050, 720)
        self._submit, self._save = submit, save
        self._neutral_settings = neutral_settings
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
        self.comparison = PictureComparison("Before this stage" if through_stage else "Source", "After this stage")
        layout.addWidget(self.comparison, 1)
        self.statistics = QLabel(objectName="previewStatistics", wordWrap=True)
        self.statistics.hide()
        layout.addWidget(self.statistics)

        self._controls = {}
        self.pick_neutral = None
        if descriptor.editable_parameters:
            settings = QGroupBox("Stage settings")
            form = QFormLayout(settings)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
            for parameter in descriptor.editable_parameters:
                control = parameter_control(parameter)
                self._controls[parameter.name] = control
                if isinstance(control, QDoubleSpinBox):
                    control.installEventFilter(self)
                    control.lineEdit().installEventFilter(self)
                control.setValue(self._original[parameter.name])
                control.setMinimumWidth(110)
                hint = ("Updates the preview automatically." if parameter.preview_on_change
                        else "Select Preview changes to update the preview.")
                control.setToolTip(hint)
                signal = control.currentIndexChanged if isinstance(control, ChoiceControl) else control.valueChanged
                signal.connect(lambda _, p=parameter, widget=control, initial=control.value():
                               self._change(p, widget.value(), initial))
                form.addRow(parameter.label, control)
            if descriptor.neutral_picker:
                self.pick_neutral = QPushButton("Pick neutral from input", checkable=True, autoDefault=False)
                self.pick_neutral.setToolTip("Click a gray card or neutral midtone to reuse this correction across photos.")
                self.pick_neutral.setEnabled(False)
                self.pick_neutral.toggled.connect(self._arm_picker)
                self.comparison.before.pixel_picked.connect(self._pick)
                form.addRow(self.pick_neutral)
            layout.addWidget(settings)

        self.feedback = QLabel(objectName="stagePreviewFeedback", wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        self.feedback.hide()
        layout.addWidget(self.feedback)
        actions = QHBoxLayout()
        self._has_manual_settings = any(not p.preview_on_change for p in descriptor.editable_parameters)
        self._manual_refresh = self._has_manual_settings
        self.render_dialog = RenderProgressDialog(self)
        self.live_progress = QProgressBar(self, accessibleName="Automatic preview progress", textVisible=False)
        self.live_progress.setFixedSize(120, self.live_progress.sizeHint().height())
        if self._has_manual_settings and not any(p.preview_on_change for p in descriptor.editable_parameters):
            self.live_progress.hide()
        actions.addWidget(self.live_progress)
        self.status = QLabel()
        actions.addWidget(self.status, 1)
        self.apply_preview = QPushButton("Preview changes", objectName="previewChanges", autoDefault=False)
        self.apply_preview.clicked.connect(self._preview_changes)
        actions.addWidget(self.apply_preview)
        self.close_button = QPushButton("Close", objectName="closeStagePreview", autoDefault=False)
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self._update_actions()
        self.finished.connect(self.comparison.clear)

    def _arm_picker(self, armed):
        before = self.comparison.before
        before.picking = armed
        before.setCursor(Qt.CursorShape.CrossCursor if armed else Qt.CursorShape.OpenHandCursor)
        if armed and self.comparison.mode.currentIndex() == self.comparison.RESULT:
            self.comparison.mode.setCurrentIndex(self.comparison.SOURCE)

    def _pick(self, x, y):
        try:
            settings = self._neutral_settings(x, y)
        except RetouchingError as exc:
            self.feedback.setText(str(exc))
            self.feedback.show()
            return
        for name, value in settings.items():
            self._controls[name].setValue(value)
        self.feedback.hide()
        self.pick_neutral.setChecked(False)

    def eventFilter(self, watched, event):
        # Numeric editors normally claim printable keys before window shortcuts.
        if (event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Space
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            event.ignore()
            return True
        return super().eventFilter(watched, event)

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
        self._manual_refresh = self._has_manual_settings
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
                self.render_dialog.hide()
                self.feedback.setText(f"{exc.code}: {exc}")
                self.feedback.show()
                if (isinstance(exc, ResizeApprovalRequired) and not self._close_requested
                        and confirm_resize(self, f"{exc}\nAllow this for this stage preview?")):
                    self._resize_approvals += (exc.stage_id,)
                    self._submitted = None
        else:
            if current:
                self.comparison.display(preview)
                if self.pick_neutral is not None:
                    self.pick_neutral.setEnabled(True)
                measurements = statistics_text(preview.context)
                self.statistics.setText(measurements)
                self.statistics.setVisible(bool(measurements))
                self.feedback.setText(preview.stage_warning or "")
                self.feedback.setVisible(bool(preview.stage_warning))
        # An approved resize retries the same request with the same progress UI.
        if current and self._submitted is not None:
            self._manual_refresh = False
        if self._close_requested:
            self._close_requested = False
            self.close_button.setEnabled(True)
            self._update_actions()
            self.reject()
        else:
            if not self._live_timer.isActive():
                self.start_preview()
            self._update_actions()

    def _update_actions(self):
        unapplied = self._draft != self._requested
        busy = self._future is not None
        message = "Finishing preview before closing…" if self._close_requested else "Rendering preview…"
        self.render_dialog.message.setText(message)
        self.render_dialog.close_preview.setEnabled(not self._close_requested)
        self.render_dialog.setVisible(busy and self._manual_refresh)
        self.live_progress.setRange(0, 0 if busy and not self._manual_refresh else 1)
        self.live_progress.setValue(0)
        if busy:
            self.status.setText(message)
        elif unapplied:
            self.status.setText("Changes pending")
        else:
            self.status.setText("Preview failed" if self._failed else "Ready")
        self.apply_preview.setVisible(self._has_manual_settings or self._failed)
        self.apply_preview.setEnabled(not self._close_requested and (unapplied or self._failed))

    def reject(self):
        self._live_timer.stop()
        if self._future is not None:
            self._close_requested = True
            self.close_button.setEnabled(False)
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
                    self.feedback.show()
                    return
        super().reject()
