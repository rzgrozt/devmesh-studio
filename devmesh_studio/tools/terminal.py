from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .base import ToolContext
from devmesh_studio.core.platform import IS_WINDOWS, command_shell, join_command, process_group_kwargs, shell_argv, split_command

if not IS_WINDOWS:  # These modules do not exist on Windows.
    import pty
    import select


BLOCKED_PATTERNS = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "> /dev/sd",
    "dd if=",
    ":(){:|:&};:",
)

# Commands that are routine, repository-confined developer operations. Shell
# composition is deliberately excluded from auto-approval: pipes, redirects,
# substitutions and command chaining fall back to the normal `bash` gate.
_SAFE_SIMPLE_PROGRAMS = {
    "pwd", "ls", "tree", "find", "rg", "grep", "git", "pytest", "tox", "nox",
    "ruff", "mypy", "pyright", "basedpyright", "flake8", "pylint", "coverage",
    "python", "python3", "node", "npm", "npx", "pnpm", "yarn", "bun", "deno",
    "cargo", "rustc", "go", "make", "cmake", "ctest", "ninja", "meson",
}
_SAFE_GIT_READ_SUBCOMMANDS = {"status", "diff", "log", "show", "rev-parse", "ls-files", "grep"}
_SAFE_PYTHON_MODULES = {"pytest", "unittest", "compileall", "ruff", "mypy", "pyright", "coverage"}
_SAFE_NODE_RUN_TARGETS = {"test", "lint", "check", "typecheck", "build", "format", "fmt", "ci"}
_SAFE_MAKE_TARGETS = {"test", "tests", "lint", "check", "typecheck", "build", "format", "fmt", "verify"}
_SHELL_META = ("&&", "||", ";", "|", ">", "<", "`", "$(", "${")


def _is_safe_developer_command(argv: list[str] | None, rendered: str) -> bool:
    if any(token in rendered for token in _SHELL_META):
        return False
    try:
        parts = list(argv) if argv is not None else split_command(rendered)
    except ValueError:
        return False
    if not parts:
        return False

    program = os.path.basename(parts[0]).lower()
    if IS_WINDOWS:
        program = os.path.splitext(program)[0]
    if program not in _SAFE_SIMPLE_PROGRAMS:
        return False

    args = parts[1:]
    for arg in args:
        if arg == ".." or arg.startswith("../") or os.path.isabs(arg) or "=/" in arg or "=../" in arg:
            return False
    if program == "git":
        if not args:
            return False
        subcommand, rest = args[0], args[1:]
        if subcommand in _SAFE_GIT_READ_SUBCOMMANDS:
            return True
        if subcommand in {"add", "commit"}:
            return True
        if subcommand == "branch":
            return not any(a in {"-d", "-D", "-m", "-M", "--delete", "--move"} for a in rest)
        if subcommand in {"checkout", "switch"}:
            destructive = {"-f", "--force", "-B", "--discard-changes"}
            return "--" not in rest and not any(a in destructive for a in rest)
        if subcommand == "restore":
            return "--staged" in rest and "--worktree" not in rest
        return False
    if program in {"python", "python3"}:
        if not args:
            return False
        if args[0] == "-m":
            return len(args) >= 2 and args[1] in _SAFE_PYTHON_MODULES
        return args[0] in {"--version", "-V"}
    if program in {"npm", "pnpm", "yarn", "bun"}:
        if not args:
            return False
        if args[0] in {"test", "lint"}:
            return True
        if args[0] in {"run", "run-script"}:
            return len(args) >= 2 and args[1] in _SAFE_NODE_RUN_TARGETS
        return args[0] in {"list", "ls", "outdated", "why"}
    if program == "npx":
        return False
    if program == "cargo":
        return bool(args) and args[0] in {"test", "check", "clippy", "fmt", "build", "metadata"}
    if program == "go":
        return bool(args) and args[0] in {"test", "vet", "list", "build", "fmt"}
    if program == "make":
        return not args or all(not a.startswith("-") and a in _SAFE_MAKE_TARGETS for a in args)
    if program == "cmake":
        return bool(args) and args[0] in {"--build", "--version"}
    if program == "find":
        return not any(a in {"-delete", "-exec", "-execdir", "-ok", "-okdir"} for a in args)
    if program == "node":
        return bool(args) and args[0] in {"--version", "-v", "--check"}
    if program == "deno":
        return bool(args) and args[0] in {"check", "test", "fmt", "lint", "info", "--version"}
    if program == "rustc":
        return bool(args) and args[0] in {"--version", "-V", "--print"}
    if program == "coverage":
        if not args:
            return False
        if args[0] == "run":
            return len(args) >= 3 and args[1] == "-m" and args[2] in {"pytest", "unittest"}
        return args[0] in {"report", "html", "xml", "json", "combine", "erase", "debug"}
    if program == "meson":
        return bool(args) and args[0] in {"setup", "configure", "compile", "test", "introspect", "format"}
    if program == "ninja":
        return not args or all(not a.startswith("-") and a not in {"install", "uninstall"} for a in args)
    if program in {"ctest", "pytest", "tox", "nox", "ruff", "mypy", "pyright", "basedpyright", "flake8", "pylint", "pwd", "ls", "tree", "rg", "grep"}:
        return True
    return False


def _normalized_command(argv: list[str] | None, command: str | None) -> tuple[list[str] | None, str]:
    if argv:
        return list(argv), join_command(argv)
    if command is None:
        raise ValueError("argv or command is required")
    return None, command.strip()


@dataclass
class TerminalSession:
    id: str
    repo_id: int
    pid: int
    master_fd: int | None
    command: str
    started_at: float
    buffer: bytearray = field(default_factory=bytearray)
    lock: threading.Lock = field(default_factory=threading.Lock)
    exited: bool = False
    returncode: int | None = None
    process: subprocess.Popen[bytes] | None = None


class TerminalTools:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self.sessions: dict[str, TerminalSession] = {}
        self._session_lock = threading.RLock()

    @staticmethod
    def _dangerous(command: str) -> str | None:
        low = command.lower().strip()
        for bad in BLOCKED_PATTERNS:
            if bad in low:
                return bad
        return None

    def exec(self, actor: str, repo_id: int, *, argv: list[str] | None = None, command: str | None = None, timeout: int = 120, env: dict[str, str] | None = None) -> dict[str, Any]:
        argv_value, rendered = _normalized_command(argv, command)
        args = {"argv": argv_value, "command": command, "timeout": timeout, "env_keys": sorted((env or {}).keys())}
        with self.ctx.record(actor, repo_id, "terminal_exec", rendered, args):
            bad = self._dangerous(rendered)
            if bad:
                raise PermissionError(f"hard-denied dangerous command pattern: {bad}")
            action = "bash_safe" if _is_safe_developer_command(argv_value, rendered) else "bash"
            self.ctx.permissions.require(actor, repo_id, action, rendered, args, suggested_pattern=(rendered.split()[0] + " *") if rendered.split() else "*")
            root = self.ctx.repos.root(repo_id)
            proc_env = os.environ.copy()
            for key, value in (env or {}).items():
                if key.upper() in {"LD_PRELOAD", "PYTHONPATH"}:
                    raise PermissionError(f"environment override denied: {key}")
                proc_env[str(key)] = str(value)
            started = time.monotonic()
            try:
                if argv_value is not None:
                    proc = subprocess.run(argv_value, cwd=root, capture_output=True, text=True, timeout=min(max(timeout, 1), 900), env=proc_env, shell=False)
                else:
                    # Explicit shell mode is supported because coding workflows need
                    # pipes/redirection, but permission is evaluated on the full text.
                    proc = subprocess.run(shell_argv(command or ""), cwd=root, capture_output=True, text=True, timeout=min(max(timeout, 1), 900), env=proc_env, shell=False)
            except subprocess.TimeoutExpired as exc:
                duration = int((time.monotonic() - started) * 1000)
                out = (exc.stdout or "") + (exc.stderr or "")
                self.ctx.storage.add_terminal_history(repo_id, None, rendered, None, str(out), duration)
                raise TimeoutError(f"command timed out after {timeout}s") from exc
            duration = int((time.monotonic() - started) * 1000)
            output = (proc.stdout or "") + (proc.stderr or "")
            self.ctx.storage.add_terminal_history(repo_id, None, rendered, proc.returncode, output, duration)
            return {
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-120_000:],
                "stderr": (proc.stderr or "")[-80_000:],
                "duration_ms": duration,
            }

    def start(self, actor: str, repo_id: int, command: str = "") -> dict[str, Any]:
        rendered = command.strip() or command_shell()
        args = {"command": rendered}
        with self.ctx.record(actor, repo_id, "terminal_start", rendered, args):
            bad = self._dangerous(rendered)
            if bad:
                raise PermissionError(f"hard-denied dangerous command pattern: {bad}")
            self.ctx.permissions.require(actor, repo_id, "bash", rendered, args, suggested_pattern="*")
            root = self.ctx.repos.root(repo_id)
            sid = uuid.uuid4().hex[:12]
            if IS_WINDOWS:
                process = subprocess.Popen(
                    shell_argv(command or None),
                    cwd=root,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    **process_group_kwargs(),
                )
                session = TerminalSession(sid, repo_id, process.pid, None, rendered, time.monotonic(), process=process)
            else:
                master_fd, slave_fd = pty.openpty()
                try:
                    process = subprocess.Popen(
                        shell_argv(command or None),
                        cwd=root,
                        stdin=slave_fd,
                        stdout=slave_fd,
                        stderr=slave_fd,
                        close_fds=True,
                        **process_group_kwargs(),
                    )
                finally:
                    os.close(slave_fd)
                session = TerminalSession(sid, repo_id, process.pid, master_fd, rendered, time.monotonic(), process=process)
            with self._session_lock:
                self.sessions[sid] = session
            threading.Thread(target=self._reader, args=(session,), daemon=True).start()
            return {"session_id": sid, "pid": session.pid, "command": rendered, "terminal_mode": "pipes" if IS_WINDOWS else "pty"}

    def _reader(self, session: TerminalSession) -> None:
        if IS_WINDOWS:
            self._reader_windows(session)
            return
        assert session.master_fd is not None
        assert session.process is not None
        try:
            while True:
                ready, _, _ = select.select([session.master_fd], [], [], 0.2)
                if ready:
                    try:
                        chunk = os.read(session.master_fd, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    with session.lock:
                        session.buffer.extend(chunk)
                        if len(session.buffer) > 1_000_000:
                            del session.buffer[:-1_000_000]
                returncode = session.process.poll()
                if returncode is not None:
                    session.exited = True
                    session.returncode = returncode
                    break
        finally:
            try:
                os.close(session.master_fd)
            except OSError:
                pass
            if session.returncode is None:
                try:
                    session.returncode = session.process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    session.returncode = session.process.poll()
            session.exited = session.returncode is not None

    def _reader_windows(self, session: TerminalSession) -> None:
        process = session.process
        assert process is not None and process.stdout is not None
        try:
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                with session.lock:
                    session.buffer.extend(chunk)
                    if len(session.buffer) > 1_000_000:
                        del session.buffer[:-1_000_000]
        finally:
            session.returncode = process.wait()
            session.exited = True

    def write(self, actor: str, session_id: str, data: str) -> dict[str, Any]:
        with self._session_lock:
            s = self.sessions.get(session_id)
        if not s:
            raise KeyError("unknown terminal session")
        args = {"session_id": session_id, "bytes": len(data.encode())}
        with self.ctx.record(actor, s.repo_id, "terminal_write", session_id, args):
            self.ctx.permissions.require(actor, s.repo_id, "bash", f"terminal:{session_id}", args, suggested_pattern="*")
            if s.exited:
                raise RuntimeError("terminal session has exited")
            if s.master_fd is None:
                if not s.process or s.process.stdin is None:
                    raise RuntimeError("terminal input is unavailable")
                s.process.stdin.write(data.encode())
                s.process.stdin.flush()
            else:
                os.write(s.master_fd, data.encode())
            return {"written": len(data.encode())}

    def read(self, actor: str, session_id: str, clear: bool = True) -> dict[str, Any]:
        with self._session_lock:
            s = self.sessions.get(session_id)
        if not s:
            raise KeyError("unknown terminal session")
        args = {"session_id": session_id, "clear": clear}
        with self.ctx.record(actor, s.repo_id, "terminal_read", session_id, args):
            with s.lock:
                data = bytes(s.buffer)
                if clear:
                    s.buffer.clear()
            return {"session_id": session_id, "text": data.decode(errors="replace"), "exited": s.exited, "returncode": s.returncode}

    def kill(self, actor: str, session_id: str) -> dict[str, Any]:
        with self._session_lock:
            s = self.sessions.get(session_id)
        if not s:
            raise KeyError("unknown terminal session")
        args = {"session_id": session_id}
        with self.ctx.record(actor, s.repo_id, "terminal_kill", session_id, args):
            self.ctx.permissions.require(actor, s.repo_id, "bash", f"terminal:{session_id}", args, suggested_pattern="*")
            if not s.exited:
                try:
                    if s.process is None:
                        raise ProcessLookupError(s.pid)
                    s.process.terminate()
                except (ProcessLookupError, OSError):
                    pass
            return {"killed": True, "session_id": session_id}

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._session_lock:
            return [
                {"session_id": s.id, "repo_id": s.repo_id, "pid": s.pid, "command": s.command, "exited": s.exited, "returncode": s.returncode}
                for s in self.sessions.values()
            ]
