from __future__ import annotations

import sys
from PyQt6.QtWidgets import QApplication
from .ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("DevMesh Studio")
    app.setOrganizationName("DevMesh")
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    _ = app.aboutToQuit.connect(window.shutdown_runtime)
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
