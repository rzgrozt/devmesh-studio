from __future__ import annotations

import shlex
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
    QMessageBox, QPlainTextEdit, QVBoxLayout
)
from argon2 import PasswordHasher

from devmesh_studio.core.storage import Storage
from devmesh_studio.core.secrets import SecretStore


class SetupDialog(QDialog):
    def __init__(self, storage: Storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.setWindowTitle("Set up DevMesh")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.username = QLineEdit(storage.get_setting("username", "devmesh") or "devmesh")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.tunnel = QCheckBox("Automatically create an HTTPS Quick Tunnel")
        self.tunnel.setChecked(True)
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        form.addRow("Confirm", self.confirm)
        form.addRow("", self.tunnel)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        user = self.username.text().strip()
        pw = self.password.text()
        if not user:
            QMessageBox.warning(self, "DevMesh", "Username is required.")
            return
        if len(pw) < 12:
            QMessageBox.warning(self, "DevMesh", "Use a password with at least 12 characters.")
            return
        if pw != self.confirm.text():
            QMessageBox.warning(self, "DevMesh", "Passwords do not match.")
            return
        self.storage.set_setting("username", user)
        self.storage.set_setting("password_hash", PasswordHasher().hash(pw))
        self.storage.set_setting("auto_tunnel", "1" if self.tunnel.isChecked() else "0")
        self.accept()


class CredentialsDialog(QDialog):
    def __init__(self, storage: Storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.setWindowTitle("Change DevMesh credentials")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.username = QLineEdit(storage.get_setting("username", "devmesh") or "devmesh")
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit(); self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Username", self.username); form.addRow("New password", self.password); form.addRow("Confirm", self.confirm)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        if len(self.password.text()) < 12 or self.password.text() != self.confirm.text():
            QMessageBox.warning(self, "DevMesh", "Passwords must match and contain at least 12 characters.")
            return
        username = self.username.text().strip()
        if not username:
            QMessageBox.warning(self, "DevMesh", "Username is required.")
            return
        self.storage.set_setting("username", username)
        self.storage.set_setting("password_hash", PasswordHasher().hash(self.password.text()))
        # Credential changes revoke existing OAuth sessions immediately while
        # preserving registered clients, so ChatGPT can simply reconnect.
        self.storage.revoke_oauth_sessions()
        SecretStore().rotate("jwt_secret", 64)
        self.accept()


class MCPServerDialog(QDialog):
    def __init__(self, parent=None, initial: dict | None = None):
        super().__init__(parent)
        self.initial = initial or {}
        self.setWindowTitle("Configure downstream MCP server")
        self.setMinimumWidth(550)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.transport = QComboBox(); self.transport.addItems(["stdio", "http"])
        self.command = QLineEdit()
        self.args = QLineEdit()
        self.url = QLineEdit()
        self.allowed = QLineEdit(); self.allowed.setPlaceholderText("Comma-separated tool names or patterns; * allows every tool")
        self.env = QPlainTextEdit(); self.env.setPlaceholderText("Optional KEY=value lines. Values stay local and are never shown to ChatGPT.")
        self.env.setMaximumHeight(100)
        form.addRow("Name", self.name); form.addRow("Transport", self.transport)
        form.addRow("Command", self.command); form.addRow("Arguments", self.args); form.addRow("HTTP URL", self.url)
        form.addRow("Allowed tools", self.allowed); form.addRow("Environment", self.env)
        layout.addLayout(form)
        if self.initial:
            self.name.setText(str(self.initial.get("name", "")))
            self.transport.setCurrentText(str(self.initial.get("transport", "stdio")))
            self.command.setText(str(self.initial.get("command") or ""))
            self.args.setText(" ".join(shlex.quote(str(x)) for x in self.initial.get("args", [])))
            self.url.setText(str(self.initial.get("url") or ""))
            self.allowed.setText(", ".join(self.initial.get("allowed_tools", [])))
            self.env.setPlainText("\n".join(f"{k}={v}" for k,v in self.initial.get("env", {}).items()))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def value(self) -> dict:
        env = {}
        for line in self.env.toPlainText().splitlines():
            if "=" in line:
                k, v = line.split("=", 1); env[k.strip()] = v
        return {
            "name": self.name.text().strip(),
            "transport": self.transport.currentText(),
            "command": self.command.text().strip() or None,
            "args": shlex.split(self.args.text()) if self.args.text().strip() else [],
            "url": self.url.text().strip() or None,
            "allowed_tools": [x.strip() for x in self.allowed.text().split(",") if x.strip()],
            "env": env,
        }
