"""Remember logical window sizes, including the normal size of maximized windows."""

from PySide6.QtCore import QEvent, QObject, QSize, Qt

from ...errors import RetouchingError


class WindowSize(QObject):
    def __init__(self, window, name, application):
        super().__init__(window)
        self.name, self.application = name, application
        try:
            saved = application.state.window_size(name)
            if saved:
                size = QSize(saved["width"], saved["height"])
                window.resize(size.boundedTo(window.screen().availableGeometry().size()))
                if saved["maximized"]:
                    window.setWindowState(window.windowState() | Qt.WindowState.WindowMaximized)
        except (OSError, RetouchingError) as exc:
            self._warn("WINDOW_SIZE_RESTORE_FAILED", exc)
        window.installEventFilter(self)

    def eventFilter(self, window, event):
        if event.type() == QEvent.Type.Hide:
            maximized = window.isMaximized()
            size = window.normalGeometry().size() if maximized or window.isMinimized() else window.size()
            if not size.isEmpty():
                try:
                    self.application.state.update(windows={self.name: {
                        "width": size.width(), "height": size.height(), "maximized": maximized}})
                except (OSError, RetouchingError) as exc:
                    self._warn("WINDOW_SIZE_SAVE_FAILED", exc)
        return False

    def _warn(self, code, exc):
        self.application.window_state_warning = f"Could not remember or restore the {self.name} window size: {exc}"
        self.application.diagnostics.emit(code, self.application.window_state_warning, component="desktop",
            level="WARNING", exc_info=True, window=self.name, state_path=self.application.state.path)
