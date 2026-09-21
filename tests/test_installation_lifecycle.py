from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import bootstrap
from devmesh_studio.core.storage import Storage
from devmesh_studio.services.runtime_supervisor import RuntimeSupervisor
from devmesh_studio import __version__


class InstallationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def paths(self) -> dict[str, Path]:
        return {
            "app": self.root / "app",
            "bin": self.root / "bin",
            "launcher": self.root / "bin" / "devmesh",
            "desktop": self.root / "applications" / "devmesh-studio.desktop",
            "start_menu": self.root / "start-menu.lnk",
            "config": self.root / "config",
            "data": self.root / "data",
            "cache": self.root / "cache",
        }

    def test_package_and_runtime_versions_stay_synchronized(self):
        with (Path(__file__).parents[1] / "pyproject.toml").open("rb") as handle:
            project_version = tomllib.load(handle)["project"]["version"]
        self.assertEqual(project_version, __version__)

    def test_linux_launcher_routes_lifecycle_commands(self):
        paths = self.paths()
        app = paths["app"]
        py = app / ".venv" / "bin" / "python"
        py.parent.mkdir(parents=True)
        py.touch()

        bootstrap.write_linux_launcher(paths, app, py)
        text = paths["launcher"].read_text(encoding="utf-8")
        self.assertIn("upgrade|uninstall|config|paths|doctor|build-windows", text)
        self.assertIn('exec "$PY" -m devmesh_studio', text)
        self.assertTrue(os.access(paths["launcher"], os.X_OK))

    def test_metadata_creates_safe_config_manifest(self):
        paths = self.paths()
        paths["app"].mkdir(parents=True)
        bootstrap.write_metadata(paths["app"], source=self.root, mode="source", paths=paths)

        manifest = paths["config"] / "install.json"
        self.assertTrue(manifest.exists())
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["install_mode"], "source")
        self.assertEqual(Path(payload["data_dir"]), paths["data"])

    def test_source_copy_excludes_git_metadata(self):
        source = self.root / "source"
        target = self.root / "target"
        (source / ".git" / "objects").mkdir(parents=True)
        (source / ".git" / "objects" / "private").write_text("git", encoding="utf-8")
        (source / "devmesh_studio").mkdir()
        (source / "devmesh_studio" / "module.py").write_text("ok = True", encoding="utf-8")
        bootstrap.copy_source(source, target)
        self.assertFalse((target / ".git").exists())
        self.assertTrue((target / "devmesh_studio" / "module.py").exists())

    def test_path_overrides_are_shared_by_installer(self):
        env = {
            "DEVMESH_INSTALL_DIR": str(self.root / "custom-app"),
            "DEVMESH_CONFIG_DIR": str(self.root / "custom-config"),
            "DEVMESH_DATA_DIR": str(self.root / "custom-data"),
            "DEVMESH_CACHE_DIR": str(self.root / "custom-cache"),
        }
        with patch.dict(os.environ, env, clear=False):
            paths = bootstrap.platform_paths()
        self.assertEqual(paths["app"], self.root / "custom-app")
        self.assertEqual(paths["config"], self.root / "custom-config")
        self.assertEqual(paths["data"], self.root / "custom-data")
        self.assertEqual(paths["cache"], self.root / "custom-cache")

    def test_uninstall_preserves_user_state_without_purge(self):
        paths = self.paths()
        for key in ("app", "config", "data", "cache"):
            paths[key].mkdir(parents=True)
            (paths[key] / "keep.txt").write_text(key, encoding="utf-8")
        paths["launcher"].parent.mkdir(parents=True, exist_ok=True)
        paths["launcher"].write_text("launcher", encoding="utf-8")
        paths["desktop"].parent.mkdir(parents=True, exist_ok=True)
        paths["desktop"].write_text("desktop", encoding="utf-8")
        if os.name == "nt":
            paths["start_menu"].parent.mkdir(parents=True, exist_ok=True)
            paths["start_menu"].write_text("start menu", encoding="utf-8")

        with patch.object(bootstrap, "platform_paths", return_value=paths):
            bootstrap.cmd_uninstall(argparse.Namespace(purge=False))

        self.assertFalse(paths["app"].exists())
        if os.name == "nt":
            self.assertFalse(paths["start_menu"].exists())
        else:
            self.assertFalse(paths["launcher"].exists())
        self.assertFalse(paths["desktop"].exists())
        self.assertTrue(paths["config"].exists())
        self.assertTrue(paths["data"].exists())
        self.assertTrue(paths["cache"].exists())

    def test_uninstall_purge_removes_all_user_state(self):
        paths = self.paths()
        for key in ("app", "config", "data", "cache"):
            paths[key].mkdir(parents=True)
        paths["launcher"].parent.mkdir(parents=True, exist_ok=True)
        paths["launcher"].touch()
        paths["desktop"].parent.mkdir(parents=True, exist_ok=True)
        paths["desktop"].touch()

        with patch.object(bootstrap, "platform_paths", return_value=paths), patch.object(bootstrap, "purge_keyring_secrets") as purge_secrets:
            bootstrap.cmd_uninstall(argparse.Namespace(purge=True))

        purge_secrets.assert_called_once_with()
        for key in ("app", "config", "data", "cache"):
            self.assertFalse(paths[key].exists())

    def test_windows_build_plan_contains_gui_and_gateway_modes(self):
        dist = self.root / "dist"
        work = self.root / "work"
        spec = self.root / "spec"
        gui = bootstrap.pyinstaller_command(
            self.root / "devmesh_gui.py",
            name="DevMesh Studio",
            console=False,
            dist=dist,
            work=work,
            spec=spec,
        )
        server = bootstrap.pyinstaller_command(
            self.root / "devmesh_server.py",
            name="devmesh-server",
            console=True,
            dist=dist,
            work=work,
            spec=spec,
        )
        self.assertIn("--onefile", gui)
        self.assertIn("--windowed", gui)
        self.assertIn("--console", server)
        self.assertIn("keyring", gui)
        self.assertIn("mcp", server)

    def test_frozen_runtime_uses_sibling_gateway_executable(self):
        data = self.root / "runtime-data"
        old_data = os.environ.get("DEVMESH_DATA_DIR")
        os.environ["DEVMESH_DATA_DIR"] = str(data)
        try:
            storage = Storage(data / "devmesh.db")
            supervisor = RuntimeSupervisor(storage)
            gui = self.root / "DevMesh Studio"
            gateway = self.root / ("devmesh-server.exe" if os.name == "nt" else "devmesh-server")
            gui.touch(); gateway.touch()
            with patch.object(sys, "executable", str(gui)), patch.object(sys, "frozen", True, create=True):
                argv = supervisor.gateway_argv()
            self.assertEqual(Path(argv[0]).resolve(), gateway.resolve())
            self.assertEqual(argv[-2:], ["--port", "8000"])
        finally:
            if old_data is None:
                os.environ.pop("DEVMESH_DATA_DIR", None)
            else:
                os.environ["DEVMESH_DATA_DIR"] = old_data


if __name__ == "__main__":
    unittest.main()
