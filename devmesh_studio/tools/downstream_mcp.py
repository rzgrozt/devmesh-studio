from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import inspect
import json
import os
import time
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncIterator

from devmesh_studio.core.permissions import ApprovalRequired
from devmesh_studio.runtime.live_activity import (
    ActivityScope,
    LiveActivity,
    extract_target,
    public_session,
    sanitize_arguments,
    target_metadata,
)
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


def _server_fingerprint(server: dict[str, Any]) -> str:
    """Fingerprint connection-relevant server configuration.

    A changed command, URL, args, or environment must replace the existing worker.
    The fingerprint is internal only and never exposes environment values.
    """
    payload = {
        "id": server.get("id"),
        "transport": server.get("transport"),
        "command": server.get("command"),
        "args": list(server.get("args", [])),
        "url": server.get("url"),
        "env": dict(server.get("env", {})),
        "enabled": bool(server.get("enabled")),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class _PersistentMCPWorker:
    """Own one long-lived downstream MCP runtime in a single asyncio task.

    Stdio MCPs often keep application state in their process even though modern MCP
    requests are protocol-level stateless. Keeping the child process and ClientSession
    alive lets explicit application handles (browser contexts, CUA sessions, snapshot
    tokens, etc.) survive across DevMesh mcp_call invocations.

    The worker task owns both entry and exit of the MCP context managers. This avoids
    crossing AnyIO cancel scopes between independent FastAPI request tasks.
    """

    def __init__(self, server: dict[str, Any]):
        self.server = dict(server)
        self.fingerprint = _server_fingerprint(server)
        self._queue: asyncio.Queue[tuple[str, tuple[Any, ...], asyncio.Future[Any]]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._fatal_error: Exception | None = None

    @property
    def usable(self) -> bool:
        return not self._closed and (self._task is None or not self._task.done())

    def _ensure_started(self) -> None:
        if self._closed:
            raise RuntimeError("downstream MCP worker is closed")
        if self._task is None:
            name = self.server.get("name") or self.server.get("id") or "mcp"
            self._task = asyncio.create_task(self._run(), name=f"devmesh-mcp-{name}")

    async def request(self, operation: str, *payload: Any) -> Any:
        self._ensure_started()
        if self._task is not None and self._task.done():
            detail = f": {self._fatal_error}" if self._fatal_error else ""
            raise RuntimeError(f"downstream MCP worker stopped{detail}")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await self._queue.put((operation, payload, future))
        task = self._task
        if task is None:
            raise RuntimeError("downstream MCP worker failed to start")
        done, _ = await asyncio.wait({future, task}, return_when=asyncio.FIRST_COMPLETED)
        if future in done:
            return future.result()
        if not future.done():
            future.cancel()
        detail = f": {self._fatal_error}" if self._fatal_error else ""
        raise RuntimeError(f"downstream MCP worker stopped{detail}")

    async def close(self) -> None:
        task = self._task
        if task is None:
            self._closed = True
            return
        if task.done():
            self._closed = True
            with suppress(asyncio.CancelledError, Exception):
                await task
            return

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await self._queue.put(("__close__", (), future))
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.wait({future, task}, return_when=asyncio.FIRST_COMPLETED)
        with suppress(asyncio.CancelledError, Exception):
            await task
        self._closed = True

    def _fail_pending(self, exc: Exception) -> None:
        while True:
            try:
                _, _, future = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not future.done():
                future.set_exception(exc)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError("mcp Python package is required") from exc

        server = self.server
        if server["transport"] == "stdio":
            command = server.get("command")
            if not command:
                raise ValueError("stdio MCP requires command")
            child_env = os.environ.copy()
            child_env.update({str(k): str(v) for k, v in server.get("env", {}).items()})
            params = StdioServerParameters(
                command=command,
                args=list(server.get("args", [])),
                env=child_env,
            )
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
        if "timeout" in signature.parameters:
            kwargs["timeout"] = 30.0
        async with streamable_http_client(url, **kwargs) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session

    async def _run(self) -> None:
        try:
            async with self._session() as session:
                while True:
                    operation, payload, future = await self._queue.get()
                    if operation == "__close__":
                        if not future.done():
                            future.set_result(None)
                        break

                    try:
                        if operation == "list_tools":
                            result = await session.list_tools()
                        elif operation == "call_tool":
                            tool_name, arguments = payload
                            result = await session.call_tool(tool_name, arguments)
                        else:
                            raise ValueError(f"unknown downstream MCP worker operation: {operation}")
                    except Exception as exc:
                        # MCP tool failures are normally returned as CallToolResult(isError).
                        # An exception here is therefore treated as a transport/protocol
                        # failure: fail this request and tear down the worker so the next
                        # DevMesh call gets a clean downstream runtime.
                        if not future.done():
                            future.set_exception(exc)
                        raise
                    else:
                        if not future.done():
                            future.set_result(result)

        except asyncio.CancelledError:
            exc = RuntimeError("downstream MCP worker cancelled")
            self._fatal_error = exc
            self._fail_pending(exc)
            raise
        except Exception as exc:
            self._fatal_error = exc
            self._fail_pending(exc)
        finally:
            self._closed = True


class DownstreamMCPTools:
    def __init__(self, ctx: ToolContext, live_activity: LiveActivity | None = None):
        self.ctx = ctx
        self.live_activity = live_activity
        self._workers: dict[str, _PersistentMCPWorker] = {}
        self._workers_lock: asyncio.Lock | None = None

    def _lock(self) -> asyncio.Lock:
        if self._workers_lock is None:
            self._workers_lock = asyncio.Lock()
        return self._workers_lock

    async def _worker_for(self, server: dict[str, Any]) -> _PersistentMCPWorker:
        key = str(server.get("id") or server.get("name"))
        fingerprint = _server_fingerprint(server)
        async with self._lock():
            worker = self._workers.get(key)
            if worker is not None and (worker.fingerprint != fingerprint or not worker.usable):
                await worker.close()
                self._workers.pop(key, None)
                worker = None
            if worker is None:
                worker = _PersistentMCPWorker(server)
                self._workers[key] = worker
            return worker

    async def close_all(self) -> None:
        """Stop every downstream MCP child/session owned by this gateway."""
        async with self._lock():
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            await worker.close()

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

    async def list_tools(self, actor: str, server_id: int | str) -> dict[str, Any]:
        server = self.ctx.storage.get_mcp_server(server_id)
        if not server or not server["enabled"]:
            raise KeyError(f"unknown/disabled MCP server: {server_id}")
        args = {"server_id": server_id}
        with self.ctx.record(actor, None, "mcp_tools", str(server_id), args):
            worker = await self._worker_for(server)
            page = await worker.request("list_tools")
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

    async def call(
        self,
        actor: str,
        repo_id: int | None = None,
        server_id: int | str | None = None,
        tool_name: str | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if server_id is None or not tool_name:
            raise ValueError("server_id and tool_name are required")
        arguments = arguments or {}
        server = self.ctx.storage.get_mcp_server(server_id)
        if not server or not server["enabled"]:
            raise KeyError(f"unknown/disabled MCP server: {server_id}")
        if not _tool_allowed(tool_name, list(server.get("allowed_tools", []))):
            raise PermissionError(f"downstream MCP tool is not allowlisted: {server['name']}.{tool_name}")

        args = {"server_id": server_id, "tool_name": tool_name, "arguments": arguments}
        resource = f"{server['name']}.{tool_name}"
        server_numeric_id = int(server["id"])
        session = public_session(arguments)
        scope = ActivityScope(actor, repo_id, server_numeric_id, session)
        started = time.perf_counter()
        with self.ctx.record(actor, repo_id, "mcp_call", resource, args) as call_id:
            telemetry = bool(self.live_activity and await self.live_activity.interested(
                actor=actor, repo_id=repo_id, server_id=server_numeric_id, session=session,
            ))
            details = sanitize_arguments(arguments) if telemetry else {}
            target = target_metadata(arguments) if telemetry else None
            base_event = {
                "actor": actor, "source": "downstream_mcp", "server_id": server_numeric_id,
                "server_name": server["name"], "tool": tool_name, "repo_id": repo_id,
                "session": session, "call_id": call_id, "details": details,
            }
            if target:
                base_event["target"] = target
            if telemetry and self.live_activity:
                await self.live_activity.publish({**base_event, "phase": "started", "status": "running"})
            try:
                self.ctx.permissions.require(
                    actor,
                    repo_id,
                    "mcp",
                    resource,
                    args,
                    suggested_pattern=f"{server['name']}.*",
                )
                worker = await self._worker_for(server)
                result = await worker.request("call_tool", tool_name, arguments)
                dumped = _dump(result)
            except ApprovalRequired:
                if telemetry and self.live_activity:
                    await self.live_activity.publish({
                        **base_event, "phase": "approval_required", "status": "approval_required",
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                        "summary": f"Approval required for {tool_name}",
                    })
                raise
            except Exception as exc:
                if telemetry and self.live_activity:
                    await self.live_activity.publish({
                        **base_event, "phase": "failed", "status": "error",
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                        "summary": f"{tool_name} failed ({type(exc).__name__})",
                    })
                raise
            else:
                completion_interested = bool(self.live_activity and (telemetry or await self.live_activity.interested(
                    actor=actor, repo_id=repo_id, server_id=server_numeric_id, session=session,
                )))
                if self.live_activity and completion_interested:
                    if not telemetry:
                        details = sanitize_arguments(arguments)
                        target = target_metadata(arguments)
                        base_event["details"] = details
                        if target:
                            base_event["target"] = target
                    preview = await self.live_activity.store_preview(dumped, scope)
                    result_target = extract_target(dumped)
                    result_failed = bool(isinstance(dumped, dict) and (dumped.get("isError") or dumped.get("is_error")))
                    completed = {
                        **base_event, "phase": "failed" if result_failed else "completed",
                        "status": "error" if result_failed else "ok",
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                        "summary": f"{tool_name} failed" if result_failed else f"{tool_name} completed",
                    }
                    if result_target:
                        completed["target"] = {**(target or {}), **result_target}
                    if preview:
                        completed["preview"] = preview
                    await self.live_activity.publish(completed)
                return dumped
