from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import Request

from devmesh_studio.runtime.live_activity import (
    ActivityScope,
    LiveActivity,
    create_live_activity_router,
    sanitize_arguments,
    sse_event,
)
from devmesh_studio.runtime.live_widget import LIVE_WIDGET_MIME, LIVE_WIDGET_URI, live_widget_resource
from devmesh_studio.runtime.tool_registry import ToolDispatchResult, ToolRegistry
from devmesh_studio.tools.base import ToolContext
from devmesh_studio.tools.downstream_mcp import DownstreamMCPTools, _PersistentMCPWorker


def run(coro):
    return asyncio.run(coro)


def test_broker_scope_bounded_queue_disconnect_and_cleanup():
    async def exercise():
        activity = LiveActivity(queue_size=2, replay_size=3)
        token, _ = await activity.mint(ActivityScope("alice", repo_id=5, server_id=1))
        subscriber = await activity.subscribe(token)
        assert await activity.publish({"actor": "bob", "repo_id": 5, "server_id": 1, "phase": "started"}) is None
        for number in range(3):
            await activity.publish({"actor": "alice", "repo_id": 5, "server_id": 1, "phase": "started", "number": number})
        assert (await subscriber.get(.1))["number"] == 1
        assert (await subscriber.get(.1))["number"] == 2
        await subscriber.close()
        assert (await activity.stats())["subscribers"] == 0
        subscriber = await activity.subscribe(token)
        await activity.close()
        assert (await subscriber.get(.1))["_close"] is True
        assert await activity.validate(token) is None

    run(exercise())


def test_capability_expiry_wrong_scope_and_preview_authorization(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("devmesh_studio.runtime.live_activity.time.time", lambda: clock[0])

    async def exercise():
        activity = LiveActivity(capability_ttl=5, preview_ttl=5)
        token, _ = await activity.mint(ActivityScope("alice", server_id=1))
        other, _ = await activity.mint(ActivityScope("alice", server_id=2))
        subscriber = await activity.subscribe(token)
        assert await activity.publish({"actor": "alice", "server_id": 2, "phase": "started"}) is None
        assert await subscriber.get(.01) is None
        raw = b"small-image"
        result = {"content": [{"type": "image", "mimeType": "image/png", "data": base64.b64encode(raw).decode()}]}
        preview = await activity.store_preview(result, ActivityScope("alice", server_id=1))
        assert (await activity.get_preview(token, preview["id"])).data == raw
        assert await activity.get_preview(other, preview["id"]) is None
        clock[0] += 6
        assert await activity.validate(token) is None
        with pytest.raises(PermissionError):
            await activity.subscribe(token)
        assert await activity.get_preview(token, preview["id"]) is None

    run(exercise())


def test_sanitizer_never_forwards_secrets_or_typed_text():
    safe = sanitize_arguments({
        "x": 12, "y": 30, "label": "Live Calls", "text": "typed secret",
        "password": "hunter2", "Authorization": "Bearer private", "api_key": "private",
        "headers": {"Cookie": "session=private"}, "prompt": "credentials here",
    })
    encoded = repr(safe)
    assert safe == {"x": "12", "y": "30", "label": "Live Calls", "text_length": 12}
    assert "hunter2" not in encoded and "Bearer" not in encoded and "credentials" not in encoded


def test_sse_format_and_widget_csp_are_restrictive():
    payload = sse_event({"id": 42, "actor": "private", "phase": "completed", "tool": "click"}).decode()
    assert payload.startswith("id: 42\nevent: activity\ndata: ")
    assert '"tool":"click"' in payload
    assert "private" not in payload
    resource = live_widget_resource("https://devmesh.example.test")
    assert resource["uri"] == LIVE_WIDGET_URI
    assert resource["mimeType"] == LIVE_WIDGET_MIME
    assert resource["_meta"]["ui"]["csp"]["connectDomains"] == ["https://devmesh.example.test"]


def test_live_open_returns_connection_data_only_in_private_meta(repo_env):
    storage, repo, _ = repo_env
    storage.set_setting("public_url", "https://devmesh.example.test")
    sid = storage.add_mcp_server("cua-driver", "stdio", command="fake", allowed_tools=["*"])
    registry = ToolRegistry(storage)

    async def exercise():
        opened = await registry.dispatch("chatgpt", "live_activity_open", {
            "repo_id": repo["id"], "server_id": sid, "session": "cua-smoke-test",
        })
        assert isinstance(opened, ToolDispatchResult)
        assert opened.data == {"status": "ready", "server": "cua-driver"}
        assert "capability" not in repr(opened.data)
        private = opened.private_meta["devmeshLive"]
        assert private["streamUrl"] == "https://devmesh.example.test/live/activity/stream"
        assert (await registry.live_activity.validate(private["capability"])).scope == ActivityScope(
            "chatgpt", repo["id"], sid, "cua-smoke-test",
        )
        routes = create_live_activity_router(registry.live_activity).routes
        assert all(route.methods <= {"GET", "OPTIONS", "HEAD"} for route in routes)

    run(exercise())


def test_stream_endpoint_rejects_invalid_capability():
    async def exercise():
        activity = LiveActivity()
        route = next(route for route in create_live_activity_router(activity).routes if route.path == "/live/activity/stream" and "GET" in route.methods)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        def request(token):
            return Request({
                "type": "http", "http_version": "1.1", "method": "GET",
                "scheme": "http", "path": "/live/activity/stream", "raw_path": b"/live/activity/stream",
                "query_string": b"", "root_path": "", "server": ("test", 80), "client": ("test", 1),
                "headers": [(b"authorization", f"Bearer {token}".encode())],
            }, receive)

        rejected = await route.endpoint(request("invalid"))
        assert rejected.status_code == 401
        assert rejected.headers["cache-control"] == "no-store"

        token, _ = await activity.mint(ActivityScope("alice"))
        response = await route.endpoint(request(token))
        assert response.media_type == "text/event-stream"
        assert await anext(response.body_iterator) == b": connected\n\n"
        await response.body_iterator.aclose()
        assert (await activity.stats())["subscribers"] == 0

    run(exercise())


class _StatefulSession:
    def __init__(self, calls, *, fail_once=False):
        self.calls = calls
        self.fail_once = fail_once

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.fail_once:
            self.fail_once = False
            raise ConnectionError("transport lost")
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}


def test_persistent_worker_reuse_serialization_config_change_recovery_and_shutdown(repo_env, monkeypatch):
    storage, repo, _ = repo_env
    sid = storage.add_mcp_server("stateful", "stdio", command="fake", allowed_tools=["*"])
    created = []
    calls = []

    @asynccontextmanager
    async def fake_session(self):
        index = len(created)
        created.append(self)
        yield _StatefulSession(calls, fail_once=index == 0)

    monkeypatch.setattr(_PersistentMCPWorker, "_session", fake_session)
    tools = DownstreamMCPTools(ToolContext(storage))

    async def exercise():
        with pytest.raises(ConnectionError):
            await tools.call("desktop", repo["id"], sid, "first", {})
        await tools.call("desktop", repo["id"], sid, "second", {})
        await asyncio.gather(
            tools.call("desktop", repo["id"], sid, "third", {"n": 3}),
            tools.call("desktop", repo["id"], sid, "fourth", {"n": 4}),
        )
        await tools.call("desktop", server_id=sid, tool_name="without_repo", arguments={})
        assert len(created) == 2
        storage.update_mcp_server(sid, "stateful", "stdio", command="changed", allowed_tools=["*"])
        await tools.call("desktop", repo["id"], sid, "after_change", {})
        assert len(created) == 3
        await tools.close_all()
        assert tools._workers == {}
        assert all(not worker.usable for worker in created)

    run(exercise())


def test_live_telemetry_does_not_bypass_mcp_permission(repo_env, monkeypatch):
    storage, repo, _ = repo_env
    sid = storage.add_mcp_server("guarded", "stdio", command="fake", allowed_tools=["click"])
    storage.upsert_permission(repo["id"], "mcp", "guarded.*", "deny", 200)
    invoked = []

    @asynccontextmanager
    async def fake_session(self):
        invoked.append("connected")
        yield SimpleNamespace(call_tool=lambda *args: None)

    monkeypatch.setattr(_PersistentMCPWorker, "_session", fake_session)
    activity = LiveActivity()
    tools = DownstreamMCPTools(ToolContext(storage), activity)

    async def exercise():
        token, _ = await activity.mint(ActivityScope("chatgpt", repo["id"], sid))
        subscriber = await activity.subscribe(token)
        with pytest.raises(PermissionError):
            await tools.call("chatgpt", repo["id"], sid, "click", {"x": 1, "password": "nope"})
        started = await subscriber.get(.1)
        failed = await subscriber.get(.1)
        assert started["phase"] == "started"
        assert failed["phase"] == "failed"
        assert invoked == []
        await subscriber.close()

    run(exercise())
