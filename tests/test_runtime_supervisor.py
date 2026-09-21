from __future__ import annotations

from devmesh_studio.services.runtime_supervisor import TUNNEL_RE


def test_tunnel_url_parser():
    line="INF +--------------------------------------------------------------------------------------------+ https://quick-fox-123.trycloudflare.com yay"
    m=TUNNEL_RE.search(line)
    assert m and m.group(0)=="https://quick-fox-123.trycloudflare.com"

import os
import socket
import time
from pathlib import Path
from argon2 import PasswordHasher
from devmesh_studio.core.storage import Storage
from devmesh_studio.services.runtime_supervisor import RuntimeSupervisor


def test_status_does_not_probe_tailscale_on_ui_poll(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMESH_DATA_DIR", str(tmp_path / "data"))
    storage = Storage(tmp_path / "status.db")
    storage.set_setting("tunnel_mode", "tailscale")
    storage.set_setting("public_url", "https://devmesh.example.ts.net")
    supervisor = RuntimeSupervisor(storage)
    monkeypatch.setattr(supervisor, "tailscale_public_url", lambda: (_ for _ in ()).throw(AssertionError("slow probe")))
    monkeypatch.setattr(supervisor, "tunnel_running", lambda: (_ for _ in ()).throw(AssertionError("slow probe")))
    status = supervisor.status()
    assert status["public_url"] == "https://devmesh.example.ts.net"
    assert status["tunnel"] is True


def _free_port():
    s=socket.socket(); s.bind(("127.0.0.1",0)); port=s.getsockname()[1]; s.close(); return port


def test_supervisor_fake_quick_tunnel_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMESH_DATA_DIR", str(tmp_path / "data"))
    storage=Storage(tmp_path/"data"/"devmesh.db")
    storage.set_setting("username","tester")
    storage.set_setting("password_hash",PasswordHasher().hash("very-long-test-password"))
    storage.set_setting("local_port",str(_free_port()))
    storage.set_setting("tunnel_mode","quick")
    fake=tmp_path/"cloudflared"
    fake.write_text("#!/bin/sh\necho 'INF https://fake-devmesh-123.trycloudflare.com'\nsleep 30\n")
    fake.chmod(0o755)
    sup=RuntimeSupervisor(storage)
    monkeypatch.setattr(sup,"ensure_cloudflared",lambda:fake)
    try:
        sup.start(use_tunnel=True)
        for _ in range(50):
            if sup.public_url: break
            time.sleep(0.05)
        assert sup.public_url == "https://fake-devmesh-123.trycloudflare.com"
        assert storage.get_setting("public_url") == sup.public_url
        assert sup.status()["mcp_url"].endswith("/mcp")
        assert sup.gateway_running() and sup.tunnel_running()
    finally:
        sup.stop()
