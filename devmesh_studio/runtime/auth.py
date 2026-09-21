from __future__ import annotations

import base64
import hashlib
import html
import secrets
import time
from urllib.parse import urlencode, urlparse

import jwt
from argon2 import PasswordHasher
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from devmesh_studio.core.secrets import SecretStore
from devmesh_studio.core.storage import Storage

SCOPES = "registry:read workspace:list repo:read repo:write terminal:run git:write tests:run agent:delegate tool:invoke offline_access"
ph = PasswordHasher()


def public_url(storage: Storage) -> str:
    return (storage.get_setting("public_url", "http://127.0.0.1:8000") or "").rstrip("/")


def protected_resource_metadata_url(storage: Storage) -> str:
    return public_url(storage) + "/.well-known/oauth-protected-resource/mcp"


def _valid_redirect_uri(value: str) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.fragment or not parsed.scheme:
            return False
        if parsed.scheme == "https":
            return bool(parsed.netloc)
        return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    except ValueError:
        return False


def b64url_sha256(value: str) -> str:
    digest = hashlib.sha256(value.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def jwt_secret() -> str:
    return SecretStore().get_or_create("jwt_secret", 64)


def issue_access(storage: Storage, subject: str, client_id: str, scope: str) -> tuple[str, int]:
    now = int(time.time())
    minutes = int(storage.get_setting("access_token_minutes", "60") or 60)
    exp = now + minutes * 60
    origin = public_url(storage)
    payload = {
        "iss": origin,
        "sub": subject,
        "aud": origin + "/mcp",
        "client_id": client_id,
        "scope": scope,
        "iat": now,
        "exp": exp,
    }
    return jwt.encode(payload, jwt_secret(), algorithm="HS256"), exp


def verify_access(storage: Storage, token: str) -> dict:
    origin = public_url(storage)
    return jwt.decode(token, jwt_secret(), algorithms=["HS256"], audience=origin + "/mcp", issuer=origin)


def create_auth_router(storage: Storage) -> APIRouter:
    router = APIRouter()

    def resource_metadata():
        origin = public_url(storage)
        return {
            "resource": origin + "/mcp",
            "resource_name": "DevMesh Studio MCP",
            "authorization_servers": [origin],
            "scopes_supported": SCOPES.split(),
            "bearer_methods_supported": ["header"],
        }

    @router.get("/.well-known/oauth-protected-resource")
    async def protected_resource():
        # Compatibility location used by older MCP hosts.
        return resource_metadata()

    @router.get("/.well-known/oauth-protected-resource/mcp")
    async def protected_resource_for_mcp():
        # RFC 9728 path-aware discovery location for the /mcp resource.
        return resource_metadata()

    @router.get("/.well-known/oauth-authorization-server")
    async def auth_metadata():
        origin = public_url(storage)
        return {
            "issuer": origin,
            "authorization_endpoint": origin + "/oauth/authorize",
            "token_endpoint": origin + "/oauth/token",
            "registration_endpoint": origin + "/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": SCOPES.split(),
            "authorization_response_iss_parameter_supported": True,
        }

    @router.get("/.well-known/openid-configuration")
    async def openid_compatibility_metadata():
        # Some MCP hosts probe the OIDC discovery path before OAuth AS metadata.
        return await auth_metadata()

    @router.post("/oauth/register", status_code=201)
    async def register(request: Request):
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "invalid_client_metadata") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "invalid_client_metadata")
        redirect_uris = body.get("redirect_uris") or []
        if not redirect_uris or not all(isinstance(u, str) for u in redirect_uris):
            raise HTTPException(400, "redirect_uris required")
        if any(not _valid_redirect_uri(u) for u in redirect_uris):
            raise HTTPException(400, "redirect URIs must be HTTPS or loopback localhost")
        if body.get("token_endpoint_auth_method", "none") != "none":
            raise HTTPException(400, "only public PKCE clients are supported")
        client_id = "dvm_" + secrets.token_urlsafe(24)
        storage.register_client(client_id, body.get("client_name", "MCP client"), redirect_uris)
        return {
            "client_id": client_id,
            "client_name": body.get("client_name", "MCP client"),
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "client_id_issued_at": int(time.time()),
        }

    @router.get("/oauth/authorize", response_class=HTMLResponse)
    async def authorize(
        client_id: str,
        redirect_uri: str,
        response_type: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
        scope: str = SCOPES,
        state: str = "",
        resource: str | None = None,
    ):
        client = storage.get_client(client_id)
        if not client or redirect_uri not in client["redirect_uris"]:
            raise HTTPException(400, "unknown client or redirect_uri")
        if response_type != "code" or code_challenge_method != "S256":
            raise HTTPException(400, "authorization code + PKCE S256 required")
        if not set(scope.split()).issubset(set(SCOPES.split())):
            raise HTTPException(400, "unsupported scope")
        expected_resource = public_url(storage) + "/mcp"
        if resource and resource.rstrip("/") != expected_resource.rstrip("/"):
            raise HTTPException(400, "unsupported resource")
        pending_id = secrets.token_urlsafe(24)
        storage.save_pending(
            pending_id,
            {"client_id": client_id, "redirect_uri": redirect_uri, "scope": scope, "state": state, "code_challenge": code_challenge, "resource": resource or expected_resource},
            int(time.time()) + 600,
        )
        return HTMLResponse(
            f"""<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>
            <title>Authorize DevMesh</title><style>body{{font:16px system-ui;max-width:560px;margin:10vh auto;padding:28px;background:#111;color:#eee}}input,button{{width:100%;box-sizing:border-box;padding:12px;margin:7px 0;border-radius:8px}}button{{cursor:pointer}}code{{word-break:break-all}}</style></head>
            <body><h2>Authorize DevMesh Studio</h2><p>Client: <b>{html.escape(str(client['client_name']))}</b></p><p>Scopes: <code>{html.escape(scope)}</code></p>
            <form method='post' action='/oauth/approve'><input type='hidden' name='pending_id' value='{pending_id}'>
            <label>Username</label><input name='username' autocomplete='username' required>
            <label>Password</label><input type='password' name='password' autocomplete='current-password' required>
            <button type='submit'>Authorize</button></form></body></html>"""
        )

    @router.post("/oauth/approve")
    async def approve(pending_id: str = Form(...), username: str = Form(...), password: str = Form(...)):
        pending = storage.pop_pending(pending_id)
        if not pending:
            raise HTTPException(400, "authorization request expired")
        expected_user = storage.get_setting("username", "devmesh") or "devmesh"
        password_hash = storage.get_setting("password_hash")
        if not password_hash or username != expected_user:
            raise HTTPException(401, "invalid credentials")
        try:
            ph.verify(password_hash, password)
        except Exception:
            raise HTTPException(401, "invalid credentials")
        code = secrets.token_urlsafe(36)
        storage.save_code(code, pending["client_id"], pending["redirect_uri"], pending["scope"], pending["code_challenge"])
        params = {"code": code, "iss": public_url(storage)}
        if pending.get("state"):
            params["state"] = pending["state"]
        sep = "&" if "?" in pending["redirect_uri"] else "?"
        return RedirectResponse(pending["redirect_uri"] + sep + urlencode(params), status_code=302)

    @router.post("/oauth/token")
    async def token(request: Request):
        form = await request.form()
        grant = str(form.get("grant_type") or "")
        client_id = str(form.get("client_id") or "")
        resource = str(form.get("resource") or "")
        if resource and resource.rstrip("/") != (public_url(storage) + "/mcp").rstrip("/"):
            return JSONResponse({"error": "invalid_target"}, status_code=400)
        if grant == "authorization_code":
            code = str(form.get("code") or "")
            redirect_uri = str(form.get("redirect_uri") or "")
            verifier = str(form.get("code_verifier") or "")
            row = storage.consume_code(code)
            if not row or row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if not verifier or b64url_sha256(verifier) != row["code_challenge"]:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            username = storage.get_setting("username", "devmesh") or "devmesh"
            access, _ = issue_access(storage, username, client_id, row["scope"])
            refresh = secrets.token_urlsafe(48)
            refresh_days = int(storage.get_setting("refresh_token_days", "3650") or 3650)
            storage.save_refresh(refresh, client_id, username, row["scope"], int(time.time()) + refresh_days * 86400)
            return {"access_token": access, "token_type": "Bearer", "expires_in": int(storage.get_setting("access_token_minutes","60") or 60)*60, "refresh_token": refresh, "scope": row["scope"]}
        if grant == "refresh_token":
            refresh = str(form.get("refresh_token") or "")
            row = storage.validate_refresh(refresh)
            if not row or row["client_id"] != client_id:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            access, _ = issue_access(storage, row["subject"], client_id, row["scope"])
            return {"access_token": access, "token_type": "Bearer", "expires_in": int(storage.get_setting("access_token_minutes","60") or 60)*60, "refresh_token": refresh, "scope": row["scope"]}
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    return router
