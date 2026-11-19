"""Qt controls for editing and exchanging portable pipelines."""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ...errors import RetouchingError
from .stage_card import StageCard


class PresetPicker(QComboBox):
    opening = Signal()

    def showPopup(self):
        self.opening.emit()
        super().showPopup()


class PipelinePanel(QWidget):
    changed = Signal()
    preview_requested = Signal(str, bool)

    def __init__(self, snapshot, service):
        super().__init__()
        self.snapshot = snapshot
        self.config = snapshot
        self.service = service
        self.origin = snapshot.pipeline_origin
        self.stage_cards = {}
        self.reveal_stage = None
        self.preview_available = False
        self.preset_folder_error = ""
        self.command_message = ""
        self.refresh_timer = QTimer(self, interval=0, singleShot=True)
        self.refresh_timer.timeout.connect(self._refresh_cards)

        layout = QVBoxLayout(self)
        self.details = QPlainTextEdit()
        self.details.setObjectName("startupDetails")
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(150)
        self._show_details(snapshot.pipeline_name)
        if service is None:
            layout.addWidget(self.details)
            return

        files = QHBoxLayout()
        new_file = QPushButton("New pipeline", objectName="newPipeline")
        new_file.clicked.connect(self._new_pipeline)
        files.addWidget(new_file)
        open_file = QPushButton("Open JSON…", objectName="openPipeline")
        open_file.clicked.connect(self._open)
        files.addWidget(open_file)
        save_file = QPushButton("Save JSON…", objectName="savePipeline")
        save_file.clicked.connect(self._save)
        files.addWidget(save_file)
        details_toggle = QPushButton("Session details", checkable=True)
        details_toggle.toggled.connect(self.details.setVisible)
        files.addStretch()
        files.addWidget(details_toggle)
        layout.addLayout(files)
        layout.addWidget(self.details)
        self.details.hide()
        if snapshot.notices:
            notices = QLabel("\n".join(snapshot.notices), objectName="startupNotices", wordWrap=True)
            notices.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(notices)

        preset_row = QHBoxLayout()
        self.preset_label = QLabel("Preset")
        self.presets = PresetPicker(objectName="presetPicker", accessibleName="Pipeline preset")
        self.preset_label.setBuddy(self.presets)
        self.presets.opening.connect(self._refresh_presets)
        self.presets.activated.connect(self._select_preset)
        preset_row.addWidget(self.preset_label)
        preset_row.addWidget(self.presets, 1)
        layout.addLayout(preset_row)

        add = QPushButton("Add operation", objectName="addOperation")
        menu = QMenu(add)
        for descriptor in service.registry.descriptors():
            for model_ref in descriptor.model_refs or (None,):
                label = f"{descriptor.title} · v{descriptor.version}"
                if model_ref is not None:
                    label += f" · {model_ref}"
                action = menu.addAction(label)
                action.setData((descriptor.operation, descriptor.version, model_ref))
        menu.triggered.connect(self._add_stage)
        add.setMenu(menu)
        add.setEnabled(not menu.isEmpty())
        layout.addWidget(add, alignment=Qt.AlignmentFlag.AlignLeft)

        self.message = QLabel(objectName="pipelineCommandError", wordWrap=True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.hide()
        layout.addWidget(self.message)
        self.scroll = QScrollArea(widgetResizable=True)
        card_content = QWidget()
        self.cards = QVBoxLayout(card_content)
        self.empty = QLabel("No operations added.")
        self.cards.addWidget(self.empty)
        self.cards.addStretch()
        self.scroll.setWidget(card_content)
        layout.addWidget(self.scroll, 1)
        self._refresh_presets()
        self._refresh_cards()

    def _show_details(self, name):
        snapshot = self.snapshot
        text = "\n".join([
            f"Pipeline: {name}", f"Loaded from: {self.origin}", f"ONNX Runtime Backend: {snapshot.backend}",
            f"PyTorch Backend: {snapshot.pytorch_backend or 'Unconfigured'}", "",
            "Sources at launch:", *([str(p) for p in snapshot.sources] or ["No sources selected"]), "",
            f"Checkpoints: {self.config.checkpoints}", f"Temporary artifacts: {self.config.temporary}",
            f"Logs: {self.config.logs}",
        ])
        if self.service is None and snapshot.notices:
            text += "\n\n" + "\n".join(snapshot.notices)
        if text != self.details.toPlainText():
            self.details.setPlainText(text)

    def show_configuration(self, config):
        self.config = config
        self._refresh_presets()
        self._refresh_cards()

    def _refresh_presets(self):
        self.presets.clear()
        try:
            paths = self.service.list_presets()
        except RetouchingError as exc:
            self.presets.setPlaceholderText("Preset folder unavailable")
            self.preset_folder_error = f"{exc.code}: {exc}"
        else:
            self.preset_folder_error = ""
            for path in paths:
                self.presets.addItem(path.stem, str(path))
            self.presets.setPlaceholderText("" if paths else "No presets found")
        self._show_message(self.command_message)
        self._sync_preset()

    def _sync_preset(self):
        index = self.presets.findData(str(self.service.preset_path), flags=Qt.MatchFlag.MatchFixedString)
        self.presets.setCurrentIndex(index)
        self.preset_label.setText("Preset (unsaved)" if self.service.has_unsaved_changes else "Preset")

    def _select_preset(self, index):
        path = self.presets.itemData(index)
        if path is not None:
            self._load(Path(path))

    def set_locked(self, locked):
        self.setEnabled(not locked)
        if not locked:
            self._refresh_cards()
            self.changed.emit()

    def set_preview_available(self, available):
        self.preview_available = available
        for card in self.stage_cards.values():
            card.set_preview_available(available)

    def show_diagnostic_operation(self, event):
        card = self.stage_cards.get(event.stage_id)
        if (card is None or (event.operation and card.descriptor.operation != event.operation)
                or (event.operation_version is not None and card.descriptor.version != event.operation_version)):
            return False
        card.expand.setChecked(True)
        self.scroll.ensureWidgetVisible(card)
        card.toggle.setFocus()
        return True

    def _refresh_cards(self):
        pipeline = self.service.status
        stages = pipeline.stages
        self._sync_preset()
        self._show_details(pipeline.definition.name)
        self.empty.setVisible(not stages)
        current_ids = {status.stage.id for status in stages}
        for stage_id in self.stage_cards.keys() - current_ids:
            self._discard_card(stage_id)
        for index, status in enumerate(stages):
            stage = status.stage
            descriptor = self.service.registry.descriptor(stage.operation, stage.operation_version)
            card = self.stage_cards.get(stage.id)
            if card is not None and card.descriptor != descriptor:
                self._discard_card(stage.id)
                card = None
            if card is None:
                card = StageCard(
                    stage.id, descriptor,
                    on_enable=lambda key, enabled: self._command(self.service.set_enabled, key, enabled),
                    on_parameter=lambda key, name, value: self._command(self.service.set_parameter, key, name, value),
                    on_recheck=self._recheck_stage,
                    on_move=self._move_stage,
                    on_remove=self._remove_stage,
                    on_preview=self.preview_requested.emit,
                )
                self.stage_cards[stage.id] = card
                self.cards.insertWidget(index, card)
                card.expand.setChecked(stage.id == self.reveal_stage)
            elif self.cards.indexOf(card) != index:
                self.cards.removeWidget(card)
                self.cards.insertWidget(index, card)
            card.show_status(status, can_move_up=index > 0, can_move_down=index + 1 < len(stages))
            card.set_preview_available(self.preview_available)
        if self.reveal_stage in self.stage_cards:
            self.cards.activate()
            self.scroll.ensureWidgetVisible(self.stage_cards[self.reveal_stage])
        self.reveal_stage = None

    def _discard_card(self, stage_id):
        card = self.stage_cards.pop(stage_id)
        self.cards.removeWidget(card)
        card.hide()
        card.setParent(None)
        card.deleteLater()

    def _command(self, action, *args, **kwargs):
        previous = self.service.status
        try:
            result = action(*args, **kwargs)
        except RetouchingError as exc:
            self._show_message(f"{exc.code}: {exc}")
            result = None
        else:
            self._show_message("")
        if self.service.status is not previous:
            self._sync_preset()
            self.changed.emit()
        # Defer layout changes until the originating widget's signal has returned.
        self.refresh_timer.start()
        return result

    def _show_message(self, text):
        self.command_message = text
        message = "\n".join(filter(None, (text, self.preset_folder_error, self.service.diagnostics.failure)))
        self.message.setText(message)
        self.message.setVisible(bool(message))

    def _recheck_stage(self, stage_id):
        status = self._command(self.service.recheck, write_unchanged=False)
        if status is None:
            return
        item = next(s for s in status.stages if s.stage.id == stage_id)
        if item.issues or item.dependency_errors:
            self._show_message(f"{item.title} needs attention. See the details on its card.")
        elif item.stage.model_ref:
            self._show_message(f"{item.title} · Required model files verified.")
        else:
            detail = "Dependencies checked." if item.stage.enabled else "Stage is off."
            self._show_message(f"{item.title} · {detail}")

    def _add_stage(self, action):
        operation, version, model_ref = action.data()
        status = self._command(self.service.add_operation, operation, version, model_ref=model_ref)
        if status is not None:
            self.reveal_stage = status.stages[-1].stage.id

    def _remove_stage(self, stage_id):
        card = self.stage_cards[stage_id]
        position = self.cards.indexOf(card) + 1
        answer = QMessageBox.question(
            self, "Remove stage", f"Remove stage {position} ({card.descriptor.title}) from the pipeline?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            self._command(self.service.remove, stage_id)

    def _move_stage(self, stage_id, step):
        order = [stage.id for stage in self.service.status.definition.stages]
        if stage_id in order:
            index = order.index(stage_id)
            target = index + step
            if 0 <= target < len(order):
                order[index], order[target] = order[target], order[index]
                if self._command(self.service.reorder, order) is not None:
                    self.reveal_stage = stage_id

    def _choose_file(self, *, save=False):
        dialog = QFileDialog(self, "Save pipeline" if save else "Open pipeline", str(self.service.preset_folder))
        dialog.setNameFilter("Pipeline JSON (*.json)")
        if save:
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            dialog.setDefaultSuffix("json")
        else:
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        try:
            if dialog.exec():
                return Path(dialog.selectedFiles()[0])
            return None
        finally:
            dialog.deleteLater()

    def _new_pipeline(self):
        answer = QMessageBox.question(
            self, "New pipeline", "Clear all stages and start a new pipeline? Any unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes and self._command(self.service.new_pipeline) is not None:
            self.origin = "New pipeline"

    def _open(self):
        path = self._choose_file()
        if path is not None:
            self._load(path)

    def _load(self, path):
        self._sync_preset()
        if self.service.has_unsaved_changes:
            answer = QMessageBox.question(
                self, "Unsaved pipeline changes", f"Discard the current pipeline changes and load {path.name}?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
        if self._command(self.service.load, path) is not None:
            self.origin = str(path)
            self.scroll.verticalScrollBar().setValue(0)
            if self.service.preset_notice:
                self._show_message(self.service.preset_notice)

    def _save(self):
        path = self._choose_file(save=True)
        if path is None:
            return
        if path.suffix.casefold() != ".json":
            self._show_message("Choose a filename ending in .json to save the pipeline.")
            return
        try:
            self.service.save(path)
        except RetouchingError as exc:
            self._show_message(f"{exc.code}: {exc}")
        else:
            self.origin = str(path)
            self._show_details(self.service.status.definition.name)
            self._show_message(self.service.preset_notice or f"Saved pipeline to {path}")
            self._refresh_presets()
            self.changed.emit()
