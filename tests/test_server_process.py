from __future__ import annotations

import socket
import urllib.request
from argon2 import PasswordHasher

from devmesh_studio.core.storage import Storage
from devmesh_studio.services.runtime_supervisor import RuntimeSupervisor


def free_port():
    s=socket.socket();s.bind(("127.0.0.1",0));port=s.getsockname()[1];s.close();return port


def test_supervisor_starts_real_gateway_without_tunnel(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMESH_DATA_DIR", str(tmp_path))
    storage=Storage(tmp_path/"devmesh.db")
    storage.set_setting("username","tester")
    storage.set_setting("password_hash",PasswordHasher().hash("very-long-test-password"))
    storage.set_setting("local_port",str(free_port()))
    storage.set_setting("auto_tunnel","0")
    sup=RuntimeSupervisor(storage)
    try:
        sup.start(use_tunnel=False)
        assert sup.gateway_running()
        with urllib.request.urlopen(sup.local_url+"/healthz",timeout=2) as r:
            body=r.read().decode()
        assert '"ok":true' in body.replace(" ","")
    finally:
        sup.stop()
