from __future__ import annotations

import json
import math
import time
from typing import Any

import jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from devmesh_studio import __version__
from devmesh_studio.core.permissions import ApprovalRequired
from devmesh_studio.core.storage import Storage
from .auth import public_url, verify_access
from .tool_registry import ToolRegistry
from .tool_widget import TOOL_WIDGET_HTML, TOOL_WIDGET_MIME, TOOL_WIDGET_URI, widget_resource

MODERN_PROTOCOL = "2026-07-28"
LEGACY_PROTOCOLS = {"2025-11-25", "2025-06-18", "2025-03-26"}
INSTRUCTIONS = (
    "DevMesh is a local coding runtime. Prefer repository-specific read/search/code-intelligence "
    "tools before editing. Review diffs after mutations. Approval-gated calls return an approval id; "
    "approve them in DevMesh Desktop and retry the same call."
)


def _server_meta() -> dict[str, Any]:
    return {
        "io.modelcontextprotocol/serverInfo": {
            "name": "DevMesh Studio",
            "version": __version__,
        }
    }


def _request_protocol(request: Request, params: dict[str, Any]) -> str | None:
    # Modern HTTP clients normally send MCP-Protocol-Version. The JSON-RPC
    # _meta copy is accepted too so stdio-style envelopes proxied over HTTP
    # continue to work.
    header = request.headers.get("mcp-protocol-version")
    if header:
        return header.strip()
    meta = params.get("_meta") if isinstance(params, dict) else None
    if isinstance(meta, dict):
        value = meta.get("io.modelcontextprotocol/protocolVersion")
        return str(value) if value else None
    return None


def _estimated_tokens(value: Any) -> int:
    """Small dependency-free estimate for tool payload observability."""
    raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return math.ceil(len(raw.encode("utf-8")) / 4) if raw else 0


def create_mcp_router(storage: Storage, registry: ToolRegistry) -> APIRouter:
    router = APIRouter()

    def unauthorized():
        return JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": f'Bearer resource_metadata="{public_url(storage)}/.well-known/oauth-protected-resource"'},
        )

    @router.get("/mcp")
    async def mcp_get():
        return Response(status_code=405, headers={"Allow": "POST"})

    @router.post("/mcp")
    async def mcp(request: Request):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return unauthorized()
        try:
            claims = verify_access(storage, auth[7:])
        except jwt.PyJWTError:
            return unauthorized()

        body = await request.json()
        method = body.get("method")
        request_id = body.get("id")
        params = body.get("params") or {}
        protocol = _request_protocol(request, params)
        modern = protocol == MODERN_PROTOCOL or method == "server/discover"

        if request_id is None:
            return Response(status_code=202)

        def ok(result: dict[str, Any]):
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

        def err(code: int, message: str):
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

        # MCP 2026-07-28 discovery path. Modern clients use this instead of
        # initialize; legacy clients continue using initialize below.
        if method == "server/discover":
            return ok({
                "resultType": "complete",
                "supportedVersions": [MODERN_PROTOCOL],
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                "_meta": _server_meta(),
                "instructions": INSTRUCTIONS,
                "ttlMs": 300_000,
                "cacheScope": "private",
            })

        if method == "initialize":
            requested = params.get("protocolVersion", "2025-11-25")
            supported = requested if requested in LEGACY_PROTOCOLS else "2025-11-25"
            return ok({
                "protocolVersion": supported,
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                "serverInfo": {"name": "DevMesh Studio", "version": __version__},
                "instructions": INSTRUCTIONS,
            })

        if method == "ping":
            return ok({})

        if method == "tools/list":
            result: dict[str, Any] = {"tools": registry.definitions()}
            if modern:
                result.update({
                    "resultType": "complete",
                    "_meta": _server_meta(),
                    "ttlMs": 60_000,
                    "cacheScope": "private",
                })
            return ok(result)

        if method == "resources/list":
            result = {"resources": [widget_resource()]}
            if modern:
                result.update({"resultType": "complete", "_meta": _server_meta(), "ttlMs": 300_000, "cacheScope": "private"})
            return ok(result)

        if method == "resources/read":
            if params.get("uri") != TOOL_WIDGET_URI:
                return err(-32002, "resource not found")
            result = {
                "contents": [{
                    "uri": TOOL_WIDGET_URI,
                    "mimeType": TOOL_WIDGET_MIME,
                    "text": TOOL_WIDGET_HTML,
                    "_meta": {"ui": {"prefersBorder": False}},
                }]
            }
            if modern:
                result.update({"resultType": "complete", "_meta": _server_meta(), "ttlMs": 300_000, "cacheScope": "private"})
            return ok(result)

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            started = time.perf_counter()
            try:
                data = await registry.dispatch(claims.get("sub", "chatgpt"), name, arguments)
                duration_ms = round((time.perf_counter() - started) * 1000)
                usage = {"input_tokens": _estimated_tokens(arguments), "output_tokens": _estimated_tokens(data), "estimated": True}
                result: dict[str, Any] = {
                    "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2, default=str)}],
                    "structuredContent": {"tool": name, "result": data, "status": "ok", "duration_ms": duration_ms, "usage": usage},
                    "isError": False,
                }
                if modern:
                    result["resultType"] = "complete"
                    result["_meta"] = _server_meta()
                return ok(result)
            except ApprovalRequired as exc:
                duration_ms = round((time.perf_counter() - started) * 1000)
                data = {
                    "approval_required": True,
                    "approval_id": exc.approval_id,
                    "action": exc.action,
                    "resource": exc.resource,
                    "message": "Approve this request in DevMesh Desktop, then retry the same tool call.",
                }
                result = {
                    "content": [{"type": "text", "text": json.dumps(data, indent=2)}],
                    "structuredContent": {"tool": name, "result": data, "status": "approval_required", "duration_ms": duration_ms, "usage": {"input_tokens": _estimated_tokens(arguments), "output_tokens": _estimated_tokens(data), "estimated": True}},
                    "isError": False,
                }
                if modern:
                    result["resultType"] = "complete"
                    result["_meta"] = _server_meta()
                return ok(result)
            except Exception as exc:
                duration_ms = round((time.perf_counter() - started) * 1000)
                data = {"error": f"{type(exc).__name__}: {exc}"}
                result = {
                    "content": [{"type": "text", "text": data["error"]}],
                    "structuredContent": {"tool": name, "result": data, "status": "error", "duration_ms": duration_ms, "usage": {"input_tokens": _estimated_tokens(arguments), "output_tokens": _estimated_tokens(data), "estimated": True}},
                    "isError": True,
                }
                if modern:
                    result["resultType"] = "complete"
                    result["_meta"] = _server_meta()
                return ok(result)

        return err(-32601, f"method not found: {method}")

    return router
