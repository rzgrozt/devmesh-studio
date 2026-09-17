from __future__ import annotations

import os

import pytest


pytest.importorskip("PyQt6", reason="PyQt6 is installed by DevMesh bootstrap on the target machine")


def test_main_window_offscreen_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("DEVMESH_DATA_DIR", str(tmp_path / "devmesh-data"))

    from argon2 import PasswordHasher
    from PyQt6.QtWidgets import QApplication

    from devmesh_studio.core.storage import Storage
    from devmesh_studio.ui.main_window import MainWindow

    storage = Storage()
    storage.set_setting("username", "test-user")
    storage.set_setting("password_hash", PasswordHasher().hash("test-password-123"))
    storage.set_setting("auto_tunnel", "0")

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    app.processEvents()

    assert window.windowTitle() == "DevMesh Studio"
    assert window.stack.count() == 13
    assert window.nav.count() == 13

    window._quitting = True
    window.close()
    app.processEvents()
