def verify_imports() -> str:
    from PySide6 import QtCore, QtWidgets
    return QtCore.qVersion()


def run_desktop(snapshot, application) -> int:
    from PySide6.QtWidgets import QApplication
    from .main_window import MainWindow

    app = QApplication([])
    window = MainWindow(snapshot, application.pipeline_service, application=application)
    window.show()
    return app.exec()
