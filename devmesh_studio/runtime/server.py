from __future__ import annotations

import argparse
from fastapi import FastAPI
import uvicorn

from devmesh_studio import __version__
from devmesh_studio.core.storage import Storage
from .auth import create_auth_router, public_url
from .mcp_http import create_mcp_router
from .tool_registry import ToolRegistry
from .tool_widget import TOOL_WIDGET_MIME, TOOL_WIDGET_URI

storage = Storage()
registry = ToolRegistry(storage)
app = FastAPI(title="DevMesh Studio", version=__version__)
app.include_router(create_auth_router(storage))
app.include_router(create_mcp_router(storage, registry))


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
    # Language servers are child processes of the MCP runtime. Stop them
    # explicitly so restarting DevMesh never leaves orphaned LSP processes.
    registry.code.lsp.stop_all()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(storage.get_setting("local_port", "8000") or 8000))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
