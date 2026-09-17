from __future__ import annotations


def test_downstream_mcp_configuration_is_persisted_and_secrets_are_local(repo_env):
    storage, repo, root = repo_env
    sid=storage.add_mcp_server("demo","stdio",command="demo-mcp",args=["--stdio"],env={"API_TOKEN":"secret"},allowed_tools=["safe_read"])
    row=storage.get_mcp_server(sid)
    assert row["command"]=="demo-mcp"
    assert row["allowed_tools"]==["safe_read"]
    assert row["env"]["API_TOKEN"]=="secret"

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from devmesh_studio.tools.base import ToolContext
from devmesh_studio.tools.downstream_mcp import DownstreamMCPTools


class _FakeSession:
    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(model_dump=lambda **_: {"name":"safe_read","description":"safe","inputSchema":{"type":"object"}}), SimpleNamespace(model_dump=lambda **_: {"name":"danger","description":"danger","inputSchema":{"type":"object"}})])
    async def call_tool(self, name, args):
        return {"name": name, "args": args, "ok": True}


def test_downstream_mcp_allowlist_and_adapter_logic(repo_env, monkeypatch):
    storage, repo, root = repo_env
    sid=storage.add_mcp_server("fake","stdio",command="fake",allowed_tools=["safe_read"])
    tools=DownstreamMCPTools(ToolContext(storage))

    @asynccontextmanager
    async def fake_session(server):
        yield _FakeSession()

    monkeypatch.setattr(tools, "_session", fake_session)
    listed=asyncio.run(tools.list_tools("desktop",sid))
    by={x["name"]:x for x in listed["tools"]}
    assert by["safe_read"]["allowed"] is True
    assert by["danger"]["allowed"] is False
    result=asyncio.run(tools.call("desktop",repo["id"],sid,"safe_read",{"x":1}))
    assert result["ok"] is True
    import pytest
    with pytest.raises(PermissionError):
        asyncio.run(tools.call("desktop",repo["id"],sid,"danger",{}))
