from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

import psutil

from devmesh_studio.core.paths import bin_dir, runtime_dir
from devmesh_studio.core.platform import IS_WINDOWS, executable, process_group_kwargs
from devmesh_studio.core.storage import Storage


TUNNEL_RE = re.compile(
    r"https://[A-Za-z0-9-]+\.trycloudflare\.com"
)


class RuntimeSupervisor:
    def __init__(
        self,
        storage: Storage,
        on_event: Callable[[str], None] | None = None,
    ):
        self.storage = storage
        self.on_event = on_event or (lambda _: None)

        self.gateway: subprocess.Popen | None = None
        self.tunnel: subprocess.Popen | None = None

        self.public_url: str | None = None

        self._lock = threading.RLock()
        self._tunnel_thread: threading.Thread | None = None
        self._gateway_pid_file = runtime_dir() / "gateway.pid"
        self._cleanup_stale_gateway()

    # -------------------------------------------------------------------------
    # Basic configuration
    # -------------------------------------------------------------------------

    @property
    def local_port(self) -> int:
        return int(
            self.storage.get_setting(
                "local_port",
                "8000",
            )
            or 8000
        )

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    @property
    def tunnel_mode(self) -> str:
        mode = (
            self.storage.get_setting(
                "tunnel_mode",
                "tailscale",
            )
            or "tailscale"
        ).strip().lower()

        if mode not in {
            "tailscale",
            "quick",
        }:
            return "tailscale"

        return mode

    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------

    def emit(self, text: str) -> None:
        self.on_event(text)

    # -------------------------------------------------------------------------
    # Gateway state
    # -------------------------------------------------------------------------

    def gateway_running(self) -> bool:
        return bool(
            self.gateway
            and self.gateway.poll() is None
        )

    def _remember_gateway_pid(self) -> None:
        if not self.gateway:
            return
        self._gateway_pid_file.write_text(str(self.gateway.pid), encoding="utf-8")

    def _forget_gateway_pid(self) -> None:
        try:
            self._gateway_pid_file.unlink(missing_ok=True)
        except Exception:
            pass

    def _cleanup_stale_gateway(self) -> None:
        """Terminate an orphaned DevMesh gateway left by a previous app process.

        The PID file is only trusted after verifying the process command line is
        actually DevMesh's local MCP server. This avoids killing an unrelated
        process that happens to reuse the same PID later.
        """
        if not self._gateway_pid_file.exists():
            return

        try:
            raw = self._gateway_pid_file.read_text(encoding="utf-8").strip()
            pid = int(raw)
        except Exception:
            self._forget_gateway_pid()
            return

        try:
            proc = psutil.Process(pid)
            cmdline = proc.cmdline()
            joined = " ".join(cmdline)
            executable_name = Path(cmdline[0]).name.lower() if cmdline else ""
            if (
                "devmesh_studio.runtime.server" not in joined
                and executable_name not in {"devmesh-server", "devmesh-server.exe"}
            ):
                self._forget_gateway_pid()
                return

            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

            self.emit(f"Cleaned up stale DevMesh gateway process {pid}")
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:
            self.emit(f"Could not clean up stale DevMesh gateway process {pid}: {exc}")
        finally:
            self._forget_gateway_pid()

    # -------------------------------------------------------------------------
    # Tailscale
    # -------------------------------------------------------------------------

    def ensure_tailscale(self) -> Path:
        program_files = Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
        local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        found = executable(
            "tailscale",
            program_files / "Tailscale" / "tailscale.exe",
            local_app_data / "Tailscale" / "tailscale.exe",
        )

        if found:
            return found

        raise RuntimeError(
            "Tailscale is not installed.\n\n"
            "Install Tailscale, sign in once, then enable Funnel "
            "for this device."
        )

    def tailscale_logged_in(self) -> bool:
        try:
            tailscale = self.ensure_tailscale()

            result = subprocess.run(
                [
                    str(tailscale),
                    "status",
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            if result.returncode != 0:
                return False

            payload = json.loads(
                result.stdout or "{}"
            )

            backend_state = str(
                payload.get("BackendState")
                or ""
            ).lower()

            if backend_state:
                return backend_state == "running"

            self_info = payload.get("Self") or {}

            return bool(
                self_info.get("DNSName")
                or self_info.get("TailscaleIPs")
            )

        except Exception:
            return False

    def tailscale_public_url(
        self,
    ) -> str | None:
        """
        Return this machine's stable Tailscale HTTPS hostname.

        Example:
            https://devmesh.example-tailnet.ts.net
        """

        try:
            tailscale = self.ensure_tailscale()

            result = subprocess.run(
                [
                    str(tailscale),
                    "status",
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            if result.returncode != 0:
                return None

            payload = json.loads(
                result.stdout or "{}"
            )

            self_info = payload.get("Self") or {}

            dns_name = str(
                self_info.get("DNSName")
                or ""
            ).strip()

            dns_name = dns_name.rstrip(".")

            if not dns_name:
                return None

            return f"https://{dns_name}"

        except Exception:
            return None

    def tailscale_funnel_running(
        self,
    ) -> bool:
        try:
            tailscale = self.ensure_tailscale()

            result = subprocess.run(
                [
                    str(tailscale),
                    "funnel",
                    "status",
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            if result.returncode != 0:
                return False

            raw = (
                result.stdout or ""
            ).strip()

            if not raw:
                return False

            if raw in {
                "{}",
                "null",
                "[]",
            }:
                return False

            try:
                payload = json.loads(raw)

                if not payload:
                    return False

            except json.JSONDecodeError:
                pass

            return True

        except Exception:
            return False

    def start_tailscale_funnel(
        self,
    ) -> None:
        tailscale = self.ensure_tailscale()

        if not self.tailscale_logged_in():
            raise RuntimeError(
                "Tailscale is installed but is not connected.\n\n"
                "Open Tailscale and sign in, or run:\n"
                f"    {'tailscale up' if IS_WINDOWS else 'sudo tailscale up'}\n\n"
                "Then sign in and try again."
            )

        # Funnel may already be configured persistently.
        if self.tailscale_funnel_running():
            public = self.tailscale_public_url()

            if public:
                self.public_url = public
                self.storage.set_setting(
                    "public_url",
                    public,
                )

                self.emit(
                    "Existing Tailscale Funnel detected: "
                    f"{public}/mcp"
                )

            return

        self.emit(
            "Starting stable Tailscale Funnel…"
        )

        argv = [
            str(tailscale),
            "funnel",
            "--yes",
            "--bg",
            "--https=443",
            self.local_url,
        ]

        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )

        except subprocess.TimeoutExpired as exc:
            stdout = ""

            if exc.stdout:
                stdout = (
                    exc.stdout.decode(
                        errors="replace"
                    )
                    if isinstance(
                        exc.stdout,
                        bytes,
                    )
                    else str(exc.stdout)
                )

            stderr = ""

            if exc.stderr:
                stderr = (
                    exc.stderr.decode(
                        errors="replace"
                    )
                    if isinstance(
                        exc.stderr,
                        bytes,
                    )
                    else str(exc.stderr)
                )

            detail = (
                stderr
                or stdout
                or "No output returned."
            ).strip()

            raise RuntimeError(
                "Tailscale Funnel did not finish configuring "
                "within 120 seconds.\n\n"
                "This commonly happens during the first Funnel "
                "authorization or HTTPS setup.\n\n"
                "Try this once in a terminal:\n\n"
                f"tailscale funnel --yes --bg --https=443 "
                f"{self.local_url}\n\n"
                "Then run:\n\n"
                "tailscale funnel status\n\n"
                f"Tailscale output:\n{detail}"
            ) from exc

        if result.returncode != 0:
            detail = (
                result.stderr
                or result.stdout
                or "Unknown Tailscale error."
            ).strip()

            operator_help = ""
            if not IS_WINDOWS:
                operator_help = (
                    "• your Linux user may control Tailscale\n\n"
                    "If necessary run:\n\n"
                    "sudo tailscale set --operator=$USER\n\n"
                )
            raise RuntimeError(
                "Could not start Tailscale Funnel.\n\n"
                "Check that:\n"
                "• Tailscale is signed in\n"
                "• MagicDNS is enabled\n"
                "• HTTPS is enabled for the tailnet\n"
                "• Funnel is permitted for this device\n"
                f"{operator_help}"
                "Then try again.\n\n"
                f"Tailscale output:\n{detail}"
            )

        public = self.tailscale_public_url()

        if not public:
            raise RuntimeError(
                "Tailscale Funnel was configured successfully, "
                "but DevMesh could not determine this device's "
                "stable .ts.net hostname."
            )

        self.public_url = public

        self.storage.set_setting(
            "public_url",
            public,
        )

        self.emit(
            "Stable Tailscale Funnel ready: "
            f"{public}/mcp"
        )

    # -------------------------------------------------------------------------
    # Cloudflare Quick Tunnel fallback
    # -------------------------------------------------------------------------

    def ensure_cloudflared(self) -> Path:
        found = shutil.which("cloudflared")

        if found:
            return Path(found)

        local = bin_dir() / ("cloudflared.exe" if IS_WINDOWS else "cloudflared")

        if (
            local.exists()
            and os.access(
                local,
                os.X_OK,
            )
        ):
            return local

        system = platform.system()
        machine = platform.machine().lower()

        if system in {"Linux", "Windows"}:
            if machine in {
                "x86_64",
                "amd64",
            }:
                asset = "cloudflared-windows-amd64.exe" if system == "Windows" else "cloudflared-linux-amd64"

            elif machine in {
                "aarch64",
                "arm64",
            }:
                asset = "cloudflared-windows-arm64.exe" if system == "Windows" else "cloudflared-linux-arm64"

            elif machine.startswith("arm") and system == "Linux":
                asset = (
                    "cloudflared-linux-arm"
                )

            else:
                raise RuntimeError(
                    "Unsupported architecture: "
                    f"{machine}"
                )

            url = (
                "https://github.com/"
                "cloudflare/cloudflared/"
                "releases/latest/download/"
                f"{asset}"
            )

            self.emit(
                "Downloading cloudflared "
                "for temporary fallback…"
            )

            tmp = local.with_suffix(".tmp")

            urllib.request.urlretrieve(
                url,
                tmp,
            )

            if not IS_WINDOWS:
                tmp.chmod(0o755)
            tmp.replace(local)

            return local

        if (
            system == "Darwin"
            and shutil.which("brew")
        ):
            subprocess.run(
                [
                    "brew",
                    "install",
                    "cloudflared",
                ],
                check=True,
            )

            found = shutil.which(
                "cloudflared"
            )

            if found:
                return Path(found)

        raise RuntimeError(
            "cloudflared is not installed and "
            "automatic installation is unavailable "
            "on this platform."
        )

    # -------------------------------------------------------------------------
    # Tunnel state
    # -------------------------------------------------------------------------

    def tunnel_running(self) -> bool:
        if self.tunnel_mode == "tailscale":
            return (
                self.tailscale_funnel_running()
            )

        return bool(
            self.tunnel
            and self.tunnel.poll() is None
        )

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def status(self, probe: bool = False) -> dict:
        """Return fast UI status; provider probing is opt-in.

        Tailscale CLI calls can take seconds. The desktop polls this method, so
        routine status rendering must never run those subprocesses on Qt's UI
        thread.
        """
        if self.tunnel_mode == "tailscale":
            public = (
                (self.tailscale_public_url() if probe else None)
                or self.public_url
                or self.storage.get_setting("public_url")
            )

            # Avoid showing an old trycloudflare URL
            # after migration to Tailscale.
            if not public:
                stored = (
                    self.storage.get_setting(
                        "public_url"
                    )
                    or ""
                )

                if (
                    stored.startswith(
                        "https://"
                    )
                    and ".ts.net" in stored
                ):
                    public = stored

            public = (
                public
                or self.local_url
            )

        else:
            public = (
                self.public_url
                or self.storage.get_setting(
                    "public_url"
                )
                or self.local_url
            )

        return {
            "gateway": (
                self.gateway_running()
            ),
            "tunnel": self.tunnel_running() if probe else bool(public and public.startswith("https://")),
            "tunnel_mode": (
                self.tunnel_mode
            ),
            "local_url": (
                self.local_url
            ),
            "public_url": public,
            "mcp_url": (
                public.rstrip("/")
                + "/mcp"
            ),
        }

    # -------------------------------------------------------------------------
    # Gateway
    # -------------------------------------------------------------------------

    def gateway_argv(self) -> list[str]:
        """Return the correct gateway command for source and frozen builds."""
        if getattr(sys, "frozen", False):
            suffix = ".exe" if os.name == "nt" else ""
            sibling = Path(sys.executable).resolve().parent / f"devmesh-server{suffix}"
            if not sibling.exists():
                raise RuntimeError(
                    "Frozen DevMesh build is missing its gateway executable.\n\n"
                    f"Expected:\n{sibling}\n\n"
                    "Reinstall or rebuild DevMesh Studio."
                )
            return [
                str(sibling),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.local_port),
            ]

        return [
            sys.executable,
            "-m",
            "devmesh_studio.runtime.server",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.local_port),
        ]

    def start(
        self,
        use_tunnel: bool | None = None,
    ) -> None:
        with self._lock:
            if not self.storage.get_setting(
                "password_hash"
            ):
                raise RuntimeError(
                    "Set DevMesh login credentials "
                    "before starting the runtime."
                )

            if self.gateway_running():
                if (
                    use_tunnel is not False
                    and not self.tunnel_running()
                ):
                    self.start_tunnel()

                return

            probe = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM,
            )

            probe.settimeout(0.2)

            try:
                in_use = (
                    probe.connect_ex(
                        (
                            "127.0.0.1",
                            self.local_port,
                        )
                    )
                    == 0
                )

            finally:
                probe.close()

            if in_use:
                raise RuntimeError(
                    f"Local port {self.local_port} "
                    "is already in use.\n\n"
                    "Stop the old DevMesh runtime "
                    "or choose another port "
                    "in Connections."
                )

            log_path = (
                runtime_dir()
                / "gateway.log"
            )

            log = open(
                log_path,
                "ab",
                buffering=0,
            )

            gateway_argv = self.gateway_argv()
            self.gateway = subprocess.Popen(
                gateway_argv,
                stdout=log,
                stderr=subprocess.STDOUT,
                **process_group_kwargs(hidden=IS_WINDOWS),
            )
            self._remember_gateway_pid()

            if self.tunnel_mode == "tailscale":
                public = (
                    self.tailscale_public_url()
                )

                self.storage.set_setting(
                    "public_url",
                    public
                    or self.local_url,
                )

            else:
                self.storage.set_setting(
                    "public_url",
                    self.local_url,
                )

            self.emit(
                "Gateway started on "
                f"{self.local_url}"
            )

        if not self._wait_gateway():
            raise RuntimeError(
                "Gateway did not become ready.\n\n"
                f"See:\n{log_path}"
            )

        if use_tunnel is None:
            use_tunnel = (
                self.storage.get_setting(
                    "auto_tunnel",
                    "1",
                )
                == "1"
            )

        if use_tunnel:
            self.start_tunnel()

    def _wait_gateway(
        self,
        timeout: float = 12.0,
    ) -> bool:
        deadline = (
            time.time()
            + timeout
        )

        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    self.local_url
                    + "/healthz",
                    timeout=0.5,
                ) as response:
                    if response.status == 200:
                        return True

            except Exception:
                pass

            if (
                self.gateway
                and self.gateway.poll()
                is not None
            ):
                return False

            time.sleep(0.15)

        return False

    # -------------------------------------------------------------------------
    # Tunnel start
    # -------------------------------------------------------------------------

    def start_tunnel(self) -> None:
        with self._lock:
            if self.tunnel_running():
                if (
                    self.tunnel_mode
                    == "tailscale"
                ):
                    public = (
                        self.tailscale_public_url()
                    )

                    if public:
                        self.public_url = public

                        self.storage.set_setting(
                            "public_url",
                            public,
                        )

                        self.emit(
                            "Tailscale Funnel "
                            "already active: "
                            f"{public}/mcp"
                        )

                return

            if (
                self.tunnel_mode
                == "tailscale"
            ):
                self.start_tailscale_funnel()
                return

            # Cloudflare Quick Tunnel fallback
            cloudflared = (
                self.ensure_cloudflared()
            )

            empty_config = (
                runtime_dir()
                / "cloudflared-empty.yml"
            )

            empty_config.write_text(
                "",
                encoding="utf-8",
            )

            argv = [
                str(cloudflared),
                "tunnel",
                "--config",
                str(empty_config),
                "--no-autoupdate",
                "--url",
                self.local_url,
            ]

            self.tunnel = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=os.environ.copy(),
                **process_group_kwargs(hidden=IS_WINDOWS),
            )

            self._tunnel_thread = (
                threading.Thread(
                    target=self._read_tunnel,
                    daemon=True,
                )
            )

            self._tunnel_thread.start()

            self.emit(
                "Creating temporary "
                "Cloudflare Quick Tunnel…"
            )

    # -------------------------------------------------------------------------
    # Cloudflare output parser
    # -------------------------------------------------------------------------

    def _read_tunnel(self) -> None:
        assert (
            self.tunnel
            and self.tunnel.stdout
        )

        log_path = (
            runtime_dir()
            / "tunnel.log"
        )

        with log_path.open(
            "a",
            encoding="utf-8",
        ) as log:
            for line in self.tunnel.stdout:
                log.write(line)
                log.flush()

                match = (
                    TUNNEL_RE.search(line)
                )

                if match:
                    url = match.group(0)

                    self.public_url = url

                    self.storage.set_setting(
                        "public_url",
                        url,
                    )

                    self.emit(
                        "Temporary public MCP ready: "
                        f"{url}/mcp"
                    )

    # -------------------------------------------------------------------------
    # Stop / restart
    # -------------------------------------------------------------------------

    def stop(self) -> None:
        with self._lock:
            # Cloudflare Quick Tunnel is represented by
            # a subprocess owned by DevMesh.
            if (
                self.tunnel
                and self.tunnel.poll()
                is None
            ):
                try:
                    self.tunnel.terminate()
                    self.tunnel.wait(
                        timeout=3
                    )

                except Exception:
                    try:
                        self.tunnel.kill()
                    except Exception:
                        pass

                self.emit(
                    "Temporary tunnel stopped"
                )

            # Tailscale Funnel intentionally remains
            # configured in --bg mode. This is what
            # keeps the public hostname/configuration
            # persistent between DevMesh restarts.
            if (
                self.tunnel_mode
                == "tailscale"
                and self.tailscale_funnel_running()
            ):
                public = (
                    self.tailscale_public_url()
                )

                if public:
                    self.emit(
                        "Tailscale Funnel remains "
                        "configured in background: "
                        f"{public}"
                    )

            if (
                self.gateway
                and self.gateway.poll()
                is None
            ):
                try:
                    self.gateway.terminate()
                    self.gateway.wait(
                        timeout=3
                    )

                except Exception:
                    try:
                        self.gateway.kill()
                    except Exception:
                        pass

                self.emit(
                    "Gateway stopped"
                )

            self._forget_gateway_pid()
            self.tunnel = None
            self.gateway = None

            if (
                self.tunnel_mode
                == "tailscale"
            ):
                public = (
                    self.tailscale_public_url()
                )

                self.public_url = public

                self.storage.set_setting(
                    "public_url",
                    public
                    or self.local_url,
                )

            else:
                self.public_url = None

                self.storage.set_setting(
                    "public_url",
                    self.local_url,
                )

    def restart(self) -> None:
        self.stop()
        self.start()
