from __future__ import annotations

import fnmatch
import inspect
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from .base import ToolContext


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(exclude_none=True)
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    return value


def _tool_allowed(tool_name: str, patterns: list[str] | tuple[str, ...] | set[str]) -> bool:
    """Return whether a downstream MCP tool name matches the configured allowlist.

    ``*`` explicitly means every tool on that downstream server. Other shell-style
    patterns are also supported (for example ``browser_*``), while exact tool names
    continue to work unchanged.
    """
    return any(pattern == "*" or fnmatch.fnmatchcase(tool_name, pattern) for pattern in patterns)


class DownstreamMCPTools:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx

    def list_servers(self, actor: str) -> dict[str, Any]:
        args: dict[str, Any] = {}
        with self.ctx.record(actor, None, "mcp_list", "mcp_servers", args):
            rows = self.ctx.storage.list_mcp_servers()
            # Do not expose environment secret values.
            safe = []
            for row in rows:
                item = {k: v for k, v in row.items() if k != "env"}
                item["env_keys"] = sorted(row.get("env", {}).keys())
                safe.append(item)
            return {"servers": safe}

    @asynccontextmanager
    async def _session(self, server: dict[str, Any]) -> AsyncIterator[Any]:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError("mcp Python package is required") from exc

        if server["transport"] == "stdio":
            command = server.get("command")
            if not command:
                raise ValueError("stdio MCP requires command")
            child_env = os.environ.copy()
            child_env.update({str(k): str(v) for k, v in server.get("env", {}).items()})
            params = StdioServerParameters(command=command, args=list(server.get("args", [])), env=child_env)
            async with stdio_client(params) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    yield session
            return

        url = server.get("url") or ""
        if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("HTTP MCP URL must use HTTPS or loopback HTTP")
        from mcp.client.streamable_http import streamable_http_client
        signature = inspect.signature(streamable_http_client)
        kwargs: dict[str, Any] = {}
        # Avoid silently sending credentials; authenticated downstream MCPs can
        # use env-backed config in future revisions.
        if "timeout" in signature.parameters:
            kwargs["timeout"] = 30.0
        async with streamable_http_client(url, **kwargs) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session

    async def list_tools(self, actor: str, server_id: int | str) -> dict[str, Any]:
        server = self.ctx.storage.get_mcp_server(server_id)
        if not server or not server["enabled"]:
            raise KeyError(f"unknown/disabled MCP server: {server_id}")
        args = {"server_id": server_id}
        with self.ctx.record(actor, None, "mcp_tools", str(server_id), args):
            async with self._session(server) as session:
                page = await session.list_tools()
                allowed_patterns = list(server.get("allowed_tools", []))
                tools = []
                for tool in page.tools:
                    raw = _dump(tool)
                    name = raw.get("name")
                    tools.append({
                        "name": name,
                        "description": raw.get("description"),
                        "inputSchema": raw.get("inputSchema") or raw.get("input_schema") or {},
                        "allowed": bool(name and _tool_allowed(name, allowed_patterns)),
                    })
                return {"server": server["name"], "tools": tools}

    async def call(self, actor: str, repo_id: int | None, server_id: int | str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        server = self.ctx.storage.get_mcp_server(server_id)
        if not server or not server["enabled"]:
            raise KeyError(f"unknown/disabled MCP server: {server_id}")
        if not _tool_allowed(tool_name, list(server.get("allowed_tools", []))):
            raise PermissionError(f"downstream MCP tool is not allowlisted: {server['name']}.{tool_name}")
        args = {"server_id": server_id, "tool_name": tool_name, "arguments": arguments}
        with self.ctx.record(actor, repo_id, "mcp_call", f"{server['name']}.{tool_name}", args):
            if repo_id is not None:
                self.ctx.permissions.require(actor, repo_id, "mcp", f"{server['name']}.{tool_name}", args, suggested_pattern=f"{server['name']}.*")
            else:
                self.ctx.permissions.require(actor, None, "mcp", f"{server['name']}.{tool_name}", args, suggested_pattern=f"{server['name']}.*")
            async with self._session(server) as session:
                result = await session.call_tool(tool_name, arguments)
                return _dump(result)
