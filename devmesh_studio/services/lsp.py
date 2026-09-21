from __future__ import annotations

import json
import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from devmesh_studio.core.repository import RepositoryManager
from devmesh_studio.core.storage import Storage


KIND_NAMES = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
    20: "key", 21: "null", 22: "enum-member", 23: "struct", 24: "event",
    25: "operator", 26: "type-parameter",
}


DEFAULT_SERVERS: dict[str, dict[str, Any]] = {
    "python": {
        "extensions": [".py", ".pyi"],
        "candidates": [
            ["basedpyright-langserver", "--stdio"],
            ["pyright-langserver", "--stdio"],
            ["pylsp"],
            ["jedi-language-server"],
        ],
    },
    "typescript": {
        "extensions": [".ts", ".tsx", ".mts", ".cts"],
        "candidates": [["typescript-language-server", "--stdio"]],
    },
    "javascript": {
        "extensions": [".js", ".jsx", ".mjs", ".cjs"],
        "candidates": [["typescript-language-server", "--stdio"]],
    },
    "rust": {"extensions": [".rs"], "candidates": [["rust-analyzer"]]},
    "go": {"extensions": [".go"], "candidates": [["gopls"]]},
    "c": {"extensions": [".c", ".h"], "candidates": [["clangd"]]},
    "cpp": {"extensions": [".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"], "candidates": [["clangd"]]},
    "java": {"extensions": [".java"], "candidates": [["jdtls"]]},
}


class LSPError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServerSpec:
    language_id: str
    extensions: tuple[str, ...]
    command: tuple[str, ...]
    source: str = "auto"

    @property
    def executable(self) -> str:
        return self.command[0]


class LSPClient:
    """Small persistent stdio LSP client.

    It intentionally implements only standard JSON-RPC/LSP framing and has no
    dependency on a particular language-server package. One client represents
    one language server process for one registered repository.
    """

    def __init__(self, command: list[str], root: Path, language_id: str, timeout: float = 8.0):
        self.command = list(command)
        self.root = root.resolve()
        self.language_id = language_id
        self.timeout = timeout
        self._proc = subprocess.Popen(
            self.command,
            cwd=self.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env={**os.environ, "NO_COLOR": "1"},
        )
        if self._proc.stdin is None or self._proc.stdout is None:
            raise LSPError("failed to create LSP stdio pipes")
        self._write_lock = threading.Lock()
        self._state_lock = threading.Condition()
        self._responses: dict[int, dict[str, Any]] = {}
        self._notifications: list[dict[str, Any]] = []
        self._stderr_tail: list[str] = []
        self._next_id = 1
        self._closed = False
        self._opened: dict[str, tuple[int, int, str]] = {}
        self.capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}

        self._reader = threading.Thread(target=self._reader_loop, name=f"devmesh-lsp-{language_id}", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._stderr_loop, name=f"devmesh-lsp-{language_id}-stderr", daemon=True)
        self._stderr_reader.start()
        self._initialize()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc.poll() is None else None

    def alive(self) -> bool:
        return self._proc.poll() is None and not self._closed

    def _stderr_loop(self) -> None:
        stream = self._proc.stderr
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._stderr_tail.append(line)
                    del self._stderr_tail[:-40]
        except Exception:
            return

    def stderr_tail(self) -> list[str]:
        return list(self._stderr_tail)

    def _reader_loop(self) -> None:
        stream = self._proc.stdout
        assert stream is not None
        try:
            while not self._closed:
                headers: dict[str, str] = {}
                while True:
                    line = stream.readline()
                    if not line:
                        return
                    if line in (b"\r\n", b"\n"):
                        break
                    text = line.decode("ascii", errors="replace").strip()
                    if ":" in text:
                        key, value = text.split(":", 1)
                        headers[key.lower().strip()] = value.strip()
                length = int(headers.get("content-length", "0") or 0)
                if length <= 0:
                    continue
                body = stream.read(length)
                if len(body) != length:
                    return
                try:
                    msg = json.loads(body.decode("utf-8"))
                except Exception:
                    continue
                with self._state_lock:
                    if "id" in msg and ("result" in msg or "error" in msg):
                        try:
                            self._responses[int(msg["id"])] = msg
                        except (TypeError, ValueError):
                            pass
                    elif "method" in msg:
                        self._notifications.append(msg)
                        del self._notifications[:-500]
                    self._state_lock.notify_all()
        finally:
            with self._state_lock:
                self._state_lock.notify_all()

    def _send(self, payload: dict[str, Any]) -> None:
        if not self.alive():
            raise LSPError(f"language server exited with code {self._proc.poll()}: {' | '.join(self.stderr_tail()[-5:])}")
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        framed = f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw
        with self._write_lock:
            assert self._proc.stdin is not None
            self._proc.stdin.write(framed)
            self._proc.stdin.flush()

    def request(self, method: str, params: Any, timeout: float | None = None) -> Any:
        with self._state_lock:
            request_id = self._next_id
            self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + (timeout or self.timeout)
        with self._state_lock:
            while request_id not in self._responses:
                if not self.alive():
                    raise LSPError(f"language server exited while waiting for {method}: {' | '.join(self.stderr_tail()[-5:])}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"LSP request timed out: {method}")
                self._state_lock.wait(min(remaining, 0.25))
            msg = self._responses.pop(request_id)
        if "error" in msg:
            err = msg["error"]
            raise LSPError(f"{method}: {err.get('message', err)}")
        return msg.get("result")

    def notify(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def wait_notification(
        self,
        method: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        timeout: float = 0.75,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        with self._state_lock:
            while True:
                for msg in reversed(self._notifications):
                    if msg.get("method") != method:
                        continue
                    if predicate is None or predicate(msg):
                        return msg
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._state_lock.wait(min(remaining, 0.1))

    def _initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "clientInfo": {"name": "DevMesh Studio", "version": "1.2.1"},
                "rootUri": self.root.as_uri(),
                "rootPath": str(self.root),
                "workspaceFolders": [{"uri": self.root.as_uri(), "name": self.root.name}],
                "capabilities": {
                    "workspace": {"symbol": {"dynamicRegistration": False}},
                    "textDocument": {
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                        "definition": {"linkSupport": True},
                        "references": {},
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                        "publishDiagnostics": {"relatedInformation": True, "versionSupport": True},
                        "diagnostic": {"dynamicRegistration": False, "relatedDocumentSupport": True},
                        "callHierarchy": {"dynamicRegistration": False},
                        "synchronization": {"didSave": True, "willSave": False},
                    },
                },
                "initializationOptions": {},
                "trace": "off",
            },
            timeout=max(self.timeout, 15.0),
        ) or {}
        self.capabilities = result.get("capabilities") or {}
        self.server_info = result.get("serverInfo") or {}
        self.notify("initialized", {})

    def open_document(self, path: Path, language_id: str | None = None) -> str:
        path = path.resolve()
        uri = path.as_uri()
        stat = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
        stamp = (int(stat.st_mtime_ns), int(stat.st_size), str(hash(text)))
        previous = self._opened.get(uri)
        if previous is None:
            self.notify(
                "textDocument/didOpen",
                {"textDocument": {"uri": uri, "languageId": language_id or self.language_id, "version": 1, "text": text}},
            )
            self._opened[uri] = (stamp[0], stamp[1], stamp[2])
        elif previous != stamp:
            version = int(time.time_ns() % 2_000_000_000)
            self.notify(
                "textDocument/didChange",
                {"textDocument": {"uri": uri, "version": version}, "contentChanges": [{"text": text}]},
            )
            self.notify("textDocument/didSave", {"textDocument": {"uri": uri}, "text": text})
            self._opened[uri] = (stamp[0], stamp[1], stamp[2])
        return uri

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self.alive():
                try:
                    self.request("shutdown", None, timeout=2.0)
                except Exception:
                    pass
                try:
                    self.notify("exit", None)
                except Exception:
                    pass
        finally:
            self._closed = True
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()


class LSPManager:
    """Repository-scoped LSP process pool with auto-discovery and custom config."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self.repos = RepositoryManager(storage)
        self._clients: dict[tuple[int, str], LSPClient] = {}
        self._lock = threading.RLock()

    def _custom_config(self) -> dict[str, Any]:
        raw = self.storage.get_setting("lsp_servers_json", "{}") or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _normalize_command(value: Any) -> list[str]:
        if isinstance(value, str):
            return shlex.split(value)
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            return list(value)
        return []

    def specs(self) -> list[ServerSpec]:
        custom = self._custom_config()
        specs: list[ServerSpec] = []
        for language_id, default in DEFAULT_SERVERS.items():
            cfg = custom.get(language_id) if isinstance(custom.get(language_id), dict) else {}
            if cfg.get("disabled") is True:
                continue
            extensions = tuple(str(x).lower() for x in (cfg.get("extensions") or default["extensions"]))
            command = self._normalize_command(cfg.get("command"))
            source = "custom" if command else "auto"
            if not command:
                for candidate in default["candidates"]:
                    if shutil.which(candidate[0]):
                        command = list(candidate)
                        break
            if command:
                specs.append(ServerSpec(language_id, extensions, tuple(command), source))
        # Fully custom languages are allowed too.
        for language_id, cfg in custom.items():
            if language_id in DEFAULT_SERVERS or not isinstance(cfg, dict) or cfg.get("disabled") is True:
                continue
            command = self._normalize_command(cfg.get("command"))
            extensions = tuple(str(x).lower() for x in (cfg.get("extensions") or []))
            if command and extensions:
                specs.append(ServerSpec(str(language_id), extensions, tuple(command), "custom"))
        return specs

    def status(self) -> dict[str, Any]:
        custom = self._custom_config()
        rows: list[dict[str, Any]] = []
        active_by_lang: dict[str, list[dict[str, Any]]] = {}
        with self._lock:
            for (repo_id, lang), client in self._clients.items():
                active_by_lang.setdefault(lang, []).append({"repo_id": repo_id, "pid": client.pid, "alive": client.alive()})
        languages = list(DEFAULT_SERVERS)
        for lang in custom:
            if lang not in languages:
                languages.append(lang)
        detected = {spec.language_id: spec for spec in self.specs()}
        for language_id in languages:
            default = DEFAULT_SERVERS.get(language_id, {})
            cfg = custom.get(language_id) if isinstance(custom.get(language_id), dict) else {}
            spec = detected.get(language_id)
            rows.append({
                "language_id": language_id,
                "extensions": list((spec.extensions if spec else tuple(cfg.get("extensions") or default.get("extensions", [])))),
                "command": list(spec.command) if spec else self._normalize_command(cfg.get("command")),
                "available": spec is not None,
                "source": spec.source if spec else ("custom" if cfg else "auto"),
                "disabled": bool(cfg.get("disabled")),
                "active": active_by_lang.get(language_id, []),
            })
        return {"servers": rows}

    def spec_for_path(self, path: Path) -> ServerSpec | None:
        suffix = path.suffix.lower()
        for spec in self.specs():
            if suffix in spec.extensions:
                return spec
        return None

    def client_for_path(self, repo_id: int, path: Path, *, required: bool = False) -> tuple[LSPClient | None, ServerSpec | None]:
        spec = self.spec_for_path(path)
        if spec is None:
            if required:
                raise LSPError(f"no available LSP server for {path.suffix or path.name}")
            return None, None
        key = (int(repo_id), spec.language_id)
        with self._lock:
            existing = self._clients.get(key)
            if existing and existing.alive():
                return existing, spec
            if existing:
                try:
                    existing.close()
                except Exception:
                    pass
            root = self.repos.root(repo_id)
            try:
                client = LSPClient(list(spec.command), root, spec.language_id)
            except Exception:
                if required:
                    raise
                return None, spec
            self._clients[key] = client
            return client, spec

    def stop_all(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except Exception:
                pass


def path_from_uri(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path)).resolve()


def position_to_dict(pos: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(pos, dict):
        return None
    return {"line": int(pos.get("line", 0)) + 1, "character": int(pos.get("character", 0))}


def range_to_dict(rng: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(rng, dict):
        return None
    return {"start": position_to_dict(rng.get("start")), "end": position_to_dict(rng.get("end"))}


def location_to_dict(location: dict[str, Any], root: Path) -> dict[str, Any]:
    uri = location.get("uri") or location.get("targetUri")
    rng = location.get("range") or location.get("targetSelectionRange") or location.get("targetRange")
    path = path_from_uri(uri) if isinstance(uri, str) else None
    if path is not None:
        try:
            display_path = str(path.relative_to(root))
        except ValueError:
            display_path = str(path)
    else:
        display_path = str(uri or "")
    return {"path": display_path, "uri": uri, "range": range_to_dict(rng)}


def flatten_document_symbols(items: list[Any], path: str, out: list[dict[str, Any]] | None = None, container: str | None = None) -> list[dict[str, Any]]:
    out = out if out is not None else []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        # DocumentSymbol has range/selectionRange; SymbolInformation has location.
        location = item.get("location") if isinstance(item.get("location"), dict) else None
        rng = item.get("selectionRange") or item.get("range") or (location or {}).get("range")
        symbol = {
            "name": item.get("name", ""),
            "kind": KIND_NAMES.get(int(item.get("kind", 0) or 0), str(item.get("kind", "unknown"))),
            "path": path,
            "range": range_to_dict(rng),
            "container": item.get("containerName") or container,
            "detail": item.get("detail"),
        }
        if symbol["range"] and symbol["range"]["start"]:
            symbol["line"] = symbol["range"]["start"]["line"]
        out.append(symbol)
        children = item.get("children")
        if isinstance(children, list):
            flatten_document_symbols(children, path, out, item.get("name") or container)
    return out


def normalize_hover(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"contents": "", "range": None}
    contents = result.get("contents")
    if isinstance(contents, str):
        text = contents
    elif isinstance(contents, dict):
        text = str(contents.get("value") or contents.get("language") or contents)
    elif isinstance(contents, list):
        parts = []
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("value") or item))
        text = "\n\n".join(parts)
    else:
        text = ""
    return {"contents": text, "range": range_to_dict(result.get("range"))}
