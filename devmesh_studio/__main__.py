from __future__ import annotations

import sys
from .core.platform import configure_frozen_qt


def main() -> None:
    configure_frozen_qt()
    # Qt must be imported after frozen Windows DLL/plugin paths are configured.
    from PyQt6.QtWidgets import QApplication
    from .ui.main_window import MainWindow

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
