"""Modal stage comparison, including local draft settings and preview scheduling."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QBoxLayout, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QVBoxLayout)

from ...application.preview import StagePreviewMode
from ...errors import ResizeApprovalRequired, RetouchingError
from ...pipeline.definition import BooleanParameter, MaskTargetParameter
from .picture_window import PictureComparison, RenderProgressDialog, statistics_text
from .stage_card import ChoiceControl, parameter_control, populate_parameter_form, update_choice_dependents


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
        self.descriptor = descriptor
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
        self._levels_layout = None
        layout.addWidget(self.comparison, 1)
        self.statistics = QLabel(objectName="previewStatistics", wordWrap=True)
        self.statistics.hide()
        layout.addWidget(self.statistics)

        self._controls = {}
        self.pick_neutral = None
        self.levels = None
        self._levels_input = None
        self._point_to_pick = ""
        if descriptor.editable_parameters:
            settings = QGroupBox("Stage settings")
            form = QFormLayout(settings)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
            for parameter in descriptor.editable_parameters:
                control = parameter_control(parameter)
                self._controls[parameter.name] = control
                control.setValue(self._original[parameter.name])
                control.setMinimumWidth(110)
                hint = ("Updates the preview automatically." if parameter.preview_on_change
                        else "Select Preview changes to update the preview.")
                control.setToolTip(hint)
                signal = control.currentIndexChanged if isinstance(control, ChoiceControl) else control.valueChanged
                signal.connect(lambda _, p=parameter, widget=control, initial=control.value():
                               self._change(p, widget.value(), initial))
            self.levels = populate_parameter_form(form, descriptor, self._controls, preview=True)
            if self.levels is not None:
                self.levels.pick_requested.connect(self._arm_levels_picker)
                self.comparison.before.pixel_picked.connect(self._pick_level)
            for target in descriptor.parameters:
                if isinstance(target, MaskTargetParameter):
                    toggle = self._controls[target.name]
                    for option in descriptor.parameters:
                        if isinstance(option, BooleanParameter) and option.optional_context_key:
                            region = self._controls[option.name]
                            region.setDisabled(toggle.isChecked())
                            toggle.toggled.connect(region.setDisabled)
            update_choice_dependents(descriptor, self._controls)
            if descriptor.neutral_picker:
                self.pick_neutral = QPushButton("Pick neutral from input", checkable=True, autoDefault=False)
                self.pick_neutral.setToolTip("Click a gray card or neutral midtone to reuse this correction across photos.")
                self.pick_neutral.setEnabled(False)
                self.pick_neutral.toggled.connect(self._arm_picker)
                self.comparison.before.pixel_picked.connect(self._pick)
                form.addRow(self.pick_neutral)
            if self.levels is None:
                layout.addWidget(settings)
            else:
                settings.setMinimumWidth(365)
                layout.removeWidget(self.comparison)
                self._levels_layout = QHBoxLayout()
                self._levels_layout.addWidget(self.comparison, 1)
                self._levels_layout.addWidget(settings, 0, Qt.AlignmentFlag.AlignTop)
                layout.insertLayout(0, self._levels_layout, 1)

        self.feedback = QLabel(objectName="stagePreviewFeedback", wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        self.feedback.setMinimumHeight(self.feedback.fontMetrics().height())
        policy = self.feedback.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.feedback.setSizePolicy(policy)
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
        self.preview_mode = QComboBox(accessibleName="Stage preview mode")
        self.preview_mode.addItems(list(StagePreviewMode))
        self.preview_mode.setVisible(descriptor.changes_pixels)
        self.preview_mode.currentIndexChanged.connect(self.start_preview)
        actions.addWidget(self.preview_mode)
        self.apply_preview = QPushButton("Preview changes", objectName="previewChanges", autoDefault=False)
        self.apply_preview.clicked.connect(self._preview_changes)
        actions.addWidget(self.apply_preview)
        self.close_button = QPushButton("Close", objectName="closeStagePreview", autoDefault=False)
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self._update_actions()
        self.finished.connect(self.comparison.clear)
        self.finished.connect(self._clear_levels_input)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._levels_layout is not None:
            direction = QBoxLayout.Direction
            self._levels_layout.setDirection(direction.LeftToRight if event.size().width() >= 860
                                             else direction.TopToBottom)

    def _clear_levels_input(self):
        self._levels_input = None

    def _arm_levels_picker(self, name):
        self._point_to_pick = name
        if name:
            self.comparison.mode.setCurrentIndex(self.comparison.SIDE_BY_SIDE)
        self._arm_picker(bool(name))

    def _pick_level(self, x, y):
        name, image = self._point_to_pick, self._levels_input
        if not name or image is None or not (0 <= x < image.width and 0 <= y < image.height):
            return
        start = (y * image.width + x) * 3
        rgb = image.rgb[start:start + 3]
        channel = self._controls["channel"].value()
        if channel == "RGB":
            value = max(rgb) if name == "input_black" else min(rgb)
        else:
            value = rgb[("Red", "Green", "Blue").index(channel)]
        control = self._controls[name]
        if not control.minimum() <= value <= control.maximum():
            self.feedback.setText(f"Sampled {value}. Choose an {name.replace('_', ' ')} point "
                                  f"between {control.minimum()} and {control.maximum()}.")
            self.feedback.show()
            return
        control.setValue(value)
        self.levels.clear_picker()
        self.feedback.hide()

    def _arm_picker(self, armed):
        before = self.comparison.before
        before.picking = armed
        before.setCursor(Qt.CursorShape.CrossCursor if armed else Qt.CursorShape.OpenHandCursor)

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

    def _change(self, parameter, value, initial):
        update_choice_dependents(self.descriptor, self._controls)
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
        request = (self._requested.copy(), self.preview_mode.currentText())
        if self._close_requested or self._future is not None or request == self._submitted:
            return
        self._submitted = request
        self._future = self._submit(request[0], self._resize_approvals, request[1])
        self._failed = False
        self._poll_timer.start()
        self._update_actions()

    def _finish_preview(self):
        if not self._future.done():
            return
        future, self._future = self._future, None
        self._poll_timer.stop()
        current = (self._requested, self.preview_mode.currentText()) == self._submitted
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
                if self.levels is not None and self._levels_input is not preview.original:
                    self._levels_input = preview.original
                    self.levels.set_histogram(preview.original.histogram)
                    for button in self.levels.pickers.values():
                        button.setEnabled(True)
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
