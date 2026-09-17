from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from devmesh_studio.core.storage import Storage
from devmesh_studio.runtime.tool_registry import ToolRegistry
from devmesh_studio.ui.pages import CallsPage


class LiveCallDetailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "devmesh.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_call_payloads_are_lazy_but_inspectable(self):
        call_id = self.storage.begin_call("tester", None, "demo", "resource", {"alpha": 1})
        self.storage.set_call_result(call_id, {"ok": True, "payload": "x"})
        self.storage.end_call(call_id, "ok", "completed", 7)

        row = self.storage.list_calls(10)[0]
        self.assertNotIn("args_json", row)
        self.assertNotIn("result_json", row)

        call = self.storage.get_call(call_id)
        self.assertEqual(call["args"], {"alpha": 1})
        self.assertEqual(call["result"], {"ok": True, "payload": "x"})

    def test_legacy_database_gets_payload_columns(self):
        legacy = Path(self.tmp.name) / "legacy.sqlite3"
        db = sqlite3.connect(legacy)
        db.execute(
            """CREATE TABLE tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at INTEGER NOT NULL,
                ended_at INTEGER,
                actor TEXT NOT NULL,
                repo_id INTEGER,
                tool TEXT NOT NULL,
                resource TEXT,
                args_hash TEXT,
                status TEXT NOT NULL,
                duration_ms INTEGER,
                summary TEXT
            )"""
        )
        db.commit(); db.close()

        migrated = Storage(legacy)
        with migrated.conn() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tool_calls)").fetchall()}
        self.assertIn("args_json", columns)
        self.assertIn("result_json", columns)

    def test_registry_persists_tool_result(self):
        repo_root = Path(self.tmp.name) / "repo"
        repo_root.mkdir()
        (repo_root / "hello.txt").write_text("hello", encoding="utf-8")
        repo = self.storage.add_repository(str(repo_root))
        repo_id = int(repo["id"])
        registry = ToolRegistry(self.storage)

        result = asyncio.run(registry.dispatch("tester", "fs_stat", {"repo_id": repo_id, "path": "hello.txt"}))
        self.assertEqual(result["path"], "hello.txt")

        call = self.storage.get_call(self.storage.list_calls(1)[0]["id"])
        self.assertEqual(call["tool"], "fs_stat")
        self.assertEqual(call["args"]["path"], "hello.txt")
        self.assertEqual(call["result"]["path"], "hello.txt")

    def test_calls_page_renders_selected_call(self):
        app = QApplication.instance() or QApplication([])
        call_id = self.storage.begin_call("tester", None, "terminal_exec", "echo hello", {"command": "echo hello", "timeout": 10})
        self.storage.set_call_result(call_id, {"returncode": 0, "stdout": "hello\n", "stderr": "", "duration_ms": 1})
        self.storage.end_call(call_id, "ok", "completed", 1)

        page = CallsPage(self.storage)
        page.refresh()
        self.assertEqual(page.selected_id(), call_id)
        self.assertIn("echo hello", page.overview.toPlainText())
        self.assertIn("STDOUT", page.artifact_view.toPlainText())
        page.deleteLater()
        app.processEvents()

    def test_main_window_does_not_poll_git_page(self):
        from argon2 import PasswordHasher
        from devmesh_studio.ui.main_window import MainWindow

        old_data_dir = os.environ.get("DEVMESH_DATA_DIR")
        data_dir = Path(self.tmp.name) / "app-data"
        os.environ["DEVMESH_DATA_DIR"] = str(data_dir)
        try:
            storage = Storage()
            storage.set_setting("username", "tester")
            storage.set_setting("password_hash", PasswordHasher().hash("test-password-123"))
            storage.set_setting("auto_tunnel", "0")
            repo_root = Path(self.tmp.name) / "registered-repo"
            repo_root.mkdir()
            storage.add_repository(repo_root)

            app = QApplication.instance() or QApplication([])
            window = MainWindow()
            app.processEvents()
            self.assertFalse(any(row["tool"] == "git_status" for row in window.storage.list_calls(100)))

            window.nav.setCurrentRow(7)  # explicit navigation to Git may refresh once
            app.processEvents()
            before = len([row for row in window.storage.list_calls(100) if row["tool"] == "git_status"])
            window.refresh_live_current()
            app.processEvents()
            after = len([row for row in window.storage.list_calls(100) if row["tool"] == "git_status"])
            self.assertEqual(after, before)

            window._quitting = True
            window.close()
            app.processEvents()
        finally:
            if old_data_dir is None:
                os.environ.pop("DEVMESH_DATA_DIR", None)
            else:
                os.environ["DEVMESH_DATA_DIR"] = old_data_dir


if __name__ == "__main__":
    unittest.main()
