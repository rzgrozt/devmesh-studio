from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse


SECRET_MARKERS = (
    "password", "passwd", "secret", "token", "authorization", "cookie",
    "api_key", "apikey", "credential", "private_key", "client_secret",
)
SAFE_DETAIL_KEYS = {
    "action", "app", "application", "browser", "button", "hostname",
    "label", "origin", "tab", "tab_title", "target", "text_length",
    "title", "window", "window_title", "x", "y",
}
# Deliberately exclude a generic ``session`` argument: downstreams often use it
# for an opaque privileged handle. These names conventionally describe public UI.
SESSION_KEYS = ("session_label", "session_name", "context_name")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(marker in lowered for marker in SECRET_MARKERS)


def _short_text(value: Any, limit: int = 160) -> str | None:
    if not isinstance(value, (str, int, float, bool)):
        return None
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:limit] if text else None


def sanitize_arguments(arguments: Any) -> dict[str, Any]:
    """Extract a small allowlisted detail object; never mirror arbitrary inputs."""
    if not isinstance(arguments, dict):
        return {}
    safe: dict[str, Any] = {}
    for raw_key, value in arguments.items():
        key = str(raw_key)
        normalized = key.lower().replace("-", "_")
        if _secret_key(normalized):
            continue
        if normalized in {"prompt", "query"}:
            continue
        if normalized in {"text", "value", "content"}:
            if isinstance(value, str):
                safe["text_length"] = len(value)
            continue
        if normalized not in SAFE_DETAIL_KEYS:
            continue
        if normalized == "target" and isinstance(value, dict):
            nested = sanitize_arguments(value)
            if nested:
                safe["target"] = nested
            continue
        scalar = _short_text(value)
        if scalar is not None:
            safe[normalized] = scalar
    return safe


def public_session(arguments: Any) -> str | None:
    if not isinstance(arguments, dict):
        return None
    for key in SESSION_KEYS:
        value = _short_text(arguments.get(key), 80)
        if value:
            return value
    return None


def target_metadata(arguments: Any) -> dict[str, str] | None:
    details = sanitize_arguments(arguments)
    target: dict[str, str] = {}
    aliases = {
        "application": "app", "app": "app", "window": "window_title",
        "window_title": "window_title", "tab": "tab_title", "tab_title": "tab_title",
        "label": "semantic_target",
    }
    for source, destination in aliases.items():
        value = details.get(source)
        if isinstance(value, str):
            target[destination] = value
    nested = details.get("target")
    if isinstance(nested, dict):
        for source, destination in aliases.items():
            value = nested.get(source)
            if isinstance(value, str):
                target[destination] = value
    return target or None


def extract_target(value: Any, depth: int = 0) -> dict[str, str] | None:
    """Find only allowlisted target fields in a structured downstream result."""
    if depth > 4:
        return None
    if isinstance(value, dict):
        direct = target_metadata(value)
        if direct:
            return direct
        for key, child in value.items():
            if not _secret_key(str(key)):
                found = extract_target(child, depth + 1)
                if found:
                    return found
    elif isinstance(value, list):
        for child in value[:20]:
            found = extract_target(child, depth + 1)
            if found:
                return found
    elif isinstance(value, str) and len(value) <= 65_536 and value.lstrip().startswith(("{", "[")):
        try:
            return extract_target(json.loads(value), depth + 1)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


@dataclass(frozen=True)
class ActivityScope:
    actor: str
    repo_id: int | None = None
    server_id: int | None = None
    session: str | None = None

    def permits(self, event: dict[str, Any]) -> bool:
        if event.get("actor") != self.actor:
            return False
        for key in ("repo_id", "server_id"):
            expected = getattr(self, key)
            if expected is not None and event.get(key) != expected:
                return False
        # A public session label narrows events when the downstream protocol
        # exposes one. Servers without a session field are still scoped by
        # actor/repository/server and may use the label as presentation only.
        if self.session is not None and event.get("session") not in (None, self.session):
            return False
        return True


@dataclass
class _Capability:
    scope: ActivityScope
    expires_at: float


@dataclass
class _Preview:
    data: bytes
    mime_type: str
    scope: ActivityScope
    expires_at: float
    width: int | None = None
    height: int | None = None


class LiveSubscription:
    def __init__(self, broker: "LiveActivity", key: int, queue: asyncio.Queue[dict[str, Any]], expires_at: float):
        self._broker = broker
        self.key = key
        self.queue = queue
        self.expires_at = expires_at
        self.closed = False

    async def get(self, timeout: float) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except TimeoutError:
            return None

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self._broker.unsubscribe(self.key)


class LiveActivity:
    """Gateway-local, read-only activity broker with bounded memory use."""

    def __init__(
        self, *, queue_size: int = 64, replay_size: int = 128,
        capability_ttl: int = 300, preview_ttl: int = 120,
        preview_bytes: int = 24 * 1024 * 1024,
    ):
        self.queue_size = max(1, queue_size)
        self.capability_ttl = max(1, capability_ttl)
        self.preview_ttl = max(1, preview_ttl)
        self.preview_bytes = max(1024, preview_bytes)
        self._next_id = 0
        self._next_subscriber = 0
        self._replay: deque[dict[str, Any]] = deque(maxlen=max(1, replay_size))
        self._capabilities: dict[str, _Capability] = {}
        self._subscribers: dict[int, tuple[ActivityScope, asyncio.Queue[dict[str, Any]]]] = {}
        self._previews: OrderedDict[str, _Preview] = OrderedDict()
        self._preview_total = 0
        self._lock = asyncio.Lock()
        self._closed = False

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def mint(self, scope: ActivityScope) -> tuple[str, int]:
        token = secrets.token_urlsafe(32)
        now = time.time()
        async with self._lock:
            self._cleanup_locked(now)
            self._capabilities[self._token_hash(token)] = _Capability(scope, now + self.capability_ttl)
        return token, self.capability_ttl

    async def validate(self, token: str) -> _Capability | None:
        if not token:
            return None
        now = time.time()
        async with self._lock:
            self._cleanup_locked(now)
            capability = self._capabilities.get(self._token_hash(token))
            if capability is None or capability.expires_at <= now:
                return None
            return capability

    async def subscribe(self, token: str, *, last_event_id: int = 0) -> LiveSubscription:
        now = time.time()
        async with self._lock:
            self._cleanup_locked(now)
            capability = self._capabilities.get(self._token_hash(token))
            if capability is None or capability.expires_at <= now or self._closed:
                raise PermissionError("invalid or expired live activity capability")
            self._next_subscriber += 1
            key = self._next_subscriber
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self.queue_size)
            matching = [event for event in self._replay if event["id"] > last_event_id and capability.scope.permits(event)]
            for event in matching[-self.queue_size:]:
                queue.put_nowait(event)
            self._subscribers[key] = (capability.scope, queue)
            return LiveSubscription(self, key, queue, capability.expires_at)

    async def unsubscribe(self, key: int) -> None:
        async with self._lock:
            self._subscribers.pop(key, None)

    async def interested(self, *, actor: str, repo_id: int | None, server_id: int, session: str | None) -> bool:
        probe = {"actor": actor, "repo_id": repo_id, "server_id": server_id, "session": session}
        async with self._lock:
            return any(scope.permits(probe) for scope, _ in self._subscribers.values())

    async def publish(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Publish to authorized subscribers. Actor is used for routing, never sent."""
        async with self._lock:
            if self._closed or not self._subscribers:
                return None
            recipients = [queue for scope, queue in self._subscribers.values() if scope.permits(event)]
            if not recipients:
                return None
            self._next_id += 1
            stored = dict(event)
            stored["id"] = self._next_id
            stored.setdefault("timestamp", _now_iso())
            self._replay.append(stored)
            for queue in recipients:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(stored)
            return self._public_event(stored)

    @staticmethod
    def _public_event(event: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in event.items() if key != "actor"}

    async def store_preview(self, result: Any, scope: ActivityScope) -> dict[str, Any] | None:
        image = self._find_image(result)
        if image is None:
            return None
        data, mime_type, width, height = image
        if not data or len(data) > self.preview_bytes:
            return None
        preview_id = "frame_" + secrets.token_urlsafe(12)
        now = time.time()
        async with self._lock:
            self._cleanup_locked(now)
            while self._previews and self._preview_total + len(data) > self.preview_bytes:
                _, old = self._previews.popitem(last=False)
                self._preview_total -= len(old.data)
            if self._preview_total + len(data) > self.preview_bytes:
                return None
            self._previews[preview_id] = _Preview(data, mime_type, scope, now + self.preview_ttl, width, height)
            self._preview_total += len(data)
        preview: dict[str, Any] = {"kind": "image", "id": preview_id}
        if width:
            preview["width"] = width
        if height:
            preview["height"] = height
        return preview

    @staticmethod
    def _find_image(value: Any) -> tuple[bytes, str, int | None, int | None] | None:
        if isinstance(value, dict):
            mime = value.get("mimeType") or value.get("mime_type")
            encoded = value.get("data")
            allowed_mime = {"image/png", "image/jpeg", "image/webp", "image/gif"}
            if value.get("type") == "image" and isinstance(encoded, str) and mime in allowed_mime:
                try:
                    return base64.b64decode(encoded, validate=True), mime, value.get("width"), value.get("height")
                except (ValueError, TypeError):
                    return None
            for key, child in value.items():
                if not _secret_key(str(key)):
                    found = LiveActivity._find_image(child)
                    if found:
                        return found
        elif isinstance(value, list):
            for child in value:
                found = LiveActivity._find_image(child)
                if found:
                    return found
        return None

    async def get_preview(self, token: str, preview_id: str) -> _Preview | None:
        now = time.time()
        async with self._lock:
            self._cleanup_locked(now)
            capability = self._capabilities.get(self._token_hash(token))
            preview = self._previews.get(preview_id)
            if not capability or not preview or not capability.scope.permits({
                "actor": preview.scope.actor, "repo_id": preview.scope.repo_id,
                "server_id": preview.scope.server_id, "session": preview.scope.session,
            }):
                return None
            self._previews.move_to_end(preview_id)
            return preview

    def _cleanup_locked(self, now: float) -> None:
        for key in [key for key, cap in self._capabilities.items() if cap.expires_at <= now]:
            self._capabilities.pop(key, None)
        for key in [key for key, preview in self._previews.items() if preview.expires_at <= now]:
            preview = self._previews.pop(key)
            self._preview_total -= len(preview.data)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            for _, queue in self._subscribers.values():
                while not queue.empty():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                queue.put_nowait({"_close": True})
            self._subscribers.clear()
            self._capabilities.clear()
            self._previews.clear()
            self._preview_total = 0
            self._replay.clear()

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            self._cleanup_locked(time.time())
            return {
                "subscribers": len(self._subscribers),
                "capabilities": len(self._capabilities),
                "previews": len(self._previews),
            }


def sse_event(event: dict[str, Any]) -> bytes:
    public = LiveActivity._public_event(event)
    data = json.dumps(public, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['id']}\nevent: activity\ndata: {data}\n\n".encode("utf-8")


def _bearer(request: Request) -> str:
    value = request.headers.get("authorization", "")
    return value[7:] if value.lower().startswith("bearer ") else ""


def _cors_headers() -> dict[str, str]:
    # Capabilities, not cookies or ambient browser credentials, authorize these
    # read-only responses. The widget CSP separately limits the destination.
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Authorization, Last-Event-ID, Accept",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
    }


def create_live_activity_router(activity: LiveActivity) -> APIRouter:
    router = APIRouter()

    @router.options("/live/activity/stream")
    @router.options("/live/activity/preview/{preview_id}")
    async def live_options(preview_id: str | None = None):
        return Response(status_code=204, headers=_cors_headers())

    @router.get("/live/activity/stream")
    async def live_stream(request: Request):
        token = _bearer(request)
        try:
            last_id = max(0, int(request.headers.get("last-event-id", "0") or 0))
        except ValueError:
            last_id = 0
        try:
            subscription = await activity.subscribe(token, last_event_id=last_id)
        except PermissionError:
            return Response(status_code=401, headers={**_cors_headers(), "Cache-Control": "no-store"})

        async def events():
            try:
                yield b": connected\n\n"
                while not await request.is_disconnected():
                    if time.time() >= subscription.expires_at:
                        yield b"event: capability_expired\ndata: {}\n\n"
                        break
                    event = await subscription.get(15.0)
                    if event is None:
                        yield b": heartbeat\n\n"
                    elif event.get("_close"):
                        break
                    else:
                        yield sse_event(event)
            finally:
                await subscription.close()

        return StreamingResponse(
            events(), media_type="text/event-stream",
            headers={
                **_cors_headers(), "Cache-Control": "no-store, no-transform",
                "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/live/activity/preview/{preview_id}")
    async def live_preview(request: Request, preview_id: str):
        preview = await activity.get_preview(_bearer(request), preview_id)
        if preview is None:
            return Response(status_code=404, headers={**_cors_headers(), "Cache-Control": "no-store"})
        return Response(
            preview.data, media_type=preview.mime_type,
            headers={**_cors_headers(), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    return router
