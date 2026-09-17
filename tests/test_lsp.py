from __future__ import annotations

import json
import sys
from pathlib import Path

from devmesh_studio.tools.base import ToolContext
from devmesh_studio.tools.code_intel import CodeIntelTools


FAKE_LSP = r'''#!/usr/bin/env python3
import json, sys

inp = sys.stdin.buffer
out = sys.stdout.buffer
opened_uri = None

def send(msg):
    raw = json.dumps(msg, separators=(",", ":")).encode()
    out.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
    out.flush()

def read_message():
    headers = {}
    while True:
        line = inp.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode().strip()
        if ":" in text:
            k, v = text.split(":", 1)
            headers[k.lower()] = v.strip()
    n = int(headers.get("content-length", 0))
    return json.loads(inp.read(n).decode()) if n else None

while True:
    msg = read_message()
    if msg is None:
        break
    method = msg.get("method")
    rid = msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        send({"jsonrpc":"2.0","id":rid,"result":{"serverInfo":{"name":"FakeLSP","version":"1"},"capabilities":{"documentSymbolProvider":True,"definitionProvider":True,"referencesProvider":True,"hoverProvider":True,"diagnosticProvider":{"interFileDependencies":False,"workspaceDiagnostics":False},"workspaceSymbolProvider":True,"callHierarchyProvider":True}}})
    elif method == "initialized":
        pass
    elif method == "textDocument/didOpen":
        opened_uri = params["textDocument"]["uri"]
        send({"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":opened_uri,"diagnostics":[{"range":{"start":{"line":0,"character":0},"end":{"line":0,"character":3}},"severity":2,"code":"W001","source":"fake","message":"fake warning"}]}})
    elif method == "textDocument/didChange" or method == "textDocument/didSave":
        pass
    elif method == "textDocument/documentSymbol":
        send({"jsonrpc":"2.0","id":rid,"result":[{"name":"greet","kind":12,"range":{"start":{"line":0,"character":0},"end":{"line":1,"character":10}},"selectionRange":{"start":{"line":0,"character":4},"end":{"line":0,"character":9}}}]})
    elif method == "textDocument/hover":
        send({"jsonrpc":"2.0","id":rid,"result":{"contents":{"kind":"markdown","value":"```python\ndef greet(name: str) -> str\n```"},"range":{"start":{"line":0,"character":4},"end":{"line":0,"character":9}}}})
    elif method == "textDocument/definition":
        uri = params["textDocument"]["uri"]
        send({"jsonrpc":"2.0","id":rid,"result":{"uri":uri,"range":{"start":{"line":0,"character":4},"end":{"line":0,"character":9}}}})
    elif method == "textDocument/references":
        uri = params["textDocument"]["uri"]
        send({"jsonrpc":"2.0","id":rid,"result":[{"uri":uri,"range":{"start":{"line":0,"character":4},"end":{"line":0,"character":9}}},{"uri":uri,"range":{"start":{"line":3,"character":6},"end":{"line":3,"character":11}}}]})
    elif method == "textDocument/diagnostic":
        send({"jsonrpc":"2.0","id":rid,"result":{"kind":"full","items":[{"range":{"start":{"line":0,"character":0},"end":{"line":0,"character":3}},"severity":2,"code":"W001","source":"fake","message":"fake warning"}]}})
    elif method == "workspace/symbol":
        uri = opened_uri
        send({"jsonrpc":"2.0","id":rid,"result":[{"name":"greet","kind":12,"containerName":"app","location":{"uri":uri,"range":{"start":{"line":0,"character":4},"end":{"line":0,"character":9}}}}]})
    elif method == "textDocument/prepareCallHierarchy":
        uri = params["textDocument"]["uri"]
        send({"jsonrpc":"2.0","id":rid,"result":[{"name":"greet","kind":12,"uri":uri,"range":{"start":{"line":0,"character":0},"end":{"line":1,"character":10}},"selectionRange":{"start":{"line":0,"character":4},"end":{"line":0,"character":9}}}]})
    elif method == "callHierarchy/incomingCalls":
        item = params["item"]
        send({"jsonrpc":"2.0","id":rid,"result":[{"from":{"name":"main","kind":12,"uri":item["uri"],"range":{"start":{"line":3,"character":0},"end":{"line":3,"character":20}},"selectionRange":{"start":{"line":3,"character":0},"end":{"line":3,"character":4}}},"fromRanges":[{"start":{"line":3,"character":6},"end":{"line":3,"character":11}}]}]})
    elif method == "callHierarchy/outgoingCalls":
        send({"jsonrpc":"2.0","id":rid,"result":[]})
    elif method == "shutdown":
        send({"jsonrpc":"2.0","id":rid,"result":None})
    elif method == "exit":
        break
    elif rid is not None:
        send({"jsonrpc":"2.0","id":rid,"result":None})
'''


def configure_fake_lsp(storage, tmp_path: Path) -> Path:
    fake = tmp_path / "fake_lsp.py"
    fake.write_text(FAKE_LSP, encoding="utf-8")
    storage.set_setting("lsp_servers_json", json.dumps({
        "python": {
            "command": [sys.executable, str(fake)],
            "extensions": [".py"],
        }
    }))
    return fake


def test_real_lsp_protocol_semantic_tools(repo_env, tmp_path):
    storage, repo, root = repo_env
    configure_fake_lsp(storage, tmp_path)
    code = CodeIntelTools(ToolContext(storage))
    try:
        status = code.lsp_status("chatgpt")
        py = next(x for x in status["servers"] if x["language_id"] == "python")
        assert py["available"] is True and py["source"] == "custom"

        symbols = code.symbols("chatgpt", repo["id"], "app.py")
        assert symbols["engine"] == "lsp:python"
        assert symbols["server"]["name"] == "FakeLSP"
        assert symbols["symbols"][0]["name"] == "greet"
        assert symbols["symbols"][0]["line"] == 1

        hover = code.hover("chatgpt", repo["id"], "app.py", 1, 5)
        assert "greet" in hover["contents"]

        definition = code.definition_at("chatgpt", repo["id"], "app.py", 4, 7)
        assert definition["definitions"][0]["path"] == "app.py"
        assert definition["definitions"][0]["range"]["start"]["line"] == 1

        references = code.references_at("chatgpt", repo["id"], "app.py", 1, 5)
        assert len(references["references"]) == 2
        assert references["references"][1]["range"]["start"]["line"] == 4

        diagnostics = code.diagnostics("chatgpt", repo["id"], "app.py", wait_ms=100)
        assert diagnostics["diagnostics"][0]["code"] == "W001"
        assert diagnostics["diagnostics"][0]["message"] == "fake warning"

        workspace = code.workspace_symbols("chatgpt", repo["id"], "greet")
        assert workspace["symbols"][0]["name"] == "greet"
        assert workspace["symbols"][0]["location"]["path"] == "app.py"

        calls = code.call_hierarchy("chatgpt", repo["id"], "app.py", 1, 5, "incoming")
        assert calls["calls"][0]["name"] == "main"
    finally:
        code.lsp.stop_all()


def test_lsp_tools_through_registry_dispatch(repo_env, tmp_path):
    import asyncio
    from devmesh_studio.runtime.tool_registry import ToolRegistry

    storage, repo, root = repo_env
    configure_fake_lsp(storage, tmp_path)
    registry = ToolRegistry(storage)

    async def run():
        status = await registry.dispatch("chatgpt", "code_lsp_status", {})
        hover = await registry.dispatch("chatgpt", "code_hover", {
            "repo_id": repo["id"], "path": "app.py", "line": 1, "character": 5,
        })
        defs = await registry.dispatch("chatgpt", "code_definition_at", {
            "repo_id": repo["id"], "path": "app.py", "line": 4, "character": 7,
        })
        diagnostics = await registry.dispatch("chatgpt", "code_diagnostics", {
            "repo_id": repo["id"], "path": "app.py", "wait_ms": 100,
        })
        return status, hover, defs, diagnostics

    try:
        status, hover, defs, diagnostics = asyncio.run(run())
        assert any(x["language_id"] == "python" and x["available"] for x in status["servers"])
        assert "greet" in hover["contents"]
        assert defs["definitions"][0]["path"] == "app.py"
        assert diagnostics["diagnostics"][0]["source"] == "fake"
    finally:
        registry.code.lsp.stop_all()
