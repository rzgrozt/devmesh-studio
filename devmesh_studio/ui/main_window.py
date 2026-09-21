from __future__ import annotations

import queue
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction, QCloseEvent
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMenu,
    QMessageBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget
)

from devmesh_studio.core.storage import Storage
from devmesh_studio.services.runtime_supervisor import RuntimeSupervisor
from .dialogs import SetupDialog
from .pages import (
    AgentsPage, ApprovalsPage, CallsPage, ChangesPage, ConnectionsPage,
    DashboardPage, GitPage, LSPPage, MCPPage, PermissionsPage,
    RepositoriesPage, SkillsPage, TerminalPage,
)
from .style import APP_STYLE


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DevMesh Studio")
        self.resize(1520, 920)
        self.setMinimumSize(1120, 720)
        self.setStyleSheet(APP_STYLE)
        self.storage = Storage()
        self.events: queue.Queue[str] = queue.Queue()
        self.supervisor = RuntimeSupervisor(self.storage, self.events.put)
        self._quitting = False
        self._shutdown_done = False

        if not self.storage.get_setting("password_hash"):
            dlg = SetupDialog(self.storage, self)
            if dlg.exec() != dlg.DialogCode.Accepted:
                raise SystemExit(0)

        central = QWidget(); self.setCentralWidget(central)
        root = QHBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        sidebar = QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(232)
        side = QVBoxLayout(sidebar); side.setContentsMargins(12, 18, 12, 12); side.setSpacing(10)
        brand = QLabel("DEVMESH"); brand.setObjectName("brand")
        tagline = QLabel("local coding control plane"); tagline.setObjectName("sidebarMuted")
        side.addWidget(brand); side.addWidget(tagline); side.addSpacing(8)

        self.nav = QListWidget(); self.nav.setObjectName("nav")
        side.addWidget(self.nav, 1)

        footer = QFrame(); footer.setObjectName("sidebarFooter")
        footer_lay = QVBoxLayout(footer); footer_lay.setContentsMargins(10, 9, 10, 9); footer_lay.setSpacing(2)
        self.sidebar_status = QLabel("● Runtime stopped"); self.sidebar_status.setObjectName("dangerText")
        self.sidebar_hint = QLabel("Local developer gateway"); self.sidebar_hint.setObjectName("sidebarMuted")
        self.sidebar_hint.setWordWrap(True)
        footer_lay.addWidget(self.sidebar_status); footer_lay.addWidget(self.sidebar_hint)
        side.addWidget(footer)

        self.stack = QStackedWidget(); self.stack.setObjectName("contentStack")
        root.addWidget(sidebar); root.addWidget(self.stack, 1)

        self.dashboard = DashboardPage(self.storage, self.supervisor)
        self.repositories = RepositoriesPage(self.storage)
        self.calls = CallsPage(self.storage)
        self.changes = ChangesPage(self.storage)
        self.approvals = ApprovalsPage(self.storage)
        self.permissions = PermissionsPage(self.storage)
        self.terminal = TerminalPage(self.storage)
        self.git = GitPage(self.storage)
        self.mcp = MCPPage(self.storage)
        self.lsp = LSPPage(self.storage)
        self.agents = AgentsPage(self.storage)
        self.skills = SkillsPage(self.storage)
        self.connections = ConnectionsPage(self.storage, self.supervisor)

        pages = [
            ("Dashboard", self.dashboard),
            ("Repositories", self.repositories),
            ("Live Calls", self.calls),
            ("Changes", self.changes),
            ("Approvals", self.approvals),
            ("Permissions", self.permissions),
            ("Terminal", self.terminal),
            ("Git", self.git),
            ("MCP Servers", self.mcp),
            ("Language Servers", self.lsp),
            ("Agents", self.agents),
            ("Skills", self.skills),
            ("Connections", self.connections),
        ]
        for label, page in pages:
            self.nav.addItem(label)
            self.stack.addWidget(page)

        self.nav.currentRowChanged.connect(self._page_changed)
        self.nav.setCurrentRow(0)

        self.statusBar().showMessage("DevMesh ready")
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh_live_current); self.timer.start(1200)
        self.event_timer = QTimer(self); self.event_timer.timeout.connect(self.drain_events); self.event_timer.start(250)
        self.setup_tray()
        self.refresh_current()
        self.refresh_runtime_badge()

    def setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("DevMesh Studio")
        menu = QMenu()
        show_action = QAction("Open DevMesh", self); show_action.triggered.connect(self.show_normal)
        start_action = QAction("Start runtime", self); start_action.triggered.connect(lambda: self._runtime_action("start"))
        stop_action = QAction("Stop runtime", self); stop_action.triggered.connect(lambda: self._runtime_action("stop"))
        quit_action = QAction("Quit", self); quit_action.triggered.connect(self.quit_app)
        menu.addAction(show_action); menu.addSeparator(); menu.addAction(start_action); menu.addAction(stop_action); menu.addSeparator(); menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_normal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _runtime_action(self, name):
        try:
            getattr(self.supervisor, name)()
        except Exception as exc:
            QMessageBox.critical(self, "DevMesh", str(exc))
        self.refresh_all()
        self.refresh_runtime_badge()

    def refresh_runtime_badge(self):
        status = self.supervisor.status()
        running = bool(status.get("gateway"))
        self.sidebar_status.setText("● Runtime running" if running else "● Runtime stopped")
        self.sidebar_status.setObjectName("success" if running else "dangerText")
        self.sidebar_status.style().unpolish(self.sidebar_status); self.sidebar_status.style().polish(self.sidebar_status)
        if running:
            self.sidebar_hint.setText("MCP gateway is available")
        else:
            self.sidebar_hint.setText("Start it from Dashboard or Connections")

    def show_normal(self):
        self.show(); self.raise_(); self.activateWindow()

    def _page_changed(self, row: int):
        if row >= 0:
            self.stack.setCurrentIndex(row)
            self.refresh_current()

    def drain_events(self):
        changed = False
        while True:
            try:
                text = self.events.get_nowait()
            except queue.Empty:
                break
            self.connections.append_event(text)
            self.statusBar().showMessage(text, 8000)
            changed = True
        if changed:
            self.dashboard.refresh(); self.connections.refresh(); self.refresh_runtime_badge()

    def refresh_current(self):
        page = self.stack.currentWidget()
        if page and hasattr(page, "refresh"):
            try:
                page.refresh()
            except Exception as exc:
                self.statusBar().showMessage(f"Refresh error: {exc}", 5000)
        self.refresh_runtime_badge()

    def refresh_live_current(self):
        """Auto-refresh only lightweight views that are expected to be live.

        Repository, Git, Skills, Agents and similar pages can execute local
        discovery commands as part of refresh(), so they stay strictly
        on-demand via navigation or their explicit refresh controls.
        """
        page = self.stack.currentWidget()
        if page in (self.calls, self.approvals):
            try:
                page.refresh()
            except Exception as exc:
                self.statusBar().showMessage(f"Refresh error: {exc}", 5000)
        self.refresh_runtime_badge()

    def refresh_all(self):
        # Historical name kept for callers: deliberately refresh only the active
        # page instead of waking every repository-backed page in the application.
        self.refresh_current()

    def closeEvent(self, event: QCloseEvent):
        if self._quitting or not QSystemTrayIcon.isSystemTrayAvailable():
            self.shutdown_runtime()
            event.accept()
            return
        event.ignore(); self.hide()
        self.tray.showMessage(
            "DevMesh Studio",
            "DevMesh is still running in the system tray. Use Tray → Quit to stop the local gateway completely.",
            QSystemTrayIcon.MessageIcon.Information,
            2500,
        )

    def shutdown_runtime(self):
        if self._shutdown_done:
            return
        self._shutdown_done = True
        try:
            self.supervisor.stop()
        except Exception as exc:
            self.events.put(f"Runtime shutdown warning: {exc}")

    def quit_app(self):
        self._quitting = True
        self.shutdown_runtime()
        self.tray.hide()
        QApplication.quit()
