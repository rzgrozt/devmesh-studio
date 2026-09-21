from __future__ import annotations

import base64
import hashlib
import re
from fastapi import FastAPI
from fastapi.testclient import TestClient
from argon2 import PasswordHasher

from devmesh_studio.core.storage import Storage
from devmesh_studio.runtime.auth import create_auth_router
from devmesh_studio.runtime.mcp_http import create_mcp_router
from devmesh_studio.runtime.tool_registry import ToolRegistry


def challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def test_oauth_pkce_and_mcp_tool_list(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMESH_DATA_DIR", str(tmp_path / "data"))
    storage = Storage(tmp_path / "devmesh.db")
    storage.set_setting("public_url", "http://127.0.0.1:8000")
    storage.set_setting("username", "tester")
    storage.set_setting("password_hash", PasswordHasher().hash("very-long-test-password"))
    registry = ToolRegistry(storage)
    app = FastAPI(); app.include_router(create_auth_router(storage)); app.include_router(create_mcp_router(storage, registry))
    client = TestClient(app)

    reg = client.post("/oauth/register", json={"client_name":"test","redirect_uris":["http://127.0.0.1/callback"]})
    assert reg.status_code == 200
    client_id = reg.json()["client_id"]
    verifier = "v" * 64
    auth = client.get("/oauth/authorize", params={
        "client_id":client_id,"redirect_uri":"http://127.0.0.1/callback","response_type":"code",
        "code_challenge":challenge(verifier),"code_challenge_method":"S256"
    })
    assert auth.status_code == 200
    pending = re.search(r"name='pending_id' value='([^']+)'", auth.text).group(1)
    approve = client.post("/oauth/approve", data={"pending_id":pending,"username":"tester","password":"very-long-test-password"}, follow_redirects=False)
    assert approve.status_code == 302
    code = re.search(r"[?&]code=([^&]+)", approve.headers["location"]).group(1)
    token = client.post("/oauth/token", data={"grant_type":"authorization_code","client_id":client_id,"code":code,"redirect_uri":"http://127.0.0.1/callback","code_verifier":verifier})
    assert token.status_code == 200
    access = token.json()["access_token"]
    headers={"Authorization":f"Bearer {access}"}
    init=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}})
    assert init.json()["result"]["serverInfo"]["name"] == "DevMesh Studio"
    tools=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}).json()["result"]["tools"]
    names={t["name"] for t in tools}
    assert {"fs_read","fs_edit","terminal_exec","git_status","agent_delegate","mcp_call","approval_list"}.issubset(names)
    assert len(tools) == 55
    widget_tools={t["name"] for t in tools if "_meta" in t}
    assert widget_tools == {"fs_write","fs_edit","patch_preview","patch_apply","patch_revert","git_diff","git_show"}
    assert all(t["_meta"]["ui"]["resourceUri"] == "ui://devmesh/change-review-v3.html" for t in tools if "_meta" in t)
    assert all(t["outputSchema"]["type"] == "object" for t in tools)

    repo_dir=tmp_path/"repo"; repo_dir.mkdir(); (repo_dir/"hello.txt").write_text("hello world\n",encoding="utf-8")
    repo=storage.add_repository(repo_dir)
    # Productive defaults allow repository-confined edits. This test explicitly
    # switches edit back to ask so the OAuth/MCP approval-retry flow stays covered.
    storage.upsert_permission(repo["id"],"edit","*","ask",200)
    read=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"fs_read","arguments":{"repo_id":repo["id"],"path":"hello.txt"}}}).json()["result"]
    assert "hello world" in read["structuredContent"]["result"]["text"]
    edit=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"fs_edit","arguments":{"repo_id":repo["id"],"path":"hello.txt","old":"world","new":"chatgpt"}}}).json()["result"]
    approval=edit["structuredContent"]["result"]
    assert approval["approval_required"] is True
    storage.resolve_approval(approval["approval_id"],"approved_once")
    edit2=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"fs_edit","arguments":{"repo_id":repo["id"],"path":"hello.txt","old":"world","new":"chatgpt"}}}).json()["result"]
    assert edit2["isError"] is False
    assert "hello chatgpt" in (repo_dir/"hello.txt").read_text()


def test_modern_2026_discovery_and_tool_results(tmp_path, monkeypatch):
    """Modern MCP clients can use the 2026-07-28 stateless discovery path."""
    monkeypatch.setenv("DEVMESH_DATA_DIR", str(tmp_path / "data-modern"))
    storage = Storage(tmp_path / "modern.db")
    storage.set_setting("public_url", "http://127.0.0.1:8000")
    storage.set_setting("username", "tester")
    storage.set_setting("password_hash", PasswordHasher().hash("very-long-test-password"))
    registry = ToolRegistry(storage)
    app = FastAPI(); app.include_router(create_auth_router(storage)); app.include_router(create_mcp_router(storage, registry))
    client = TestClient(app)

    # Reuse the real OAuth/DCR/PKCE flow rather than minting a token directly.
    reg = client.post("/oauth/register", json={"client_name":"modern","redirect_uris":["http://127.0.0.1/callback"]})
    client_id = reg.json()["client_id"]
    verifier = "m" * 64
    auth = client.get("/oauth/authorize", params={
        "client_id":client_id,"redirect_uri":"http://127.0.0.1/callback","response_type":"code",
        "code_challenge":challenge(verifier),"code_challenge_method":"S256"
    })
    pending = re.search(r"name='pending_id' value='([^']+)'", auth.text).group(1)
    approve = client.post("/oauth/approve", data={"pending_id":pending,"username":"tester","password":"very-long-test-password"}, follow_redirects=False)
    code = re.search(r"[?&]code=([^&]+)", approve.headers["location"]).group(1)
    token = client.post("/oauth/token", data={"grant_type":"authorization_code","client_id":client_id,"code":code,"redirect_uri":"http://127.0.0.1/callback","code_verifier":verifier})
    access = token.json()["access_token"]
    headers={"Authorization":f"Bearer {access}", "MCP-Protocol-Version":"2026-07-28"}
    meta={
        "io.modelcontextprotocol/protocolVersion":"2026-07-28",
        "io.modelcontextprotocol/clientInfo":{"name":"modern-test","version":"1.0"},
        "io.modelcontextprotocol/clientCapabilities":{},
    }

    discover=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":"d1","method":"server/discover","params":{"_meta":meta}})
    result=discover.json()["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == ["2026-07-28"]
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["capabilities"]["resources"]["listChanged"] is False
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "DevMesh Studio"

    listed=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":"d2","method":"tools/list","params":{"_meta":meta}}).json()["result"]
    assert listed["resultType"] == "complete"
    assert len(listed["tools"]) == 55
    resources=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":"d2r","method":"resources/list","params":{"_meta":meta}}).json()["result"]
    assert resources["resources"][0]["mimeType"] == "text/html;profile=mcp-app"
    widget=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":"d2w","method":"resources/read","params":{"uri":"ui://devmesh/change-review-v3.html","_meta":meta}}).json()["result"]
    assert "Changes ready" in widget["contents"][0]["text"]

    repo_dir=tmp_path/"modern-repo"; repo_dir.mkdir(); (repo_dir/"hello.txt").write_text("modern mcp\n",encoding="utf-8")
    repo=storage.add_repository(repo_dir)
    called=client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":"d3","method":"tools/call","params":{"name":"fs_read","arguments":{"repo_id":repo["id"],"path":"hello.txt"},"_meta":meta}}).json()["result"]
    assert called["resultType"] == "complete"
    assert called["isError"] is False
    assert "modern mcp" in called["structuredContent"]["result"]["text"]
    assert called["structuredContent"]["usage"]["estimated"] is True


def test_refresh_token_is_reusable_and_sliding(tmp_path):
    storage = Storage(tmp_path / "refresh.db")
    storage.save_refresh("stable-refresh", "client-1", "tester", "offline_access", 4_000_000_000)
    first = storage.rotate_refresh("stable-refresh")
    second = storage.rotate_refresh("stable-refresh")
    assert first and second
    assert first["client_id"] == second["client_id"] == "client-1"


def test_credential_rotation_revokes_oauth_sessions(tmp_path, monkeypatch):
    """Credential changes invalidate access/refresh state without deleting the client."""
    import jwt as pyjwt
    import pytest

    from devmesh_studio.core.secrets import SecretStore
    from devmesh_studio.runtime.auth import issue_access, verify_access

    monkeypatch.setenv("DEVMESH_DATA_DIR", str(tmp_path / "rotation-data"))
    monkeypatch.setattr(SecretStore, "_keyring", lambda self: None)

    storage = Storage(tmp_path / "rotation.db")
    storage.set_setting("public_url", "http://127.0.0.1:8000")
    storage.register_client("client-1", "ChatGPT", ["http://127.0.0.1/callback"])

    secrets_store = SecretStore()
    secrets_store.set("jwt_secret", "old-signing-secret-that-is-long-enough-for-hs256-tests")
    access, _ = issue_access(storage, "tester", "client-1", "repo:read offline_access")
    assert verify_access(storage, access)["client_id"] == "client-1"

    storage.save_refresh("refresh-old", "client-1", "tester", "repo:read offline_access", 4_000_000_000)
    storage.revoke_oauth_sessions()
    secrets_store.rotate("jwt_secret", 64)

    assert storage.get_client("client-1") is not None
    assert storage.rotate_refresh("refresh-old") is None
    with pytest.raises(pyjwt.InvalidTokenError):
        verify_access(storage, access)
