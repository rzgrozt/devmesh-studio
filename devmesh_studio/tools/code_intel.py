from __future__ import annotations

import ast
import re
import time
from pathlib import Path
from typing import Any

from devmesh_studio.services.lsp import (
    LSPError,
    LSPManager,
    flatten_document_symbols,
    location_to_dict,
    normalize_hover,
    range_to_dict,
)
from .base import ToolContext


class CodeIntelTools:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self.lsp = LSPManager(ctx.storage)

    def _target(self, repo_id: int, path: str) -> tuple[Path, str]:
        target = self.ctx.repos.resolve(repo_id, path, must_exist=True)
        if not target.is_file():
            raise IsADirectoryError(target)
        return target, self.ctx.repos.relative(repo_id, target)

    @staticmethod
    def _position(line: int, character: int) -> dict[str, int]:
        if line < 1:
            raise ValueError("line is 1-based and must be >= 1")
        if character < 0:
            raise ValueError("character must be >= 0")
        return {"line": line - 1, "character": character}

    def lsp_status(self, actor: str) -> dict[str, Any]:
        args: dict[str, Any] = {}
        with self.ctx.record(actor, None, "code_lsp_status", "lsp", args):
            return self.lsp.status()

    def symbols(self, actor: str, repo_id: int, path: str) -> dict[str, Any]:
        args = {"path": path}
        with self.ctx.record(actor, repo_id, "code_symbols", path, args):
            target, rel = self._target(repo_id, path)
            self.ctx.permissions.require(actor, repo_id, "code_intel", rel, args, suggested_pattern="*")

            client, spec = self.lsp.client_for_path(repo_id, target)
            if client is not None and spec is not None:
                try:
                    uri = client.open_document(target, spec.language_id)
                    result = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}}) or []
                    return {
                        "path": rel,
                        "symbols": flatten_document_symbols(result if isinstance(result, list) else [], rel),
                        "engine": f"lsp:{spec.language_id}",
                        "server": client.server_info,
                    }
                except Exception as exc:
                    lsp_error = f"{type(exc).__name__}: {exc}"
            else:
                lsp_error = None

            text = target.read_text(encoding="utf-8", errors="replace")
            if target.suffix == ".py":
                tree = ast.parse(text)
                out = []
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        out.append({
                            "name": node.name,
                            "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                            "line": node.lineno,
                            "end_line": getattr(node, "end_lineno", None),
                            "path": rel,
                        })
                result = {"path": rel, "symbols": sorted(out, key=lambda x: x["line"]), "engine": "python-ast"}
                if lsp_error:
                    result["lsp_error"] = lsp_error
                return result

            patterns = [
                ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", re.M)),
                ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.M)),
                ("function", re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][\w]*)", re.M)),
                ("function", re.compile(r"^\s*(?:func)\s+([A-Za-z_][\w]*)", re.M)),
            ]
            out = []
            for kind, pattern in patterns:
                for m in pattern.finditer(text):
                    out.append({"name": m.group(1), "kind": kind, "line": text.count("\n", 0, m.start()) + 1, "path": rel})
            result = {"path": rel, "symbols": sorted(out, key=lambda x: x["line"]), "engine": "regex-fallback"}
            if lsp_error:
                result["lsp_error"] = lsp_error
            return result

    def hover(self, actor: str, repo_id: int, path: str, line: int, character: int = 0) -> dict[str, Any]:
        args = {"path": path, "line": line, "character": character}
        with self.ctx.record(actor, repo_id, "code_hover", path, args):
            target, rel = self._target(repo_id, path)
            self.ctx.permissions.require(actor, repo_id, "code_intel", rel, args, suggested_pattern="*")
            client, spec = self.lsp.client_for_path(repo_id, target, required=True)
            assert client is not None and spec is not None
            uri = client.open_document(target, spec.language_id)
            result = client.request("textDocument/hover", {
                "textDocument": {"uri": uri},
                "position": self._position(line, character),
            })
            return {"path": rel, **normalize_hover(result), "engine": f"lsp:{spec.language_id}", "server": client.server_info}

    def definition_at(self, actor: str, repo_id: int, path: str, line: int, character: int = 0) -> dict[str, Any]:
        args = {"path": path, "line": line, "character": character}
        with self.ctx.record(actor, repo_id, "code_definition_at", path, args):
            target, rel = self._target(repo_id, path)
            self.ctx.permissions.require(actor, repo_id, "code_intel", rel, args, suggested_pattern="*")
            client, spec = self.lsp.client_for_path(repo_id, target, required=True)
            assert client is not None and spec is not None
            uri = client.open_document(target, spec.language_id)
            result = client.request("textDocument/definition", {
                "textDocument": {"uri": uri},
                "position": self._position(line, character),
            })
            locations = []
            if isinstance(result, dict):
                locations = [result]
            elif isinstance(result, list):
                locations = [x for x in result if isinstance(x, dict)]
            root = self.ctx.repos.root(repo_id)
            return {"path": rel, "definitions": [location_to_dict(x, root) for x in locations], "engine": f"lsp:{spec.language_id}"}

    def references_at(self, actor: str, repo_id: int, path: str, line: int, character: int = 0, include_declaration: bool = True) -> dict[str, Any]:
        args = {"path": path, "line": line, "character": character, "include_declaration": include_declaration}
        with self.ctx.record(actor, repo_id, "code_references_at", path, args):
            target, rel = self._target(repo_id, path)
            self.ctx.permissions.require(actor, repo_id, "code_intel", rel, args, suggested_pattern="*")
            client, spec = self.lsp.client_for_path(repo_id, target, required=True)
            assert client is not None and spec is not None
            uri = client.open_document(target, spec.language_id)
            result = client.request("textDocument/references", {
                "textDocument": {"uri": uri},
                "position": self._position(line, character),
                "context": {"includeDeclaration": bool(include_declaration)},
            }) or []
            root = self.ctx.repos.root(repo_id)
            locations = [location_to_dict(x, root) for x in result if isinstance(x, dict)] if isinstance(result, list) else []
            return {"path": rel, "references": locations, "engine": f"lsp:{spec.language_id}"}

    def diagnostics(self, actor: str, repo_id: int, path: str, wait_ms: int = 600) -> dict[str, Any]:
        args = {"path": path, "wait_ms": wait_ms}
        with self.ctx.record(actor, repo_id, "code_diagnostics", path, args):
            target, rel = self._target(repo_id, path)
            self.ctx.permissions.require(actor, repo_id, "code_intel", rel, args, suggested_pattern="*")
            client, spec = self.lsp.client_for_path(repo_id, target, required=True)
            assert client is not None and spec is not None
            uri = client.open_document(target, spec.language_id)
            diagnostics: list[dict[str, Any]] = []
            engine_method = "publishDiagnostics"

            if client.capabilities.get("diagnosticProvider"):
                try:
                    result = client.request("textDocument/diagnostic", {
                        "textDocument": {"uri": uri},
                        "identifier": None,
                        "previousResultId": None,
                    }, timeout=max(2.0, wait_ms / 1000 + 1.0)) or {}
                    items = result.get("items") if isinstance(result, dict) else None
                    if isinstance(items, list):
                        diagnostics = items
                        engine_method = "textDocument/diagnostic"
                except Exception:
                    diagnostics = []

            if not diagnostics:
                msg = client.wait_notification(
                    "textDocument/publishDiagnostics",
                    lambda m: isinstance(m.get("params"), dict) and m["params"].get("uri") == uri,
                    timeout=max(0.0, min(wait_ms, 5000) / 1000),
                )
                if msg and isinstance(msg.get("params"), dict):
                    diagnostics = msg["params"].get("diagnostics") or []

            normalized = []
            for item in diagnostics:
                if not isinstance(item, dict):
                    continue
                normalized.append({
                    "range": range_to_dict(item.get("range")),
                    "severity": item.get("severity"),
                    "code": item.get("code"),
                    "source": item.get("source"),
                    "message": item.get("message", ""),
                    "tags": item.get("tags") or [],
                    "relatedInformation": item.get("relatedInformation") or [],
                })
            return {
                "path": rel,
                "diagnostics": normalized,
                "engine": f"lsp:{spec.language_id}",
                "method": engine_method,
            }

    def workspace_symbols(self, actor: str, repo_id: int, query: str, max_results: int = 200) -> dict[str, Any]:
        args = {"query": query, "max_results": max_results}
        with self.ctx.record(actor, repo_id, "code_workspace_symbols", query, args):
            self.ctx.permissions.require(actor, repo_id, "code_intel", query, args, suggested_pattern="*")
            root = self.ctx.repos.root(repo_id)
            results: list[dict[str, Any]] = []
            errors: list[str] = []
            used: list[str] = []
            # Query one server per language. This avoids assuming a repository has
            # only one language while still keeping bounded output.
            for spec in self.lsp.specs():
                if len(results) >= max_results:
                    break
                sample = next((p for ext in spec.extensions for p in root.rglob(f"*{ext}") if ".git" not in p.parts), None)
                if sample is None:
                    continue
                client, _ = self.lsp.client_for_path(repo_id, sample)
                if client is None:
                    continue
                try:
                    raw = client.request("workspace/symbol", {"query": query}) or []
                    used.append(spec.language_id)
                    if not isinstance(raw, list):
                        continue
                    for item in raw:
                        if not isinstance(item, dict):
                            continue
                        location = item.get("location") if isinstance(item.get("location"), dict) else {}
                        entry = {
                            "name": item.get("name", ""),
                            "kind": item.get("kind"),
                            "kind_name": self._kind_name(item.get("kind")),
                            "container": item.get("containerName"),
                            "location": location_to_dict(location, root) if location else None,
                            "language_id": spec.language_id,
                        }
                        results.append(entry)
                        if len(results) >= max_results:
                            break
                except Exception as exc:
                    errors.append(f"{spec.language_id}: {type(exc).__name__}: {exc}")
            return {"query": query, "symbols": results, "languages": used, "errors": errors, "engine": "lsp"}

    @staticmethod
    def _kind_name(kind: Any) -> str:
        try:
            from devmesh_studio.services.lsp import KIND_NAMES
            return KIND_NAMES.get(int(kind), str(kind))
        except Exception:
            return str(kind)

    def call_hierarchy(self, actor: str, repo_id: int, path: str, line: int, character: int = 0, direction: str = "incoming") -> dict[str, Any]:
        if direction not in {"incoming", "outgoing"}:
            raise ValueError("direction must be incoming or outgoing")
        args = {"path": path, "line": line, "character": character, "direction": direction}
        with self.ctx.record(actor, repo_id, "code_call_hierarchy", path, args):
            target, rel = self._target(repo_id, path)
            self.ctx.permissions.require(actor, repo_id, "code_intel", rel, args, suggested_pattern="*")
            client, spec = self.lsp.client_for_path(repo_id, target, required=True)
            assert client is not None and spec is not None
            uri = client.open_document(target, spec.language_id)
            items = client.request("textDocument/prepareCallHierarchy", {
                "textDocument": {"uri": uri},
                "position": self._position(line, character),
            }) or []
            if not isinstance(items, list) or not items:
                return {"path": rel, "direction": direction, "items": [], "calls": [], "engine": f"lsp:{spec.language_id}"}
            item = items[0]
            method = "callHierarchy/incomingCalls" if direction == "incoming" else "callHierarchy/outgoingCalls"
            calls = client.request(method, {"item": item}) or []
            root = self.ctx.repos.root(repo_id)
            normalized = []
            for call in calls if isinstance(calls, list) else []:
                if not isinstance(call, dict):
                    continue
                peer = call.get("from") if direction == "incoming" else call.get("to")
                if not isinstance(peer, dict):
                    continue
                normalized.append({
                    "name": peer.get("name", ""),
                    "kind": peer.get("kind"),
                    "detail": peer.get("detail"),
                    "location": location_to_dict({"uri": peer.get("uri"), "range": peer.get("selectionRange") or peer.get("range")}, root),
                    "ranges": [range_to_dict(x) for x in (call.get("fromRanges") or []) if isinstance(x, dict)],
                })
            return {"path": rel, "direction": direction, "items": items, "calls": normalized, "engine": f"lsp:{spec.language_id}"}

    # Backward-compatible symbol-oriented helpers --------------------------------
    def references(self, actor: str, repo_id: int, symbol: str, max_results: int = 200) -> dict[str, Any]:
        args = {"symbol": symbol, "max_results": max_results}
        with self.ctx.record(actor, repo_id, "code_references", symbol, args):
            self.ctx.permissions.require(actor, repo_id, "code_intel", symbol, args, suggested_pattern="*")
            from .filesystem import FilesystemTools
            fs = FilesystemTools(self.ctx)
            result = fs.grep(actor, repo_id, symbol, regex=False, max_results=max_results)
            result["engine"] = result.get("engine", "text-search")
            return result

    def definition(self, actor: str, repo_id: int, symbol: str, max_results: int = 50) -> dict[str, Any]:
        args = {"symbol": symbol}
        with self.ctx.record(actor, repo_id, "code_definition", symbol, args):
            self.ctx.permissions.require(actor, repo_id, "code_intel", symbol, args, suggested_pattern="*")

            # Prefer workspace/symbol from live language servers when available.
            try:
                semantic = self.workspace_symbols(actor, repo_id, symbol, max_results=max_results)
                exact = [x for x in semantic.get("symbols", []) if x.get("name") == symbol and x.get("location")]
                if exact:
                    return {"definitions": [x["location"] for x in exact[:max_results]], "truncated": len(exact) > max_results, "engine": "lsp:workspace-symbol"}
            except Exception:
                pass

            patterns = [
                rf"^\s*class\s+{re.escape(symbol)}\b",
                rf"^\s*(?:async\s+)?def\s+{re.escape(symbol)}\b",
                rf"^\s*(?:export\s+)?(?:async\s+)?function\s+{re.escape(symbol)}\b",
                rf"^\s*(?:export\s+)?class\s+{re.escape(symbol)}\b",
                rf"^\s*(?:pub\s+)?fn\s+{re.escape(symbol)}\b",
                rf"^\s*func\s+{re.escape(symbol)}\b",
            ]
            root = self.ctx.repos.root(repo_id)
            out = []
            combined = re.compile("|".join(f"(?:{p})" for p in patterns))
            for p in root.rglob("*"):
                if not p.is_file() or ".git" in p.parts or p.stat().st_size > 2_000_000:
                    continue
                if p.suffix.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".hpp"}:
                    continue
                try:
                    for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if combined.search(line):
                            out.append({"path": str(p.relative_to(root)), "line": i, "text": line[:1000]})
                            if len(out) >= max_results:
                                return {"definitions": out, "truncated": True, "engine": "syntax-search"}
                except OSError:
                    continue
            return {"definitions": out, "truncated": False, "engine": "syntax-search"}
