from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .paths import db_path


SCHEMA = r"""
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER,
    action TEXT NOT NULL,
    pattern TEXT NOT NULL DEFAULT '*',
    effect TEXT NOT NULL CHECK(effect IN ('allow','ask','deny')),
    priority INTEGER NOT NULL DEFAULT 0,
    UNIQUE(repo_id, action, pattern),
    FOREIGN KEY(repo_id) REFERENCES repositories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    resolved_at INTEGER,
    actor TEXT NOT NULL,
    repo_id INTEGER,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    args_json TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved_once','approved_always','denied','consumed')),
    suggested_pattern TEXT,
    FOREIGN KEY(repo_id) REFERENCES repositories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    actor TEXT NOT NULL,
    repo_id INTEGER,
    tool TEXT NOT NULL,
    resource TEXT,
    args_hash TEXT,
    args_json TEXT,
    result_json TEXT,
    status TEXT NOT NULL,
    duration_ms INTEGER,
    summary TEXT,
    FOREIGN KEY(repo_id) REFERENCES repositories(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS patches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    repo_id INTEGER NOT NULL,
    actor TEXT NOT NULL,
    tool TEXT NOT NULL,
    patch TEXT NOT NULL,
    changed_paths TEXT NOT NULL,
    reverted INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(repo_id) REFERENCES repositories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS terminal_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    repo_id INTEGER NOT NULL,
    session_id TEXT,
    command TEXT NOT NULL,
    returncode INTEGER,
    output_tail TEXT,
    duration_ms INTEGER,
    FOREIGN KEY(repo_id) REFERENCES repositories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    transport TEXT NOT NULL CHECK(transport IN ('stdio','http')),
    command TEXT,
    args_json TEXT,
    url TEXT,
    env_json TEXT,
    allowed_tools_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_name TEXT,
    redirect_uris TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    scope TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    scope TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pending_auth (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
"""


class Storage:
    def __init__(self, path: str | Path | None = None):
        self.path = str(Path(path or db_path()).expanduser().resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.init()
        self._secure_database_files()

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

    def init(self) -> None:
        with self.conn() as db:
            db.executescript(SCHEMA)
            self._ensure_schema_columns(db)
        self._ensure_defaults()

    def _secure_database_files(self) -> None:
        """Keep OAuth material and downstream credentials private on shared hosts."""
        if os.name == "nt":
            return
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(self.path + suffix)
            if candidate.exists():
                try:
                    candidate.chmod(0o600)
                except OSError:
                    pass

    @staticmethod
    def _ensure_schema_columns(db: sqlite3.Connection) -> None:
        """Apply additive schema upgrades for existing DevMesh databases."""
        columns = {row["name"] for row in db.execute("PRAGMA table_info(tool_calls)").fetchall()}
        if "args_json" not in columns:
            db.execute("ALTER TABLE tool_calls ADD COLUMN args_json TEXT")
        if "result_json" not in columns:
            db.execute("ALTER TABLE tool_calls ADD COLUMN result_json TEXT")

    @staticmethod
    def sha(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()

    def _ensure_defaults(self) -> None:
        defaults = {
            "local_host": "127.0.0.1",
            "local_port": "8000",
            "public_url": "http://127.0.0.1:8000",
            "username": "devmesh",
            "access_token_minutes": "60",
            "refresh_token_days": "3650",
            "oauth_profile_version": "0",
            "auto_tunnel": "1",
            "tunnel_mode": "tailscale",
            "named_tunnel_hostname": "",
            "tunnel_profile_version": "0",
            "permission_profile_version": "0",
            "lsp_servers_json": "{}",
        }
        with self.conn() as db:
            for k, v in defaults.items():
                db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        self._upgrade_tunnel_profile()
        self._upgrade_permission_profile()
        self._upgrade_oauth_profile()

    def _upgrade_oauth_profile(self) -> None:
        """Move legacy short-lived rotating sessions to durable reusable sessions."""
        try:
            version = int(self.get_setting("oauth_profile_version", "0") or 0)
        except ValueError:
            version = 0
        if version >= 1:
            return
        with self.conn() as db:
            # Only rewrite the shipped legacy values; preserve explicit user choices.
            db.execute("UPDATE settings SET value='60' WHERE key='access_token_minutes' AND value='15'")
            db.execute("UPDATE settings SET value='3650' WHERE key='refresh_token_days' AND value='30'")
            db.execute(
                "INSERT INTO settings(key,value) VALUES('oauth_profile_version','1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )

    def _upgrade_tunnel_profile(self) -> None:
        """Migrate legacy Cloudflare tunnel modes to Tailscale Funnel once.

        Tailscale Funnel gives DevMesh a stable ``*.ts.net`` HTTPS hostname that
        survives application and machine restarts. Cloudflare Quick Tunnel stays
        available as an explicit temporary fallback, but existing installations
        are moved to the stable provider by default.
        """
        try:
            version = int(self.get_setting("tunnel_profile_version", "0") or 0)
        except ValueError:
            version = 0
        if version >= 1:
            return
        with self.conn() as db:
            db.execute(
                "INSERT INTO settings(key,value) VALUES('tunnel_mode','tailscale') ON CONFLICT(key) DO UPDATE SET value='tailscale'"
            )
            db.execute(
                "INSERT INTO settings(key,value) VALUES('named_tunnel_hostname','') ON CONFLICT(key) DO UPDATE SET value=''"
            )
            db.execute(
                "INSERT INTO settings(key,value) VALUES('tunnel_profile_version','1') ON CONFLICT(key) DO UPDATE SET value='1'"
            )

    def _upgrade_permission_profile(self) -> None:
        """Apply one-time safe-default permission upgrades without clobbering later user choices."""
        try:
            version = int(self.get_setting("permission_profile_version", "0") or 0)
        except ValueError:
            version = 0
        if version >= 3:
            return
        newly_safe = ["git_stage", "git_commit", "git_branch", "git_checkout"]
        if version < 2:
            newly_safe = ["read", "list", "glob", "grep", "git_read", "code_intel", "skill", "edit", "task", "bash_safe", *newly_safe]
        with self.conn() as db:
            repo_ids = [int(row["id"]) for row in db.execute("SELECT id FROM repositories").fetchall()]
            for repo_id in repo_ids:
                for action in newly_safe:
                    db.execute(
                        """INSERT INTO permissions(repo_id,action,pattern,effect,priority) VALUES(?,?,'*','allow',0)
                           ON CONFLICT(repo_id,action,pattern) DO UPDATE SET effect='allow',priority=0""",
                        (repo_id, action),
                    )
                for action, effect in (("bash", "ask"), ("git_write", "allow"), ("git_restore", "ask"), ("git_history_rewrite", "ask"), ("git_push", "ask"), ("git_force_push", "deny"), ("agent", "ask"), ("mcp", "ask"), ("external_directory", "deny")):
                    db.execute(
                        """INSERT OR IGNORE INTO permissions(repo_id,action,pattern,effect,priority) VALUES(?,?,'*',?,0)""",
                        (repo_id, action, effect),
                    )
                # Migrate only the exact shipped v2 wildcard defaults.
                db.execute(
                    "UPDATE permissions SET effect='allow' WHERE repo_id=? AND action='git_write' AND pattern='*' AND effect='ask' AND priority=0",
                    (repo_id,),
                )
                db.execute(
                    "UPDATE permissions SET effect='ask' WHERE repo_id=? AND action='git_push' AND pattern='*' AND effect='deny' AND priority=0",
                    (repo_id,),
                )
            # Old pending prompts for actions that are now explicitly safe are stale.
            db.execute(
                f"""UPDATE approvals SET status='consumed', resolved_at=?
                    WHERE status='pending' AND action IN ({','.join('?' for _ in newly_safe + ['git_write'])})""",
                (int(time.time()), *newly_safe, "git_write"),
            )
            db.execute(
                "INSERT INTO settings(key,value) VALUES('permission_profile_version','3') ON CONFLICT(key) DO UPDATE SET value='3'"
            )

    # settings -----------------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.conn() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.conn() as db:
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def settings_dict(self) -> dict[str, str]:
        with self.conn() as db:
            rows = db.execute("SELECT key,value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # repositories --------------------------------------------------------------
    def add_repository(self, path: str | Path, name: str | None = None) -> dict[str, Any]:
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            raise ValueError(f"repository path is not a directory: {p}")
        now = int(time.time())
        with self.conn() as db:
            db.execute(
                "INSERT OR IGNORE INTO repositories(name,path,enabled,created_at) VALUES(?,?,1,?)",
                (name or p.name, str(p), now),
            )
            row = db.execute("SELECT * FROM repositories WHERE path=?", (str(p),)).fetchone()
        assert row
        repo = dict(row)
        self.ensure_repo_permissions(repo["id"])
        return repo

    def ensure_repo_permissions(self, repo_id: int) -> None:
        # Productive coding defaults: repository-confined reads, edits, detected tasks,
        # code intelligence and known-safe terminal commands do not interrupt the user.
        # Open-ended shell, destructive Git operations, pushes, agent delegation and
        # external integrations remain gated because their effects are harder to bound.
        defaults = [
            ("read", "*", "allow", 0),
            ("read", "*.env", "ask", 100),
            ("read", "*.env.*", "ask", 100),
            ("read", "*.env.example", "allow", 110),
            ("list", "*", "allow", 0),
            ("glob", "*", "allow", 0),
            ("grep", "*", "allow", 0),
            ("git_read", "*", "allow", 0),
            ("code_intel", "*", "allow", 0),
            ("skill", "*", "allow", 0),
            ("edit", "*", "allow", 0),
            ("task", "*", "allow", 0),
            ("bash_safe", "*", "allow", 0),
            ("bash", "*", "ask", 0),
            ("git_write", "*", "allow", 0),
            ("git_stage", "*", "allow", 0),
            ("git_commit", "*", "allow", 0),
            ("git_branch", "*", "allow", 0),
            ("git_checkout", "*", "allow", 0),
            ("git_restore", "*", "ask", 0),
            ("git_history_rewrite", "*", "ask", 0),
            ("git_push", "*", "ask", 0),
            ("git_force_push", "*", "deny", 0),
            ("agent", "*", "ask", 0),
            ("mcp", "*", "ask", 0),
            ("external_directory", "*", "deny", 0),
        ]
        with self.conn() as db:
            for action, pattern, effect, priority in defaults:
                db.execute(
                    "INSERT OR IGNORE INTO permissions(repo_id,action,pattern,effect,priority) VALUES(?,?,?,?,?)",
                    (repo_id, action, pattern, effect, priority),
                )

    def list_repositories(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM repositories"
        args: tuple[Any, ...] = ()
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY name COLLATE NOCASE"
        with self.conn() as db:
            rows = db.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def get_repository(self, repo_id: int | str) -> dict[str, Any] | None:
        with self.conn() as db:
            if isinstance(repo_id, int) or str(repo_id).isdigit():
                row = db.execute("SELECT * FROM repositories WHERE id=?", (int(repo_id),)).fetchone()
            else:
                row = db.execute("SELECT * FROM repositories WHERE name=? OR path=?", (str(repo_id), str(repo_id))).fetchone()
        return dict(row) if row else None

    def remove_repository(self, repo_id: int) -> None:
        with self.conn() as db:
            db.execute("DELETE FROM repositories WHERE id=?", (repo_id,))

    def set_repository_enabled(self, repo_id: int, enabled: bool) -> None:
        with self.conn() as db:
            db.execute("UPDATE repositories SET enabled=? WHERE id=?", (1 if enabled else 0, repo_id))

    # permissions ---------------------------------------------------------------
    def list_permissions(self, repo_id: int) -> list[dict[str, Any]]:
        with self.conn() as db:
            rows = db.execute(
                "SELECT * FROM permissions WHERE repo_id=? ORDER BY action,priority, id", (repo_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_permission(self, repo_id: int, action: str, pattern: str, effect: str, priority: int = 0) -> None:
        if effect not in {"allow", "ask", "deny"}:
            raise ValueError("effect must be allow, ask or deny")
        with self.conn() as db:
            db.execute(
                """INSERT INTO permissions(repo_id,action,pattern,effect,priority) VALUES(?,?,?,?,?)
                   ON CONFLICT(repo_id,action,pattern) DO UPDATE SET effect=excluded.effect, priority=excluded.priority""",
                (repo_id, action, pattern, effect, priority),
            )

    # approvals -----------------------------------------------------------------
    def create_approval(self, actor: str, repo_id: int | None, action: str, resource: str, args: Any, suggested_pattern: str | None = None) -> int:
        raw = json.dumps(args, sort_keys=True, default=str)
        h = self.sha(raw)
        with self.conn() as db:
            existing = db.execute(
                """SELECT id FROM approvals WHERE status='pending' AND actor=? AND repo_id IS ? AND action=? AND resource=? AND args_hash=? ORDER BY id DESC LIMIT 1""",
                (actor, repo_id, action, resource, h),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cur = db.execute(
                """INSERT INTO approvals(created_at,actor,repo_id,action,resource,args_json,args_hash,status,suggested_pattern)
                   VALUES(?,?,?,?,?,?,?,'pending',?)""",
                (int(time.time()), actor, repo_id, action, resource, raw, h, suggested_pattern),
            )
            return int(cur.lastrowid)

    def list_approvals(self, status: str | None = "pending") -> list[dict[str, Any]]:
        with self.conn() as db:
            if status:
                rows = db.execute("SELECT * FROM approvals WHERE status=? ORDER BY id DESC", (status,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM approvals ORDER BY id DESC LIMIT 500").fetchall()
        return [dict(r) for r in rows]

    def get_approval(self, approval_id: int) -> dict[str, Any] | None:
        with self.conn() as db:
            row = db.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["args"] = json.loads(data.get("args_json") or "{}")
        except json.JSONDecodeError:
            data["args"] = data.get("args_json")
        return data

    def dashboard_counts(self) -> dict[str, int]:
        with self.conn() as db:
            return {
                "calls": int(db.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]),
                "patches": int(db.execute("SELECT COUNT(*) FROM patches").fetchone()[0]),
                "commands": int(db.execute("SELECT COUNT(*) FROM terminal_history").fetchone()[0]),
            }

    def resolve_approval(self, approval_id: int, status: str) -> None:
        if status not in {"approved_once", "approved_always", "denied"}:
            raise ValueError("invalid approval status")
        with self.conn() as db:
            row = db.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if not row:
                raise KeyError("approval not found")
            db.execute("UPDATE approvals SET status=?,resolved_at=? WHERE id=?", (status, int(time.time()), approval_id))
            if status == "approved_always" and row["repo_id"] is not None:
                pattern = row["suggested_pattern"] or row["resource"] or "*"
                db.execute(
                    """INSERT INTO permissions(repo_id,action,pattern,effect,priority) VALUES(?,?,?,?,80)
                       ON CONFLICT(repo_id,action,pattern) DO UPDATE SET effect='allow',priority=80""",
                    (row["repo_id"], row["action"], pattern, "allow"),
                )

    def consume_matching_approval(self, actor: str, repo_id: int | None, action: str, resource: str, args: Any) -> str | None:
        h = self.sha(json.dumps(args, sort_keys=True, default=str))
        with self.conn() as db:
            row = db.execute(
                """SELECT * FROM approvals WHERE actor=? AND repo_id IS ? AND action=? AND resource=? AND args_hash=?
                   AND status IN ('approved_once','denied') ORDER BY id DESC LIMIT 1""",
                (actor, repo_id, action, resource, h),
            ).fetchone()
            if not row:
                return None
            if row["status"] == "approved_once":
                db.execute("UPDATE approvals SET status='consumed' WHERE id=?", (row["id"],))
                return "allow"
            return "deny"

    # call history --------------------------------------------------------------
    def begin_call(self, actor: str, repo_id: int | None, tool: str, resource: str, args: Any) -> int:
        raw = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
        with self.conn() as db:
            cur = db.execute(
                "INSERT INTO tool_calls(started_at,actor,repo_id,tool,resource,args_hash,args_json,status) VALUES(?,?,?,?,?,?,?,?)",
                (int(time.time()), actor, repo_id, tool, resource, self.sha(raw), raw, "running"),
            )
            return int(cur.lastrowid)

    def set_call_result(self, call_id: int, result: Any) -> None:
        """Persist a bounded structured result for Live Calls inspection."""
        raw = json.dumps(result, sort_keys=True, default=str, ensure_ascii=False)
        max_chars = 2_000_000
        if len(raw) > max_chars:
            raw = json.dumps(
                {
                    "_truncated": True,
                    "_original_chars": len(raw),
                    "preview": raw[:max_chars],
                },
                ensure_ascii=False,
            )
        with self.conn() as db:
            db.execute("UPDATE tool_calls SET result_json=? WHERE id=?", (raw, call_id))

    def end_call(self, call_id: int, status: str, summary: str, duration_ms: int) -> None:
        with self.conn() as db:
            db.execute(
                "UPDATE tool_calls SET ended_at=?,status=?,summary=?,duration_ms=? WHERE id=?",
                (int(time.time()), status, summary[:2000], duration_ms, call_id),
            )

    def list_calls(self, limit: int = 200, repo_id: int | None = None) -> list[dict[str, Any]]:
        # Keep timeline reads intentionally light: large args/results are fetched
        # only when the user selects one call in Live Calls.
        fields = "c.id,c.started_at,c.ended_at,c.actor,c.repo_id,c.tool,c.resource,c.args_hash,c.status,c.duration_ms,c.summary,r.name repo_name"
        with self.conn() as db:
            if repo_id is None:
                rows = db.execute(
                    f"SELECT {fields} FROM tool_calls c LEFT JOIN repositories r ON r.id=c.repo_id ORDER BY c.id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = db.execute(
                    f"SELECT {fields} FROM tool_calls c LEFT JOIN repositories r ON r.id=c.repo_id WHERE c.repo_id=? ORDER BY c.id DESC LIMIT ?",
                    (repo_id, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_call(self, call_id: int) -> dict[str, Any] | None:
        with self.conn() as db:
            row = db.execute(
                "SELECT c.*,r.name repo_name FROM tool_calls c LEFT JOIN repositories r ON r.id=c.repo_id WHERE c.id=?",
                (call_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        for source, target in (("args_json", "args"), ("result_json", "result")):
            raw = data.get(source)
            try:
                data[target] = json.loads(raw) if raw else None
            except (TypeError, json.JSONDecodeError):
                data[target] = raw
        return data

    def related_patch_for_call(self, call: dict[str, Any]) -> dict[str, Any] | None:
        result = call.get("result")
        if isinstance(result, dict) and result.get("patch_id") is not None:
            return self.get_patch(int(result["patch_id"]))
        if call.get("repo_id") is None or call.get("tool") not in {"fs_write", "fs_edit", "patch_apply", "patch_revert"}:
            return None
        with self.conn() as db:
            row = db.execute(
                """SELECT * FROM patches
                   WHERE repo_id=? AND actor=? AND ABS(created_at-?)<=2
                   ORDER BY ABS(created_at-?), id DESC LIMIT 1""",
                (call["repo_id"], call["actor"], call["started_at"], call["started_at"]),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["changed_paths"] = json.loads(data["changed_paths"])
        return data

    def related_terminal_for_call(self, call: dict[str, Any]) -> dict[str, Any] | None:
        if call.get("repo_id") is None or call.get("tool") != "terminal_exec":
            return None
        with self.conn() as db:
            row = db.execute(
                """SELECT t.*,r.name repo_name FROM terminal_history t
                   JOIN repositories r ON r.id=t.repo_id
                   WHERE t.repo_id=? AND ABS(t.created_at-?)<=3
                   ORDER BY ABS(t.created_at-?), t.id DESC LIMIT 1""",
                (call["repo_id"], call["started_at"], call["started_at"]),
            ).fetchone()
        return dict(row) if row else None

    # patches -------------------------------------------------------------------
    def add_patch(self, repo_id: int, actor: str, tool: str, patch: str, changed_paths: list[str]) -> int:
        with self.conn() as db:
            cur = db.execute(
                "INSERT INTO patches(created_at,repo_id,actor,tool,patch,changed_paths) VALUES(?,?,?,?,?,?)",
                (int(time.time()), repo_id, actor, tool, patch, json.dumps(changed_paths)),
            )
            return int(cur.lastrowid)

    def list_patches(self, limit: int = 200, repo_id: int | None = None) -> list[dict[str, Any]]:
        with self.conn() as db:
            if repo_id is None:
                rows = db.execute(
                    "SELECT p.*,r.name repo_name FROM patches p JOIN repositories r ON r.id=p.repo_id ORDER BY p.id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT p.*,r.name repo_name FROM patches p JOIN repositories r ON r.id=p.repo_id WHERE p.repo_id=? ORDER BY p.id DESC LIMIT ?",
                    (repo_id, limit),
                ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["changed_paths"] = json.loads(d["changed_paths"])
            out.append(d)
        return out

    def get_patch(self, patch_id: int) -> dict[str, Any] | None:
        with self.conn() as db:
            row = db.execute("SELECT * FROM patches WHERE id=?", (patch_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["changed_paths"] = json.loads(d["changed_paths"])
        return d

    def mark_patch_reverted(self, patch_id: int) -> None:
        with self.conn() as db:
            db.execute("UPDATE patches SET reverted=1 WHERE id=?", (patch_id,))

    # terminal ------------------------------------------------------------------
    def add_terminal_history(self, repo_id: int, session_id: str | None, command: str, returncode: int | None, output_tail: str, duration_ms: int) -> None:
        with self.conn() as db:
            db.execute(
                "INSERT INTO terminal_history(created_at,repo_id,session_id,command,returncode,output_tail,duration_ms) VALUES(?,?,?,?,?,?,?)",
                (int(time.time()), repo_id, session_id, command, returncode, output_tail[-12000:], duration_ms),
            )

    def list_terminal_history(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.conn() as db:
            rows = db.execute(
                "SELECT t.*,r.name repo_name FROM terminal_history t JOIN repositories r ON r.id=t.repo_id ORDER BY t.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # downstream MCP ------------------------------------------------------------
    def add_mcp_server(self, name: str, transport: str, *, command: str | None = None, args: list[str] | None = None, url: str | None = None, env: dict[str, str] | None = None, allowed_tools: list[str] | None = None) -> int:
        if transport not in {"stdio", "http"}:
            raise ValueError("transport must be stdio or http")
        with self.conn() as db:
            cur = db.execute(
                """INSERT INTO mcp_servers(name,transport,command,args_json,url,env_json,allowed_tools_json,enabled,created_at)
                   VALUES(?,?,?,?,?,?,?,1,?)""",
                (name, transport, command, json.dumps(args or []), url, json.dumps(env or {}), json.dumps(allowed_tools or []), int(time.time())),
            )
            return int(cur.lastrowid)

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        with self.conn() as db:
            rows = db.execute("SELECT * FROM mcp_servers ORDER BY name").fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["args"] = json.loads(d.pop("args_json") or "[]")
            d["env"] = json.loads(d.pop("env_json") or "{}")
            d["allowed_tools"] = json.loads(d.pop("allowed_tools_json") or "[]")
            out.append(d)
        return out

    def get_mcp_server(self, server_id: int | str) -> dict[str, Any] | None:
        rows = self.list_mcp_servers()
        for row in rows:
            if str(row["id"]) == str(server_id) or row["name"] == str(server_id):
                return row
        return None

    def update_mcp_server(self, server_id: int, name: str, transport: str, *, command: str | None = None, args: list[str] | None = None, url: str | None = None, env: dict[str, str] | None = None, allowed_tools: list[str] | None = None, enabled: bool = True) -> None:
        if transport not in {"stdio", "http"}:
            raise ValueError("transport must be stdio or http")
        with self.conn() as db:
            db.execute(
                """UPDATE mcp_servers SET name=?,transport=?,command=?,args_json=?,url=?,env_json=?,allowed_tools_json=?,enabled=? WHERE id=?""",
                (name, transport, command, json.dumps(args or []), url, json.dumps(env or {}), json.dumps(allowed_tools or []), 1 if enabled else 0, server_id),
            )

    def remove_mcp_server(self, server_id: int) -> None:
        with self.conn() as db:
            db.execute("DELETE FROM mcp_servers WHERE id=?", (server_id,))

    # oauth ---------------------------------------------------------------------
    def register_client(self, client_id: str, name: str, redirect_uris: list[str]) -> None:
        with self.conn() as db:
            db.execute("INSERT OR REPLACE INTO oauth_clients VALUES (?,?,?,?)", (client_id, name, json.dumps(redirect_uris), int(time.time())))

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        with self.conn() as db:
            row = db.execute("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["redirect_uris"] = json.loads(d["redirect_uris"])
        return d

    def save_pending(self, pending_id: str, payload: dict[str, Any], expires_at: int) -> None:
        with self.conn() as db:
            db.execute("INSERT OR REPLACE INTO pending_auth VALUES (?,?,?)", (pending_id, json.dumps(payload), expires_at))

    def pop_pending(self, pending_id: str) -> dict[str, Any] | None:
        now = int(time.time())
        with self.conn() as db:
            row = db.execute("SELECT * FROM pending_auth WHERE id=?", (pending_id,)).fetchone()
            db.execute("DELETE FROM pending_auth WHERE id=?", (pending_id,))
        if not row or row["expires_at"] < now:
            return None
        return json.loads(row["payload"])

    def save_code(self, code: str, client_id: str, redirect_uri: str, scope: str, challenge: str, ttl: int = 300) -> None:
        with self.conn() as db:
            db.execute(
                "INSERT INTO auth_codes VALUES (?,?,?,?,?,?,0)",
                (self.sha(code), client_id, redirect_uri, scope, challenge, int(time.time()) + ttl),
            )

    def consume_code(self, code: str) -> dict[str, Any] | None:
        key = self.sha(code)
        now = int(time.time())
        with self.conn() as db:
            row = db.execute("SELECT * FROM auth_codes WHERE code_hash=?", (key,)).fetchone()
            if not row or row["used"] or row["expires_at"] < now:
                return None
            db.execute("UPDATE auth_codes SET used=1 WHERE code_hash=?", (key,))
        return dict(row)

    def save_refresh(self, token: str, client_id: str, subject: str, scope: str, expires_at: int) -> None:
        with self.conn() as db:
            db.execute("INSERT INTO refresh_tokens VALUES (?,?,?,?,?,0)", (self.sha(token), client_id, subject, scope, expires_at))

    def validate_refresh(self, token: str) -> dict[str, Any] | None:
        """Validate and extend a reusable refresh token without revoking it."""
        key = self.sha(token)
        now = int(time.time())
        with self.conn() as db:
            row = db.execute("SELECT * FROM refresh_tokens WHERE token_hash=?", (key,)).fetchone()
            if not row or row["revoked"] or row["expires_at"] < now:
                return None
            days = int(self.get_setting("refresh_token_days", "3650") or 3650)
            db.execute("UPDATE refresh_tokens SET expires_at=? WHERE token_hash=?", (now + days * 86400, key))
        return dict(row)

    def rotate_refresh(self, token: str) -> dict[str, Any] | None:
        """Compatibility alias for callers from the former rotation model."""
        return self.validate_refresh(token)

    def revoke_oauth_sessions(self) -> None:
        """Invalidate outstanding authorization state and refresh tokens.

        Registered OAuth clients are intentionally preserved so ChatGPT or
        another MCP host can re-authenticate without being recreated. Access
        tokens are invalidated separately by rotating the JWT signing secret.
        """
        with self.conn() as db:
            db.execute("UPDATE refresh_tokens SET revoked=1")
            db.execute("DELETE FROM auth_codes")
            db.execute("DELETE FROM pending_auth")
