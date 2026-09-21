from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from devmesh_studio.core import platform as host_platform
from devmesh_studio.core.storage import Storage
from devmesh_studio.services import runtime_supervisor as supervisor_module
from devmesh_studio.services.runtime_supervisor import RuntimeSupervisor
from devmesh_studio.tools import terminal


def test_native_shell_arguments_are_platform_specific(monkeypatch):
    monkeypatch.setattr(host_platform, "IS_WINDOWS", True)
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    assert host_platform.shell_argv("echo hello") == [
        r"C:\Windows\System32\cmd.exe", "/d", "/s", "/c", "echo hello"
    ]

    monkeypatch.setattr(host_platform, "IS_WINDOWS", False)
    assert host_platform.shell_argv("printf ok") == ["/bin/bash", "-lc", "printf ok"]


def test_windows_command_line_round_trip(monkeypatch):
    monkeypatch.setattr(host_platform, "IS_WINDOWS", True)
    args = ["--stdio", r"C:\Program Files\DevMesh\server.js", "plain"]
    assert host_platform.split_command(host_platform.join_command(args)) == args


def test_windows_executable_suffix_is_safe_command(monkeypatch):
    monkeypatch.setattr(terminal, "IS_WINDOWS", True)
    assert terminal._is_safe_developer_command(["git.exe", "status"], "git.exe status")
    assert terminal._is_safe_developer_command(["python.exe", "-m", "pytest"], "python.exe -m pytest")


def test_cloudflared_windows_download_uses_exe_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMESH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(supervisor_module, "IS_WINDOWS", True)
    monkeypatch.setattr(supervisor_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(supervisor_module.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(supervisor_module.shutil, "which", lambda _: None)
    requested: list[str] = []

    def fake_download(url: str, target: Path):
        requested.append(url)
        Path(target).write_bytes(b"cloudflared")

    monkeypatch.setattr(supervisor_module.urllib.request, "urlretrieve", fake_download)
    binary = RuntimeSupervisor(Storage(tmp_path / "data" / "devmesh.db")).ensure_cloudflared()
    assert binary.name == "cloudflared.exe"
    assert requested[0].endswith("/cloudflared-windows-amd64.exe")
    assert binary.read_bytes() == b"cloudflared"
