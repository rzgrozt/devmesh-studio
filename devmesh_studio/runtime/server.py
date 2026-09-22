from __future__ import annotations

import argparse
import json
from fastapi import FastAPI
import uvicorn

from devmesh_studio import __version__
from devmesh_studio.core.storage import Storage
from .auth import authorization_server_metadata, create_auth_router, public_url
from .mcp_http import create_mcp_router
from .live_activity import create_live_activity_router
from .tool_registry import ToolRegistry
from .tool_widget import TOOL_WIDGET_MIME, TOOL_WIDGET_URI

storage = Storage()
registry = ToolRegistry(storage)
app = FastAPI(title="DevMesh Studio", version=__version__)
app.include_router(create_auth_router(storage))
app.include_router(create_mcp_router(storage, registry))
app.include_router(create_live_activity_router(registry.live_activity))


@app.get("/")
async def root():
    return {"name": "DevMesh Studio", "version": __version__, "mcp": public_url(storage) + "/mcp", "repositories": len(storage.list_repositories(enabled_only=True))}


@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "version": __version__,
        "tools": len(registry.definitions()),
        "mcp_apps_ui": True,
        "widget_uri": TOOL_WIDGET_URI,
        "widget_mime": TOOL_WIDGET_MIME,
    }


@app.on_event("shutdown")
async def shutdown_runtime():
    # Stateful downstream MCPs (CUA, browser automation, etc.) may own child
    # processes and application state. Close those workers before the gateway
    # exits so restarts never leave orphaned runtimes behind.
    await registry.mcp.close_all()
    await registry.live_activity.close()

    # Language servers are child processes of the MCP runtime. Stop them
    # explicitly so restarting DevMesh never leaves orphaned LSP processes.
    registry.code.lsp.stop_all()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(storage.get_setting("local_port", "8000") or 8000))
    parser.add_argument(
        "--oauth-self-test",
        action="store_true",
        help="Verify that this packaged gateway advertises DCR and PKCE S256, then exit.",
    )
    args = parser.parse_args()
    if args.oauth_self_test:
        metadata = authorization_server_metadata(storage)
        openapi_paths = app.openapi().get("paths", {})
        failures = []
        if not metadata.get("registration_endpoint"):
            failures.append("registration_endpoint missing")
        if "S256" not in metadata.get("code_challenge_methods_supported", []):
            failures.append("PKCE S256 missing")
        for path, method in (
            ("/oauth/register", "POST"),
            ("/oauth/authorize", "GET"),
            ("/oauth/token", "POST"),
        ):
            if method.lower() not in openapi_paths.get(path, {}):
                failures.append(f"route missing: {method} {path}")
        result = {
            "ok": not failures,
            "registration_endpoint": metadata.get("registration_endpoint"),
            "code_challenge_methods_supported": metadata.get("code_challenge_methods_supported", []),
            "failures": failures,
        }
        print(json.dumps(result, sort_keys=True))
        raise SystemExit(0 if not failures else 2)
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
