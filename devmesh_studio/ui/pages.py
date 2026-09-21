from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget
)

from devmesh_studio.core.storage import Storage
from devmesh_studio.services.runtime_supervisor import RuntimeSupervisor
from devmesh_studio.tools.base import ToolContext
from devmesh_studio.tools.filesystem import FilesystemTools
from devmesh_studio.tools.git import GitTools
from devmesh_studio.tools.skills import SkillTools
from devmesh_studio.tools.terminal import TerminalTools
from devmesh_studio.tools.downstream_mcp import DownstreamMCPTools
from devmesh_studio.tools.agents import AgentTools
from devmesh_studio.tools.code_intel import CodeIntelTools
from .dialogs import CredentialsDialog, MCPServerDialog


def page_header(title: str, subtitle: str | None = None) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(4)
    heading = QLabel(title); heading.setObjectName("title")
    lay.addWidget(heading)
    if subtitle:
        sub = QLabel(subtitle); sub.setObjectName("subtitle"); sub.setWordWrap(True)
        lay.addWidget(sub)
    return lay


def panel(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(); frame.setObjectName("panel")
    lay = QVBoxLayout(frame); lay.setContentsMargins(14, 12, 14, 14); lay.setSpacing(10)
    if title:
        label = QLabel(title); label.setObjectName("sectionTitle"); lay.addWidget(label)
    return frame, lay


def make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setShowGrid(False)
    return t


def metric_card(title: str, value: str = "0", hint: str = ""):
    frame = QFrame(); frame.setObjectName("metricCard")
    lay = QVBoxLayout(frame); lay.setContentsMargins(14, 12, 14, 12); lay.setSpacing(2)
    name = QLabel(title); name.setObjectName("subtitle")
    val = QLabel(value); val.setObjectName("metric")
    lay.addWidget(name); lay.addWidget(val)
    hint_label = QLabel(hint); hint_label.setObjectName("muted"); hint_label.setWordWrap(True)
    lay.addWidget(hint_label)
    return frame, val, hint_label


def status_card(title: str, value: str, detail: str = "") -> tuple[QFrame, QLabel, QLabel]:
    frame = QFrame(); frame.setObjectName("statusCard")
    lay = QVBoxLayout(frame); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(3)
    name = QLabel(title); name.setObjectName("subtitle")
    val = QLabel(value); val.setObjectName("sectionTitle")
    detail_label = QLabel(detail); detail_label.setObjectName("muted"); detail_label.setWordWrap(True)
    lay.addWidget(name); lay.addWidget(val); lay.addWidget(detail_label)
    return frame, val, detail_label


def set_cell(table: QTableWidget, row: int, col: int, value, tooltip: str | None = None):
    item = QTableWidgetItem("" if value is None else str(value))
    if tooltip:
        item.setToolTip(tooltip)
    table.setItem(row, col, item)


class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.added = QTextCharFormat(); self.added.setForeground(QColor("#7ee787")); self.added.setBackground(QColor("#12291a"))
        self.removed = QTextCharFormat(); self.removed.setForeground(QColor("#ff7b72")); self.removed.setBackground(QColor("#31171b"))
        self.hunk = QTextCharFormat(); self.hunk.setForeground(QColor("#8ab4f8")); self.hunk.setBackground(QColor("#172235")); self.hunk.setFontWeight(QFont.Weight.DemiBold)
        self.header = QTextCharFormat(); self.header.setForeground(QColor("#c9d1d9")); self.header.setBackground(QColor("#1b222c")); self.header.setFontWeight(QFont.Weight.DemiBold)
        self.meta = QTextCharFormat(); self.meta.setForeground(QColor("#7d8996"))

    def highlightBlock(self, text: str):
        if text.startswith("diff --git") or text.startswith("--- ") or text.startswith("+++ "):
            self.setFormat(0, len(text), self.header)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self.hunk)
        elif text.startswith("+"):
            self.setFormat(0, len(text), self.added)
        elif text.startswith("-"):
            self.setFormat(0, len(text), self.removed)
        elif text.startswith("index ") or text.startswith("new file mode") or text.startswith("deleted file mode"):
            self.setFormat(0, len(text), self.meta)


def diff_stats(text: str) -> tuple[int, int, int]:
    files = additions = deletions = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return files, additions, deletions


class DashboardPage(QWidget):
    def __init__(self, storage: Storage, supervisor: RuntimeSupervisor):
        super().__init__(); self.storage=storage; self.supervisor=supervisor
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(16)
        root.addLayout(page_header("DevMesh Studio", "ChatGPT reasoning + your local developer runtime"))

        cards=QGridLayout(); cards.setHorizontalSpacing(10); cards.setVerticalSpacing(10)
        c,self.repo_metric,self.repo_hint=metric_card("Repositories", "0", "registered codebases"); cards.addWidget(c,0,0)
        c,self.call_metric,self.call_hint=metric_card("Tool calls", "0", "audited runtime actions"); cards.addWidget(c,0,1)
        c,self.patch_metric,self.patch_hint=metric_card("Patches", "0", "reversible file changes"); cards.addWidget(c,0,2)
        c,self.pending_metric,self.pending_hint=metric_card("Pending approvals", "0", "waiting for review"); cards.addWidget(c,0,3)
        root.addLayout(cards)

        runtime_frame, runtime_lay = panel("Runtime")
        controls=QHBoxLayout(); controls.setSpacing(8)
        self.status=QLabel("● stopped"); self.status.setObjectName("dangerText")
        self.url=QLineEdit(); self.url.setReadOnly(True); self.url.setPlaceholderText("Start the runtime to expose an MCP URL")
        self.start=QPushButton("Start"); self.start.setObjectName("primary")
        self.stop=QPushButton("Stop"); self.restart=QPushButton("Restart"); self.copy=QPushButton("Copy URL")
        controls.addWidget(self.status); controls.addWidget(self.url,1); controls.addWidget(self.start); controls.addWidget(self.stop); controls.addWidget(self.restart); controls.addWidget(self.copy)
        runtime_lay.addLayout(controls)
        root.addWidget(runtime_frame)

        health=QGridLayout(); health.setSpacing(10)
        c,self.active_proc,self.active_proc_detail=status_card("Commands","0","Audited terminal runs"); health.addWidget(c,0,0)
        c,self.repo_health,self.repo_health_detail=status_card("Repository health","—","Git state across codebases"); health.addWidget(c,0,1)
        c,self.approval_health,self.approval_health_detail=status_card("Approval queue","clear","No pending remote mutations"); health.addWidget(c,0,2)
        c,self.public_health,self.public_health_detail=status_card("Gateway","offline","Local + tunnel state"); health.addWidget(c,0,3)
        root.addLayout(health)

        activity_frame, activity_lay = panel("Recent activity")
        self.calls=make_table(["Repository","Tool","Resource","Status","Duration"])
        self.calls.setMinimumHeight(260); activity_lay.addWidget(self.calls)
        root.addWidget(activity_frame,1)

        self.start.clicked.connect(lambda: self._runtime("start")); self.stop.clicked.connect(lambda:self._runtime("stop")); self.restart.clicked.connect(lambda:self._runtime("restart"))
        self.copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.url.text()))

    def _runtime(self, action:str):
        try: getattr(self.supervisor, action)()
        except Exception as exc: QMessageBox.critical(self,"DevMesh",str(exc))
        self.refresh()

    def refresh(self):
        repos=self.storage.list_repositories(True); calls=self.storage.list_calls(200); approvals=self.storage.list_approvals("pending"); counts=self.storage.dashboard_counts()
        self.repo_metric.setText(str(len(repos))); self.call_metric.setText(str(counts["calls"])); self.patch_metric.setText(str(counts["patches"])); self.pending_metric.setText(str(len(approvals)))
        self.pending_hint.setText("waiting for review" if approvals else "nothing waiting")
        st=self.supervisor.status(); running=bool(st["gateway"])
        self.status.setText("● running" if running else "● stopped"); self.status.setObjectName("success" if running else "dangerText"); self.status.style().unpolish(self.status); self.status.style().polish(self.status)
        self.url.setText(st["mcp_url"] or "")
        self.public_health.setText("online" if running else "offline"); self.public_health.setObjectName("success" if running else "dangerText"); self.public_health.style().unpolish(self.public_health); self.public_health.style().polish(self.public_health)
        self.public_health_detail.setText(st.get("public_url") or st.get("local_url") or "Runtime is stopped")
        pending=len(approvals); self.approval_health.setText("clear" if not pending else f"{pending} pending"); self.approval_health_detail.setText("No pending remote mutations" if not pending else "Open Approvals to review queued actions")
        self.active_proc.setText(str(counts["commands"])); self.active_proc_detail.setText("audited terminal command" + ("s" if counts["commands"] != 1 else ""))
        self.repo_health.setText(f"{len(repos)} registered")
        self.repo_health_detail.setText("Open Repositories or Git for an on-demand working-tree check")

        recent=calls[:30]; self.calls.setRowCount(len(recent))
        for r,row in enumerate(recent):
            vals=[row.get("repo_name") or "—",row.get("tool"),row.get("resource") or "",row.get("status"),f"{row.get('duration_ms') or 0} ms"]
            for c,v in enumerate(vals): set_cell(self.calls,r,c,v)


class RepositoriesPage(QWidget):
    def __init__(self, storage: Storage):
        super().__init__(); self.storage=storage
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14)
        header=QHBoxLayout(); text=QVBoxLayout(); text.addLayout(page_header("Repositories","Registered sandbox roots available to ChatGPT and local tools")); header.addLayout(text,1)
        self.add=QPushButton("+ Add repository"); self.add.setObjectName("primary"); self.remove=QPushButton("Remove"); self.refresh_btn=QPushButton("Refresh")
        header.addWidget(self.refresh_btn); header.addWidget(self.remove); header.addWidget(self.add); root.addLayout(header)

        split=QSplitter(); split.setChildrenCollapsible(False)
        left, left_lay=panel("Codebases"); self.table=make_table(["Name","Branch","Git","Files","Path"]); left_lay.addWidget(self.table)
        right, right_lay=panel("Repository overview")
        self.repo_name=QLabel("Select a repository"); self.repo_name.setObjectName("sectionTitle"); right_lay.addWidget(self.repo_name)
        self.repo_path=QLabel("—"); self.repo_path.setObjectName("muted"); self.repo_path.setWordWrap(True); right_lay.addWidget(self.repo_path)
        summary=QGridLayout(); summary.setSpacing(8)
        c,self.branch_value,self.branch_hint=status_card("Branch","—",""); summary.addWidget(c,0,0)
        c,self.head_value,self.head_hint=status_card("HEAD","—",""); summary.addWidget(c,0,1)
        c,self.dirty_value,self.dirty_hint=status_card("Working tree","—",""); summary.addWidget(c,1,0)
        c,self.files_value,self.files_hint=status_card("Files","—",""); summary.addWidget(c,1,1)
        right_lay.addLayout(summary)
        right_lay.addWidget(QLabel("Languages / file types"))
        self.types=QPlainTextEdit(); self.types.setReadOnly(True); self.types.setMaximumHeight(170); right_lay.addWidget(self.types)
        right_lay.addWidget(QLabel("Git status"))
        self.details=QPlainTextEdit(); self.details.setReadOnly(True); right_lay.addWidget(self.details,1)
        split.addWidget(left); split.addWidget(right); split.setSizes([820,620]); root.addWidget(split,1)
        self.add.clicked.connect(self.add_repo); self.remove.clicked.connect(self.remove_repo); self.refresh_btn.clicked.connect(self.refresh); self.table.itemSelectionChanged.connect(self.show_details)

    def selected_id(self):
        rows=self.table.selectionModel().selectedRows(); return self.table.item(rows[0].row(),0).data(Qt.ItemDataRole.UserRole) if rows else None

    def add_repo(self):
        path=QFileDialog.getExistingDirectory(self,"Choose repository/codebase")
        if not path:return
        try:self.storage.add_repository(path); self.refresh()
        except Exception as exc:QMessageBox.critical(self,"DevMesh",str(exc))

    def remove_repo(self):
        rid=self.selected_id()
        if rid is None:return
        if QMessageBox.question(self,"Remove repository","Remove this repository from DevMesh? No files will be deleted.")==QMessageBox.StandardButton.Yes:
            self.storage.remove_repository(rid); self.refresh(); self.details.clear()

    def refresh(self):
        from devmesh_studio.core.repository import RepositoryManager
        mgr=RepositoryManager(self.storage); rows=self.storage.list_repositories(); self._repo_rows={r["id"]:r for r in rows}; self.table.setRowCount(len(rows))
        selected=self.selected_id()
        for i,row in enumerate(rows):
            try: info=mgr.info(row["id"])
            except Exception: info={**row,"branch":"?","head":"?","file_count":"?","dirty_files":"?"}
            name=QTableWidgetItem(str(row["name"])); name.setData(Qt.ItemDataRole.UserRole,row["id"]); self.table.setItem(i,0,name)
            git_state="not git" if not info.get("git") else ("clean" if not info.get("dirty_files") else f"{info.get('dirty_files')} changed")
            vals=[info.get("branch") or "—",git_state,info.get("file_count") or 0,row["path"]]
            for j,v in enumerate(vals,1): set_cell(self.table,i,j,v,row["path"] if j==4 else None)
            if selected==row["id"]: self.table.selectRow(i)
        if selected is None and rows: self.table.selectRow(0)

    def show_details(self):
        rid=self.selected_id()
        if rid is None:return
        from devmesh_studio.core.repository import RepositoryManager
        mgr=RepositoryManager(self.storage)
        try:
            info=mgr.info(rid); self.repo_name.setText(info.get("name") or "Repository"); self.repo_path.setText(info.get("path") or "")
            self.branch_value.setText(info.get("branch") or "—"); self.head_value.setText(info.get("head") or "—")
            dirty=int(info.get("dirty_files") or 0); self.dirty_value.setText("clean" if dirty==0 else f"{dirty} changed"); self.dirty_hint.setText("No uncommitted files" if dirty==0 else "Uncommitted working-tree changes")
            self.files_value.setText(str(info.get("file_count") or 0))
            top=info.get("top_extensions") or []
            self.types.setPlainText("\n".join(f"{x.get('extension') or '(no extension)'}    {x.get('count',0)}" for x in top[:12]) or "No file statistics available")
            status=mgr.git(rid,["status","--short","--branch"]).stdout if info.get("git") else "This directory is not a Git repository."
            self.details.setPlainText(status.strip() or "Working tree clean")
        except Exception as exc:self.details.setPlainText(str(exc))


class CallsPage(QWidget):
    def __init__(self, storage:Storage):
        super().__init__(); self.storage=storage; self._selected_call_id=None
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14)
        root.addLayout(page_header("Live Calls","Audited tool activity with inspectable inputs, results, diffs and command output"))
        top=QHBoxLayout(); self.summary=QLabel("0 calls"); self.summary.setObjectName("muted"); top.addWidget(self.summary); top.addStretch(); root.addLayout(top)

        split=QSplitter(); split.setChildrenCollapsible(False)
        timeline,timeline_lay=panel("Timeline")
        self.table=make_table(["ID","Actor","Repository","Tool","Resource","Status","Duration"])
        self.table.setColumnWidth(0,70); self.table.itemSelectionChanged.connect(self.show_call)
        timeline_lay.addWidget(self.table)

        inspector,inspector_lay=panel("Call inspector")
        cards=QGridLayout(); cards.setSpacing(8)
        c,self.tool_value,self.tool_hint=status_card("Tool","—","Select a call"); cards.addWidget(c,0,0)
        c,self.status_value,self.status_hint=status_card("Status","—",""); cards.addWidget(c,0,1)
        c,self.repo_value,self.repo_hint=status_card("Repository","—",""); cards.addWidget(c,1,0)
        c,self.duration_value,self.duration_hint=status_card("Duration","—",""); cards.addWidget(c,1,1)
        inspector_lay.addLayout(cards)

        self.tabs=QTabWidget()
        self.overview=QPlainTextEdit(); self.overview.setReadOnly(True); self.overview.setObjectName("codeView")
        self.input_view=QPlainTextEdit(); self.input_view.setReadOnly(True); self.input_view.setObjectName("codeView")
        self.result_view=QPlainTextEdit(); self.result_view.setReadOnly(True); self.result_view.setObjectName("codeView")
        self.artifact_view=QPlainTextEdit(); self.artifact_view.setReadOnly(True); self.artifact_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap); self.artifact_view.setObjectName("diffView")
        self.artifact_highlighter=DiffHighlighter(self.artifact_view.document())
        self.tabs.addTab(self.overview,"Overview"); self.tabs.addTab(self.input_view,"Input"); self.tabs.addTab(self.result_view,"Result"); self.tabs.addTab(self.artifact_view,"Diff / Output")
        inspector_lay.addWidget(self.tabs,1)

        split.addWidget(timeline); split.addWidget(inspector); split.setSizes([820,760]); root.addWidget(split,1)

    @staticmethod
    def _pretty(value) -> str:
        if value is None:
            return "No payload was stored for this call."
        if isinstance(value,str):
            return value
        return json.dumps(value,indent=2,ensure_ascii=False,sort_keys=True,default=str)

    def selected_id(self):
        rows=self.table.selectionModel().selectedRows()
        if not rows:return None
        item=self.table.item(rows[0].row(),0)
        return int(item.data(Qt.ItemDataRole.UserRole) or item.text()) if item else None

    def refresh(self):
        selected=self.selected_id() or self._selected_call_id
        rows=self.storage.list_calls(500); self.summary.setText(f"{len(rows)} recent calls")
        self.table.blockSignals(True); self.table.setRowCount(len(rows)); selected_row=None
        for i,row in enumerate(rows):
            first=QTableWidgetItem(str(row["id"])); first.setData(Qt.ItemDataRole.UserRole,row["id"]); self.table.setItem(i,0,first)
            vals=[row["actor"],row.get("repo_name") or "—",row["tool"],row.get("resource") or "",row["status"],f"{row.get('duration_ms') or 0} ms"]
            for j,v in enumerate(vals,1):set_cell(self.table,i,j,v)
            if selected==row["id"]:selected_row=i
        self.table.blockSignals(False)
        if selected_row is not None:self.table.selectRow(selected_row)
        elif rows and selected is None:self.table.selectRow(0)
        elif self._selected_call_id:self.show_call()

    def _set_status_style(self,status:str):
        name="success" if status=="ok" else ("warning" if status=="running" else "dangerText")
        self.status_value.setObjectName(name); self.status_value.style().unpolish(self.status_value); self.status_value.style().polish(self.status_value)

    def _set_artifact(self,text:str,diff:bool=False):
        self.artifact_highlighter.setDocument(self.artifact_view.document() if diff else None)
        self.artifact_view.setPlainText(text)

    def show_call(self):
        call_id=self.selected_id()
        if call_id is None:return
        call=self.storage.get_call(call_id)
        if not call:return
        self._selected_call_id=call_id
        tool=call.get("tool") or "—"; status=call.get("status") or "—"; repo=call.get("repo_name") or "global"
        self.tool_value.setText(tool); self.tool_hint.setText(call.get("resource") or "no resource")
        self.status_value.setText(status); self.status_hint.setText(call.get("summary") or ""); self._set_status_style(status)
        self.repo_value.setText(repo); self.repo_hint.setText(f"actor: {call.get('actor') or 'unknown'}")
        self.duration_value.setText(f"{call.get('duration_ms') or 0} ms"); self.duration_hint.setText(f"call #{call_id}")

        started=datetime.fromtimestamp(call["started_at"]).strftime("%Y-%m-%d %H:%M:%S") if call.get("started_at") else "—"
        ended=datetime.fromtimestamp(call["ended_at"]).strftime("%Y-%m-%d %H:%M:%S") if call.get("ended_at") else "—"
        args=call.get("args"); result=call.get("result")
        lines=[
            f"Call ID      {call_id}",f"Tool         {tool}",f"Actor        {call.get('actor') or '—'}",
            f"Repository   {repo}",f"Resource     {call.get('resource') or '—'}",f"Status       {status}",
            f"Started      {started}",f"Ended        {ended}",f"Duration     {call.get('duration_ms') or 0} ms",
            f"Summary      {call.get('summary') or '—'}",
        ]
        if tool=="terminal_exec":
            command=(args or {}).get("command") or " ".join((args or {}).get("argv") or []) or call.get("resource") or "—"
            lines.extend(["", "Command", command, f"Timeout      {(args or {}).get('timeout','—')} s"])
            if isinstance(result,dict):lines.append(f"Exit code    {result.get('returncode','—')}")
        elif tool=="task_run" and isinstance(args,dict):
            lines.extend(["",f"Task         {args.get('name','—')}",f"Command      {' '.join(args.get('argv') or [])}"])
        elif tool.startswith("fs_") and isinstance(args,dict):
            lines.extend(["",f"Path         {args.get('path',call.get('resource') or '—')}"])
        elif tool.startswith("git_"):
            lines.extend(["",f"Git action   {tool.removeprefix('git_')}"])
        self.overview.setPlainText("\n".join(lines))
        self.input_view.setPlainText(self._pretty(args))
        self.result_view.setPlainText(self._pretty(result))

        patch=self.storage.related_patch_for_call(call)
        if patch:
            text=patch.get("patch") or ""
            files,adds,dels=diff_stats(text); paths=patch.get("changed_paths") or []
            self.tabs.setTabText(3,f"Diff · +{adds} −{dels}")
            self._set_artifact(text or "Patch contains no textual diff.",True)
            return

        if tool=="terminal_exec":
            terminal=self.storage.related_terminal_for_call(call)
            command=(args or {}).get("command") or " ".join((args or {}).get("argv") or []) or call.get("resource") or ""
            if isinstance(result,dict):
                stdout=result.get("stdout") or ""; stderr=result.get("stderr") or ""; rc=result.get("returncode")
            elif terminal:
                stdout=terminal.get("output_tail") or ""; stderr=""; rc=terminal.get("returncode")
            else:
                stdout=stderr=""; rc="—"
            body=f"$ {command}\n\nexit code: {rc}\n\nSTDOUT\n{stdout or '(empty)'}"
            if stderr:body+=f"\n\nSTDERR\n{stderr}"
            self.tabs.setTabText(3,"Command output"); self._set_artifact(body,False); return

        if tool=="task_run" and isinstance(result,dict):
            cmd=" ".join(result.get("argv") or (args or {}).get("argv") or [])
            body=f"$ {cmd}\n\nexit code: {result.get('returncode','—')}\n\nSTDOUT\n{result.get('stdout') or '(empty)'}"
            if result.get("stderr"):body+=f"\n\nSTDERR\n{result['stderr']}"
            self.tabs.setTabText(3,"Task output"); self._set_artifact(body,False); return

        if tool in {"git_diff","git_show"} and isinstance(result,dict):
            text=result.get("stdout") or result.get("diff") or "No diff output."
            self.tabs.setTabText(3,"Git diff"); self._set_artifact(text,True); return

        if tool.startswith("git_") and isinstance(result,dict):
            text=result.get("stdout") or result.get("stderr") or self._pretty(result)
            self.tabs.setTabText(3,"Git output"); self._set_artifact(text,False); return

        if tool in {"fs_read","fs_read_many","fs_grep","fs_glob","fs_tree","fs_list"} and result is not None:
            self.tabs.setTabText(3,"Tool output"); self._set_artifact(self._pretty(result),False); return

        self.tabs.setTabText(3,"Diff / Output")
        self._set_artifact("No specialized visual output is available for this call.\nUse Input and Result for the complete structured payload.",False)


class ChangesPage(QWidget):
    def __init__(self, storage:Storage):
        super().__init__(); self.storage=storage; self.ctx=ToolContext(storage); self.fs=FilesystemTools(self.ctx)
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14)
        head=QHBoxLayout(); head.addLayout(page_header("Changes","Every DevMesh edit is recorded as a reversible patch"),1); self.revert=QPushButton("Revert selected patch"); self.revert.setObjectName("danger"); head.addWidget(self.revert); root.addLayout(head)
        split=QSplitter(); split.setChildrenCollapsible(False)
        left,left_lay=panel("Patch timeline"); self.table=make_table(["ID","Repository","Tool","Paths","State"]); left_lay.addWidget(self.table)
        right,right_lay=panel("Unified diff")
        self.diff_summary=QLabel("Select a patch to inspect its changes"); self.diff_summary.setObjectName("muted"); right_lay.addWidget(self.diff_summary)
        self.diff=QPlainTextEdit(); self.diff.setReadOnly(True); self.diff.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap); self.diff.setObjectName("diffView"); right_lay.addWidget(self.diff)
        self.diff_highlighter=DiffHighlighter(self.diff.document())
        split.addWidget(left); split.addWidget(right); split.setSizes([620,930]); root.addWidget(split,1)
        self.table.itemSelectionChanged.connect(self.show_patch);self.revert.clicked.connect(self.revert_patch)
    def selected_id(self):
        rows=self.table.selectionModel().selectedRows();return int(self.table.item(rows[0].row(),0).text()) if rows else None
    def refresh(self):
        rows=self.storage.list_patches(500); self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            vals=[row["id"],row.get("repo_name") or "",row["tool"],", ".join(row["changed_paths"]),"reverted" if row["reverted"] else "applied"]
            for j,v in enumerate(vals): set_cell(self.table,i,j,v)
    def show_patch(self):
        pid=self.selected_id(); row=self.storage.get_patch(pid) if pid else None
        if not row:
            self.diff.clear(); self.diff_summary.setText("Select a patch to inspect its changes"); return
        text=row["patch"] or ""; files,adds,dels=diff_stats(text); paths=row.get("changed_paths") or []
        file_count=files or len(paths); self.diff_summary.setText(f"{file_count} file(s)  ·  +{adds}  −{dels}  ·  {row.get('tool','edit')} by {row.get('actor','unknown')}")
        self.diff.setPlainText(text)
    def revert_patch(self):
        pid=self.selected_id()
        if not pid:return
        if QMessageBox.question(self,"Revert patch",f"Reverse patch #{pid}?")==QMessageBox.StandardButton.Yes:
            try:self.fs.revert_patch("desktop",pid);self.refresh()
            except Exception as exc:QMessageBox.critical(self,"DevMesh",str(exc))


class ApprovalsPage(QWidget):
    def __init__(self, storage:Storage):
        super().__init__();self.storage=storage
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14)
        root.addLayout(page_header("Approvals","Review permission-gated actions requested remotely"))
        bar=QHBoxLayout();self.once=QPushButton("Approve once");self.once.setObjectName("primary");self.always=QPushButton("Always allow matching");self.deny=QPushButton("Deny");self.deny.setObjectName("danger")
        for b in (self.once,self.always,self.deny):bar.addWidget(b)
        bar.addStretch();root.addLayout(bar)
        split=QSplitter(Qt.Orientation.Vertical); split.setChildrenCollapsible(False)
        self.table=make_table(["ID","Actor","Repo","Action","Resource","Status"]); split.addWidget(self.table)
        detail_frame,detail_lay=panel("Request details")
        self.detail=QPlainTextEdit(); self.detail.setReadOnly(True); self.detail.setPlaceholderText("Select an approval to inspect its exact arguments and matching rule")
        detail_lay.addWidget(self.detail); split.addWidget(detail_frame); split.setSizes([480,220]); root.addWidget(split,1)
        self.once.clicked.connect(lambda:self.resolve("approved_once"));self.always.clicked.connect(lambda:self.resolve("approved_always"));self.deny.clicked.connect(lambda:self.resolve("denied"))
        self.table.itemSelectionChanged.connect(self.show_details)
    def selected_id(self):
        rows=self.table.selectionModel().selectedRows();return int(self.table.item(rows[0].row(),0).text()) if rows else None
    def refresh(self):
        rows=self.storage.list_approvals(None);self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            vals=[row["id"],row["actor"],row.get("repo_id") or "—",row["action"],row["resource"],row["status"]]
            for j,v in enumerate(vals): set_cell(self.table,i,j,v)
        self.show_details()
    def show_details(self):
        pid=self.selected_id(); row=self.storage.get_approval(pid) if pid else None
        if not row: self.detail.clear(); return
        display={"action":row["action"],"resource":row["resource"],"actor":row["actor"],"suggested_pattern":row.get("suggested_pattern"),"arguments":row.get("args")}
        self.detail.setPlainText(json.dumps(display,ensure_ascii=False,indent=2,default=str))
    def resolve(self,status):
        pid=self.selected_id()
        if pid:
            try:self.storage.resolve_approval(pid,status);self.refresh()
            except Exception as exc:QMessageBox.critical(self,"DevMesh",str(exc))


class PermissionsPage(QWidget):
    ACTIONS=["read","list","glob","grep","code_intel","skill","edit","task","bash_safe","bash","git_read","git_write","git_stage","git_commit","git_branch","git_checkout","git_restore","git_history_rewrite","git_push","git_force_push","agent","mcp","external_directory"]
    def __init__(self, storage:Storage):
        super().__init__(); self.storage=storage
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14); root.addLayout(page_header("Permissions","Repository-scoped allow / ask / deny rules; higher-priority granular rules win"))
        top=QHBoxLayout(); top.addWidget(QLabel("Repository")); self.repo=QComboBox(); top.addWidget(self.repo); top.addStretch(); root.addLayout(top)
        quick_frame,quick_lay=panel("Default policy")
        quick=QGridLayout(); self.quick_combos={}
        for i,a in enumerate(self.ACTIONS):
            quick.addWidget(QLabel(a), i//3, (i%3)*2)
            combo=QComboBox(); combo.addItems(["allow","ask","deny"]); combo.currentTextChanged.connect(lambda value,action=a:self.update_default(action,value)); self.quick_combos[a]=combo
            quick.addWidget(combo, i//3, (i%3)*2+1)
        quick_lay.addLayout(quick); root.addWidget(quick_frame)
        rules_frame,rules_lay=panel("Granular rules"); self.rules=make_table(["ID","Action","Pattern","Effect","Priority"]); rules_lay.addWidget(self.rules)
        bar=QHBoxLayout(); self.action=QComboBox(); self.action.addItems(self.ACTIONS); self.pattern=QLineEdit("*"); self.effect=QComboBox(); self.effect.addItems(["allow","ask","deny"]); self.priority=QSpinBox(); self.priority.setRange(-1000,1000); self.priority.setValue(50); self.add_rule=QPushButton("Add / update rule"); self.add_rule.setObjectName("primary")
        bar.addWidget(self.action); bar.addWidget(self.pattern,1); bar.addWidget(self.effect); bar.addWidget(self.priority); bar.addWidget(self.add_rule); rules_lay.addLayout(bar); root.addWidget(rules_frame,1)
        self.repo.currentIndexChanged.connect(self.load_rules); self.add_rule.clicked.connect(self.add_custom_rule)
    def refresh(self):
        cur=self.repo.currentData(); self.repo.blockSignals(True); self.repo.clear()
        for r in self.storage.list_repositories(): self.repo.addItem(r["name"],r["id"])
        if cur:
            idx=self.repo.findData(cur); self.repo.setCurrentIndex(max(0,idx))
        self.repo.blockSignals(False); self.load_rules()
    def load_rules(self):
        rid=self.repo.currentData()
        if not rid: self.rules.setRowCount(0); return
        rows=self.storage.list_permissions(rid); by={(r["action"],r["pattern"]):r["effect"] for r in rows}
        for a,combo in self.quick_combos.items():
            combo.blockSignals(True); combo.setCurrentText(by.get((a,"*"),"ask")); combo.blockSignals(False)
        self.rules.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,v in enumerate([r["id"],r["action"],r["pattern"],r["effect"],r["priority"]]): set_cell(self.rules,i,j,v)
    def update_default(self, action, value):
        rid=self.repo.currentData()
        if rid: self.storage.upsert_permission(rid,action,"*",value,0); self.load_rules()
    def add_custom_rule(self):
        rid=self.repo.currentData(); pattern=self.pattern.text().strip()
        if not rid or not pattern: return
        self.storage.upsert_permission(rid,self.action.currentText(),pattern,self.effect.currentText(),self.priority.value()); self.load_rules()


class TerminalPage(QWidget):
    def __init__(self, storage:Storage):
        super().__init__();self.storage=storage;self.tools=TerminalTools(ToolContext(storage))
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14); root.addLayout(page_header("Terminal","Run explicit commands locally and inspect DevMesh-managed command history"))
        bar=QHBoxLayout();self.repo=QComboBox();self.command=QLineEdit();self.command.setPlaceholderText("Command to run in the selected repository");self.run=QPushButton("Run");self.run.setObjectName("primary");bar.addWidget(self.repo);bar.addWidget(self.command,1);bar.addWidget(self.run);root.addLayout(bar)
        split=QSplitter(Qt.Orientation.Vertical); split.setChildrenCollapsible(False)
        out_frame,out_lay=panel("Output"); self.output=QPlainTextEdit();self.output.setReadOnly(True); out_lay.addWidget(self.output); split.addWidget(out_frame)
        hist_frame,hist_lay=panel("History"); self.history=make_table(["Repository","Command","Exit","Duration"]); hist_lay.addWidget(self.history); split.addWidget(hist_frame); split.setSizes([520,250]); root.addWidget(split,1);self.run.clicked.connect(self.execute)
    def refresh(self):
        cur=self.repo.currentData();self.repo.blockSignals(True);self.repo.clear()
        for r in self.storage.list_repositories(True):self.repo.addItem(r["name"],r["id"])
        if cur:
            idx=self.repo.findData(cur);self.repo.setCurrentIndex(max(0,idx))
        self.repo.blockSignals(False)
        rows=self.storage.list_terminal_history(100);self.history.setRowCount(len(rows))
        for i,r in enumerate(rows):
            vals=[r["repo_name"],r["command"],r.get("returncode"),f"{r.get('duration_ms') or 0} ms"]
            for j,v in enumerate(vals):set_cell(self.history,i,j,v)
    def execute(self):
        rid=self.repo.currentData();cmd=self.command.text().strip()
        if not rid or not cmd:return
        try:
            result=self.tools.exec("desktop",rid,command=cmd,timeout=300);self.output.setPlainText((result["stdout"] or "")+(result["stderr"] or ""));self.refresh()
        except Exception as exc:QMessageBox.critical(self,"Terminal",str(exc))


class SkillsPage(QWidget):
    def __init__(self, storage:Storage):
        super().__init__();self.storage=storage;self.tools=SkillTools(ToolContext(storage))
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14); root.addLayout(page_header("Skills","Discover SKILL.md capabilities from Codex, Claude, OpenCode and repository-local skill folders"))
        bar=QHBoxLayout();self.repo=QComboBox();bar.addWidget(QLabel("Repository"));bar.addWidget(self.repo);bar.addStretch();root.addLayout(bar)
        split=QSplitter(); split.setChildrenCollapsible(False); left,left_lay=panel("Discovered skills"); self.table=make_table(["Name","Source","Description"]); left_lay.addWidget(self.table); right,right_lay=panel("Skill instructions"); self.text=QPlainTextEdit();self.text.setReadOnly(True);right_lay.addWidget(self.text);split.addWidget(left);split.addWidget(right);split.setSizes([700,820]);root.addWidget(split,1);self.repo.currentIndexChanged.connect(self.refresh_skills);self.table.itemSelectionChanged.connect(self.show_skill)
    def refresh(self):
        cur=self.repo.currentData(); self.repo.blockSignals(True);self.repo.clear();self.repo.addItem("Global only",None)
        for r in self.storage.list_repositories(True):self.repo.addItem(r["name"],r["id"])
        if cur is not None:
            idx=self.repo.findData(cur); self.repo.setCurrentIndex(max(0,idx))
        self.repo.blockSignals(False);self.refresh_skills()
    def refresh_skills(self):
        try:rows=self.tools.list("desktop",self.repo.currentData())["skills"]
        except Exception:rows=[]
        self._rows=rows;self.table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,v in enumerate([r["name"],r["source"],r["description"]]):set_cell(self.table,i,j,v,r.get("path"))
    def show_skill(self):
        rows=self.table.selectionModel().selectedRows()
        if not rows:return
        item=self._rows[rows[0].row()]
        try:self.text.setPlainText(Path(item["path"]).read_text(encoding="utf-8",errors="replace"))
        except Exception as exc:self.text.setPlainText(str(exc))


class MCPPage(QWidget):
    def __init__(self, storage:Storage):
        super().__init__(); self.storage=storage; self.tools=DownstreamMCPTools(ToolContext(storage))
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14)
        head=QHBoxLayout(); head.addLayout(page_header("MCP Servers","Connect optional downstream MCPs and explicitly allow the exact tools DevMesh may invoke"),1)
        self.add=QPushButton("+ Add server"); self.add.setObjectName("primary"); self.edit=QPushButton("Edit"); self.remove=QPushButton("Remove"); self.test=QPushButton("Test / list tools")
        for b in (self.test,self.edit,self.remove,self.add): head.addWidget(b)
        root.addLayout(head)
        split=QSplitter(); split.setChildrenCollapsible(False); left,left_lay=panel("Configured servers"); self.table=make_table(["Name","Transport","Endpoint","Allowed tools","State"]);left_lay.addWidget(self.table);right,right_lay=panel("Discovery / diagnostics");self.details=QPlainTextEdit();self.details.setReadOnly(True);right_lay.addWidget(self.details);split.addWidget(left);split.addWidget(right);split.setSizes([850,650]);root.addWidget(split,1)
        self.add.clicked.connect(self.add_server); self.edit.clicked.connect(self.edit_server); self.remove.clicked.connect(self.remove_server); self.test.clicked.connect(self.test_server)
    def selected_id(self):
        rows=self.table.selectionModel().selectedRows(); return self.table.item(rows[0].row(),0).data(Qt.ItemDataRole.UserRole) if rows else None
    def refresh(self):
        rows=self.storage.list_mcp_servers(); self.table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            name=QTableWidgetItem(r["name"]); name.setData(Qt.ItemDataRole.UserRole,r["id"]); self.table.setItem(i,0,name)
            endpoint=(r.get("command") or r.get("url") or ""); vals=[r["transport"],endpoint,", ".join(r["allowed_tools"]),"enabled" if r["enabled"] else "disabled"]
            for j,v in enumerate(vals,1): set_cell(self.table,i,j,v)
    def add_server(self):
        d=MCPServerDialog(self)
        if d.exec()!=d.DialogCode.Accepted: return
        v=d.value()
        if not v["name"]: return
        try: self.storage.add_mcp_server(**v); self.refresh()
        except Exception as exc: QMessageBox.critical(self,"MCP",str(exc))
    def edit_server(self):
        sid=self.selected_id()
        if not sid: return
        row=self.storage.get_mcp_server(sid)
        if not row: return
        d=MCPServerDialog(self,row)
        if d.exec()!=d.DialogCode.Accepted: return
        v=d.value()
        try: self.storage.update_mcp_server(sid,**v); self.refresh()
        except Exception as exc: QMessageBox.critical(self,"MCP",str(exc))
    def remove_server(self):
        sid=self.selected_id()
        if sid and QMessageBox.question(self,"Remove MCP","Remove this downstream MCP configuration?")==QMessageBox.StandardButton.Yes:
            self.storage.remove_mcp_server(sid); self.refresh(); self.details.clear()
    def test_server(self):
        sid=self.selected_id()
        if not sid: return
        try: r=asyncio.run(self.tools.list_tools("desktop",sid)); self.details.setPlainText(json.dumps(r,indent=2,default=str))
        except Exception as exc: self.details.setPlainText(f"{type(exc).__name__}: {exc}")


class GitPage(QWidget):
    def __init__(self, storage:Storage):
        super().__init__(); self.storage=storage; self.git=GitTools(ToolContext(storage))
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14); root.addLayout(page_header("Git","Working tree, history and branches in a repository-focused desktop view"))
        bar=QHBoxLayout(); bar.addWidget(QLabel("Repository")); self.repo=QComboBox(); self.mode=QComboBox(); self.mode.addItems(["Working tree","History","Branches","Staged diff"]); self.refresh_btn=QPushButton("Refresh")
        bar.addWidget(self.repo); bar.addWidget(self.mode); bar.addWidget(self.refresh_btn); bar.addStretch(); root.addLayout(bar)
        self.branch=QLabel("—"); self.branch.setObjectName("sectionTitle"); self.state=QLabel(""); self.state.setObjectName("muted"); meta=QHBoxLayout(); meta.addWidget(QLabel("Current branch")); meta.addWidget(self.branch); meta.addSpacing(16); meta.addWidget(self.state); meta.addStretch(); root.addLayout(meta)

        split=QSplitter(); split.setChildrenCollapsible(False)
        left,left_lay=panel("Repository")
        self.items=make_table(["Item","State","Detail","Meta"]); left_lay.addWidget(self.items)
        right,right_lay=panel("Details")
        self.output=QPlainTextEdit(); self.output.setReadOnly(True); self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap); self.output.setObjectName("diffView"); right_lay.addWidget(self.output)
        self.output_highlighter=DiffHighlighter(self.output.document())
        split.addWidget(left); split.addWidget(right); split.setSizes([700,850]); root.addWidget(split,1)

        self.refresh_btn.clicked.connect(self.load_view); self.repo.currentIndexChanged.connect(self.load_view); self.mode.currentTextChanged.connect(self.load_view); self.items.itemSelectionChanged.connect(self.load_selected)

    def refresh(self):
        cur=self.repo.currentData(); self.repo.blockSignals(True); self.repo.clear()
        for r in self.storage.list_repositories(True): self.repo.addItem(r["name"],r["id"])
        if cur:
            idx=self.repo.findData(cur); self.repo.setCurrentIndex(max(0,idx))
        self.repo.blockSignals(False); self.load_view()

    def _set_headers(self, headers: list[str]):
        self.items.setColumnCount(len(headers)); self.items.setHorizontalHeaderLabels(headers); self.items.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    @staticmethod
    def _parse_change(line: str) -> tuple[str,str]:
        if line.startswith("? "):
            return "??", line[2:]
        if line.startswith("! "):
            return "!!", line[2:]
        parts=line.split()
        if line.startswith(("1 ","2 ")) and len(parts) >= 9:
            return parts[1], parts[-1]
        if line.startswith("u ") and len(parts) >= 11:
            return "UU", parts[-1]
        return "?", line

    def load_view(self):
        rid=self.repo.currentData(); self.items.clearContents(); self.items.setRowCount(0)
        if not rid: self.output.clear(); return
        try:
            status=self.git.status("desktop",rid); repo_meta=status.get("repository") or {}; self.branch.setText(repo_meta.get("branch") or "—")
            porcelain=(status.get("stdout") or "").splitlines(); dirty_lines=[line for line in porcelain if line and not line.startswith("#")]; self.state.setText("working tree clean" if not dirty_lines else f"{len(dirty_lines)} working-tree change(s)")
            mode=self.mode.currentText()
            if mode=="Working tree":
                self._set_headers(["File","State","Scope","Path"]); self.items.setRowCount(len(dirty_lines))
                for row,line in enumerate(dirty_lines):
                    code,path=self._parse_change(line); staged=code[0] != "." if len(code)>0 else False; unstaged=code[1] != "." if len(code)>1 else False
                    scope="staged + working" if staged and unstaged else ("staged" if staged else ("working tree" if unstaged else "untracked"))
                    first=QTableWidgetItem(Path(path).name); first.setData(Qt.ItemDataRole.UserRole,{"kind":"file","path":path}); self.items.setItem(row,0,first)
                    for col,val in enumerate([code,scope,path],1): set_cell(self.items,row,col,val,path)
                self.output.setPlainText("Working tree clean" if not dirty_lines else "Select a changed file to inspect its diff.")
            elif mode=="History":
                result=self.git.log("desktop",rid,100); lines=[x for x in (result.get("stdout") or "").splitlines() if x.strip()]; self._set_headers(["Commit","Author","Date","Message"]); self.items.setRowCount(len(lines))
                for row,line in enumerate(lines):
                    parts=line.split("\t",3); parts += [""]*(4-len(parts)); first=QTableWidgetItem(parts[0]); first.setData(Qt.ItemDataRole.UserRole,{"kind":"commit","ref":parts[0]}); self.items.setItem(row,0,first)
                    for col,val in enumerate(parts[1:],1): set_cell(self.items,row,col,val)
                self.output.setPlainText("Select a commit to inspect its patch and file summary.")
            elif mode=="Branches":
                result=self.git.branches("desktop",rid); lines=[x.rstrip() for x in (result.get("stdout") or "").splitlines() if x.strip()]; self._set_headers(["Branch","Current","Kind","Reference"]); self.items.setRowCount(len(lines))
                for row,line in enumerate(lines):
                    current=line.startswith("*"); name=line[2:].strip() if len(line)>2 else line.strip(); kind="remote" if name.startswith("remotes/") else "local"; first=QTableWidgetItem(name); first.setData(Qt.ItemDataRole.UserRole,{"kind":"branch","name":name}); self.items.setItem(row,0,first)
                    for col,val in enumerate(["yes" if current else "",kind,name],1): set_cell(self.items,row,col,val)
                self.output.setPlainText(f"{len(lines)} branch reference(s) visible.")
            else:
                self._set_headers(["Staged diff","","",""]); self.items.setRowCount(0); result=self.git.diff("desktop",rid,staged=True); self.output.setPlainText((result.get("stdout") or "").strip() or "No staged changes")
        except Exception as exc:
            self.output.setPlainText(f"{type(exc).__name__}: {exc}")

    def load_selected(self):
        rows=self.items.selectionModel().selectedRows()
        if not rows:return
        item=self.items.item(rows[0].row(),0); payload=item.data(Qt.ItemDataRole.UserRole) if item else None; rid=self.repo.currentData()
        if not isinstance(payload,dict) or not rid:return
        try:
            if payload.get("kind")=="file":
                result=self.git.diff("desktop",rid,path=payload["path"]); text=(result.get("stdout") or "").strip(); self.output.setPlainText(text or "No tracked diff for this path. It may be untracked or only staged.")
            elif payload.get("kind")=="commit":
                result=self.git.show("desktop",rid,payload["ref"]); self.output.setPlainText((result.get("stdout") or "").strip() or "No commit details")
            elif payload.get("kind")=="branch":
                self.output.setPlainText(f"Branch: {payload['name']}\n\nBranch switching remains an explicit Git action; this view is read-only.")
        except Exception as exc:
            self.output.setPlainText(f"{type(exc).__name__}: {exc}")


class AgentsPage(QWidget):
    def __init__(self, storage:Storage):
        super().__init__(); self.storage=storage; self.tools=AgentTools(ToolContext(storage))
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14); root.addLayout(page_header("Coding agents","Optional Codex, Claude Code and OpenCode delegates; ChatGPT can still operate DevMesh directly"))
        self.summary=QLabel(""); self.summary.setObjectName("muted"); root.addWidget(self.summary)
        self.table=make_table(["Agent","Status","Executable","Modes"]); root.addWidget(self.table,1)
    def refresh(self):
        rows=self.tools.list("desktop")["agents"]; available=sum(bool(r["available"]) for r in rows); self.summary.setText(f"{available} of {len(rows)} agent CLIs detected on PATH"); self.table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            vals=[r["label"],"available" if r["available"] else "not installed",r.get("executable") or "—",", ".join(r["modes"])]
            for j,v in enumerate(vals):set_cell(self.table,i,j,v)


class ConnectionsPage(QWidget):
    def __init__(self, storage:Storage, supervisor:RuntimeSupervisor):
        super().__init__();self.storage=storage;self.supervisor=supervisor
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14); root.addLayout(page_header("Connections & runtime","OAuth credentials, local gateway, stable public tunnel and MCP endpoint"))
        info,form_lay=panel("Connection settings")
        form=QFormLayout()
        self.username=QLineEdit(); self.username.setReadOnly(True)
        self.port=QSpinBox(); self.port.setRange(1024,65535)
        self.auto=QCheckBox("Start public tunnel automatically")
        self.tunnel_mode=QComboBox(); self.tunnel_mode.addItem("Tailscale Funnel · stable URL (recommended)","tailscale"); self.tunnel_mode.addItem("Cloudflare Quick Tunnel · temporary URL","quick")
        self.provider_state=QLabel(""); self.provider_state.setObjectName("muted"); self.provider_state.setWordWrap(True)
        self.local=QLineEdit(); self.local.setReadOnly(True)
        self.public=QLineEdit(); self.public.setReadOnly(True)
        self.mcp=QLineEdit(); self.mcp.setReadOnly(True)
        form.addRow("Login username",self.username); form.addRow("Local port",self.port); form.addRow("",self.auto)
        form.addRow("Tunnel provider",self.tunnel_mode); form.addRow("",self.provider_state)
        form.addRow("Local gateway",self.local); form.addRow("Public URL",self.public); form.addRow("ChatGPT MCP URL",self.mcp)
        form_lay.addLayout(form); root.addWidget(info)
        self.tunnel_help=QLabel("Tailscale Funnel is the default stable provider. DevMesh publishes the local gateway with `tailscale funnel --bg --https=443` and reuses this machine's stable .ts.net hostname. No tunnel token or custom domain is required."); self.tunnel_help.setObjectName("muted"); self.tunnel_help.setWordWrap(True); form_lay.addWidget(self.tunnel_help)
        bar=QHBoxLayout();self.creds=QPushButton("Change credentials");self.save=QPushButton("Save settings");self.start=QPushButton("Start");self.start.setObjectName("primary");self.stop=QPushButton("Stop");self.restart=QPushButton("Restart");self.copy=QPushButton("Copy MCP URL")
        for b in (self.creds,self.save,self.start,self.stop,self.restart,self.copy):bar.addWidget(b)
        bar.addStretch();root.addLayout(bar); log_frame,log_lay=panel("Runtime log"); self.log=QPlainTextEdit();self.log.setReadOnly(True);log_lay.addWidget(self.log);root.addWidget(log_frame,1)
        self.creds.clicked.connect(self.change_creds);self.save.clicked.connect(self.save_settings);self.start.clicked.connect(lambda:self.action("start"));self.stop.clicked.connect(lambda:self.action("stop"));self.restart.clicked.connect(lambda:self.action("restart"));self.copy.clicked.connect(lambda:QGuiApplication.clipboard().setText(self.mcp.text()));self.tunnel_mode.currentIndexChanged.connect(self.update_tunnel_help)
    def update_tunnel_help(self):
        if self.tunnel_mode.currentData()=="tailscale":
            self.tunnel_help.setText("Stable mode uses Tailscale Funnel in background mode. Install/sign in to Tailscale once and allow Funnel for this device; DevMesh then keeps the same .ts.net MCP URL across app and machine restarts.")
        else:
            self.tunnel_help.setText("Cloudflare Quick Tunnel is kept only as a temporary fallback. Its trycloudflare.com URL changes whenever the tunnel restarts, so it is not recommended for a persistent ChatGPT plugin connection.")
    def refresh(self):
        s=self.supervisor.status(); self.username.setText(self.storage.get_setting("username","") or ""); self.port.setValue(int(self.storage.get_setting("local_port","8000") or 8000)); self.auto.setChecked(self.storage.get_setting("auto_tunnel","1")=="1")
        mode=self.storage.get_setting("tunnel_mode","tailscale") or "tailscale"; idx=self.tunnel_mode.findData(mode); self.tunnel_mode.blockSignals(True); self.tunnel_mode.setCurrentIndex(max(0,idx)); self.tunnel_mode.blockSignals(False)
        if mode=="tailscale":
            available=self.supervisor.tailscale_public_url(); running=self.supervisor.tailscale_funnel_running(); self.provider_state.setText(("● Funnel active · " if running else "○ Funnel not active · ") + (available or "Tailscale hostname unavailable"))
        else:
            self.provider_state.setText("Temporary fallback; URL changes on every Cloudflare Quick Tunnel restart")
        self.local.setText(s["local_url"]); self.public.setText(s["public_url"] or ""); self.mcp.setText(s["mcp_url"] or ""); self.update_tunnel_help()
    def append_event(self,text):self.log.appendPlainText(text)
    def change_creds(self):
        d=CredentialsDialog(self.storage,self)
        if d.exec()==d.DialogCode.Accepted:QMessageBox.information(self,"DevMesh","Credentials updated. Existing OAuth sessions were revoked; reconnect DevMesh in ChatGPT to authorize again.");self.refresh()
    def save_settings(self):
        mode=self.tunnel_mode.currentData() or "tailscale"; current_mode=self.storage.get_setting("tunnel_mode","tailscale") or "tailscale"
        if self.supervisor.gateway_running() and (self.port.value()!=self.supervisor.local_port or mode!=current_mode):
            QMessageBox.warning(self,"DevMesh","Stop the runtime before changing port or tunnel provider.");return
        self.storage.set_setting("local_port",str(self.port.value())); self.storage.set_setting("auto_tunnel","1" if self.auto.isChecked() else "0"); self.storage.set_setting("tunnel_mode",mode); self.storage.set_setting("named_tunnel_hostname","")
        self.refresh()
    def action(self,name):
        try:getattr(self.supervisor,name)()
        except Exception as exc:QMessageBox.critical(self,"DevMesh",str(exc))
        self.refresh()


class LSPPage(QWidget):
    def __init__(self, storage: Storage):
        super().__init__(); self.storage=storage; self.tools=CodeIntelTools(ToolContext(storage))
        root=QVBoxLayout(self); root.setContentsMargins(24,22,24,22); root.setSpacing(14); root.addLayout(page_header("Language Servers","Semantic code intelligence over persistent repository-scoped LSP processes"))
        self.summary=QLabel(""); self.summary.setObjectName("muted"); root.addWidget(self.summary)
        frame,lay=panel("Detected servers"); self.table=make_table(["Language","Extensions","Command","State","Source","Active"]);lay.addWidget(self.table);root.addWidget(frame,1)
        config_frame,config_lay=panel("Custom configuration"); self.config=QPlainTextEdit(); self.config.setPlaceholderText('{\n  "python": {"command": ["pyright-langserver", "--stdio"], "extensions": [".py"]}\n}');self.config.setMaximumHeight(150);config_lay.addWidget(self.config)
        bar=QHBoxLayout(); self.save=QPushButton("Save configuration"); self.save.setObjectName("primary"); self.reset=QPushButton("Reset to auto-detection"); self.refresh_btn=QPushButton("Refresh")
        for b in (self.save,self.reset,self.refresh_btn): bar.addWidget(b)
        bar.addStretch();config_lay.addLayout(bar);root.addWidget(config_frame)
        self.save.clicked.connect(self.save_config); self.reset.clicked.connect(self.reset_config); self.refresh_btn.clicked.connect(self.refresh)
    def refresh(self):
        status=self.tools.lsp.status(); rows=status.get("servers",[]); available=sum(bool(r.get("available")) for r in rows); active=sum(sum(1 for a in r.get("active",[]) if a.get("alive")) for r in rows); self.summary.setText(f"{available} language servers available · {active} active processes")
        self.table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            active_text=", ".join(f"repo {a['repo_id']} · pid {a.get('pid') or '-'}" for a in r.get("active",[]) if a.get("alive")) or "—"
            vals=[r.get("language_id"),", ".join(r.get("extensions",[]))," ".join(r.get("command",[])) or "—","available" if r.get("available") else "missing",r.get("source","auto"),active_text]
            for j,v in enumerate(vals):set_cell(self.table,i,j,v)
        raw=self.storage.get_setting("lsp_servers_json","{}") or "{}"
        try: pretty=json.dumps(json.loads(raw),indent=2)
        except Exception: pretty=raw
        if not self.config.hasFocus(): self.config.setPlainText(pretty)
    def save_config(self):
        try:
            data=json.loads(self.config.toPlainText() or "{}")
            if not isinstance(data,dict): raise ValueError("configuration must be a JSON object")
            self.storage.set_setting("lsp_servers_json",json.dumps(data,separators=(",",":")))
            self.tools.lsp.stop_all(); self.refresh(); QMessageBox.information(self,"LSP","Language-server configuration saved.")
        except Exception as exc: QMessageBox.critical(self,"LSP",f"Invalid LSP configuration: {exc}")
    def reset_config(self):
        self.storage.set_setting("lsp_servers_json","{}");self.tools.lsp.stop_all();self.config.setPlainText("{}\n");self.refresh()
