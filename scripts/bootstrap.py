#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

APP_NAME = "DevMesh Studio"
APP_SLUG = "devmesh-studio"
DEFAULT_REPO_URL = os.getenv(
    "DEVMESH_REPO_URL",
    "https://github.com/rzgrozt/devmesh-studio.git",
)
ROOT = Path(__file__).resolve().parents[1]


def _run(argv: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print("[DevMesh]", " ".join(str(x) for x in argv))
    return subprocess.run(argv, cwd=cwd, check=check)


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def platform_paths() -> dict[str, Path]:
    home = Path.home()
    install_override = os.getenv("DEVMESH_INSTALL_DIR")
    config_override = os.getenv("DEVMESH_CONFIG_DIR")
    data_override = os.getenv("DEVMESH_DATA_DIR")
    cache_override = os.getenv("DEVMESH_CACHE_DIR")

    if os.name == "nt":
        local = Path(os.getenv("LOCALAPPDATA") or (home / "AppData" / "Local"))
        roaming = Path(os.getenv("APPDATA") or (home / "AppData" / "Roaming"))
        app_dir = Path(install_override).expanduser() if install_override else local / "Programs" / APP_NAME
        config = Path(config_override).expanduser() if config_override else roaming / APP_NAME
        data = Path(data_override).expanduser() if data_override else local / APP_NAME / "Data"
        cache = Path(cache_override).expanduser() if cache_override else local / APP_NAME / "Cache"
        return {
            "app": app_dir,
            "bin": app_dir,
            "launcher": app_dir / "devmesh.cmd",
            "desktop": home / "Desktop" / f"{APP_NAME}.lnk",
            "start_menu": roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk",
            "config": config,
            "data": data,
            "cache": cache,
        }

    if sys.platform == "darwin":
        app_dir = Path(install_override).expanduser() if install_override else home / ".local" / "opt" / APP_SLUG
        config = Path(config_override).expanduser() if config_override else home / "Library" / "Application Support" / APP_NAME / "Config"
        data = Path(data_override).expanduser() if data_override else home / "Library" / "Application Support" / APP_NAME / "Data"
        cache = Path(cache_override).expanduser() if cache_override else home / "Library" / "Caches" / APP_NAME
        return {
            "app": app_dir,
            "bin": home / ".local" / "bin",
            "launcher": home / ".local" / "bin" / "devmesh",
            "desktop": home / "Applications" / f"{APP_NAME}.app",
            "start_menu": Path(""),
            "config": config,
            "data": data,
            "cache": cache,
        }

    xdg_data = Path(os.getenv("XDG_DATA_HOME") or (home / ".local" / "share"))
    xdg_config = Path(os.getenv("XDG_CONFIG_HOME") or (home / ".config"))
    xdg_cache = Path(os.getenv("XDG_CACHE_HOME") or (home / ".cache"))
    app_dir = Path(install_override).expanduser() if install_override else home / ".local" / "opt" / APP_SLUG
    config = Path(config_override).expanduser() if config_override else xdg_config / APP_SLUG
    data = Path(data_override).expanduser() if data_override else xdg_data / APP_SLUG
    cache = Path(cache_override).expanduser() if cache_override else xdg_cache / APP_SLUG
    return {
        "app": app_dir,
        "bin": home / ".local" / "bin",
        "launcher": home / ".local" / "bin" / "devmesh",
        "desktop": xdg_data / "applications" / f"{APP_SLUG}.desktop",
        "start_menu": Path(""),
        "config": config,
        "data": data,
        "cache": cache,
    }


def source_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {
        ".git",
        ".venv",
        ".testvenv",
        ".pytest_cache",
        "__pycache__",
        "build",
        "dist",
        ".mypy_cache",
        ".ruff_cache",
    }
    return {name for name in names if name in ignored or name.endswith(".egg-info")}


def copy_source(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.expanduser().resolve()
    if _same_path(source, target):
        return

    print(f"[DevMesh] Installing source into {target}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=source_ignore)


def verify_mcp_ui_source(root: Path) -> None:
    """Refuse an install that would silently keep an old gateway/UI package."""
    required = (
        root / "devmesh_studio" / "runtime" / "auth.py",
        root / "devmesh_studio" / "runtime" / "mcp_http.py",
        root / "devmesh_studio" / "runtime" / "tool_registry.py",
        root / "devmesh_studio" / "runtime" / "tool_widget.py",
    )
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(
            "Incomplete DevMesh source tree; refusing to retain stale runtime files. Missing: "
            + ", ".join(missing)
        )


def venv_python(app_dir: Path) -> Path:
    return app_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_source_runtime(app_dir: Path) -> Path:
    py = venv_python(app_dir)
    if not py.exists():
        print("[DevMesh] Creating application virtual environment")
        venv.EnvBuilder(with_pip=True).create(app_dir / ".venv")
    _run([str(py), "-m", "pip", "install", "--upgrade", "pip"], cwd=app_dir)
    _run([str(py), "-m", "pip", "install", "-e", "."], cwd=app_dir)
    return py


def metadata_path(app_dir: Path) -> Path:
    return app_dir / ".devmesh-install.json"


def write_metadata(app_dir: Path, *, source: Path, mode: str, paths: dict[str, Path] | None = None) -> None:
    payload = {
        "installed_at": int(time.time()),
        "platform": platform.system(),
        "source": str(source.resolve()),
        "repo_url": DEFAULT_REPO_URL,
        "mode": mode,
    }
    metadata_path(app_dir).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if paths is not None:
        config = paths["config"]
        config.mkdir(parents=True, exist_ok=True)
        public = {
            "app_dir": str(paths["app"]),
            "launcher": str(paths["launcher"]),
            "data_dir": str(paths["data"]),
            "cache_dir": str(paths["cache"]),
            "install_mode": mode,
            "source": str(source.resolve()),
        }
        (config / "install.json").write_text(json.dumps(public, indent=2), encoding="utf-8")


def read_metadata(app_dir: Path) -> dict:
    try:
        return json.loads(metadata_path(app_dir).read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_linux_launcher(paths: dict[str, Path], app_dir: Path, py: Path) -> None:
    launcher = paths["launcher"]
    launcher.parent.mkdir(parents=True, exist_ok=True)
    script = f'''#!/bin/sh
set -eu
APP_DIR={json.dumps(str(app_dir))}
PY={json.dumps(str(py))}
case "${{1:-}}" in
  upgrade|uninstall|config|paths|doctor|build-windows)
    exec "$PY" "$APP_DIR/scripts/bootstrap.py" "$@"
    ;;
  *)
    exec "$PY" -m devmesh_studio "$@"
    ;;
esac
'''
    launcher.write_text(script, encoding="utf-8")
    launcher.chmod(0o755)
    print(f"[DevMesh] Launcher installed: {launcher}")


def write_linux_desktop(paths: dict[str, Path], app_dir: Path) -> None:
    desktop = paths["desktop"]
    desktop.parent.mkdir(parents=True, exist_ok=True)
    icon = app_dir / "assets" / "devmesh.svg"
    desktop.write_text(
        "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                f"Name={APP_NAME}",
                "Comment=ChatGPT local developer control plane",
                f"Exec={paths['launcher']}",
                f"Icon={icon}",
                "Terminal=false",
                "Categories=Development;Utility;",
                "StartupNotify=true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    desktop.chmod(0o755)
    print(f"[DevMesh] Desktop entry installed: {desktop}")


def powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def create_windows_shortcut(target: Path, shortcut: Path) -> None:
    ps = powershell()
    if not ps:
        print(f"[DevMesh] PowerShell unavailable; shortcut skipped: {shortcut}")
        return
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    target_s = str(target).replace("'", "''")
    shortcut_s = str(shortcut).replace("'", "''")
    work_s = str(target.parent).replace("'", "''")
    command = (
        "$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut('{shortcut_s}');"
        f"$s.TargetPath='{target_s}';"
        f"$s.WorkingDirectory='{work_s}';"
        "$s.Description='DevMesh Studio';"
        "$s.Save()"
    )
    _run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command])


def pyinstaller_command(entry: Path, *, name: str, console: bool, dist: Path, work: Path, spec: Path) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        name,
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(spec),
        "--collect-all",
        "keyring",
        "--collect-all",
        "mcp",
    ]
    argv.append("--console" if console else "--windowed")
    argv.append(str(entry))
    return argv


def build_windows(source: Path, *, dry_run: bool = False) -> tuple[Path, Path]:
    source = source.resolve()
    if os.name != "nt" and not dry_run:
        raise SystemExit("Windows executables must be built on Windows. Use --dry-run to inspect the build plan.")

    dist = source / "dist" / "windows"
    work_root = source / "build" / "pyinstaller"
    spec = source / "build" / "specs"
    dist.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    spec.mkdir(parents=True, exist_ok=True)

    gui_entry = source / "devmesh_gui.py"
    server_entry = source / "devmesh_server.py"
    gui_cmd = pyinstaller_command(
        gui_entry,
        name=APP_NAME,
        console=False,
        dist=dist,
        work=work_root / "gui",
        spec=spec,
    )
    server_cmd = pyinstaller_command(
        server_entry,
        name="devmesh-server",
        console=True,
        dist=dist,
        work=work_root / "server",
        spec=spec,
    )

    if dry_run:
        print("[DevMesh] Windows build plan")
        print("GUI:", " ".join(gui_cmd))
        print("SERVER:", " ".join(server_cmd))
        return dist / f"{APP_NAME}.exe", dist / "devmesh-server.exe"

    _run([sys.executable, "-m", "pip", "install", "-e", ".[build]"], cwd=source)
    _run(gui_cmd, cwd=source)
    _run(server_cmd, cwd=source)

    gui = dist / f"{APP_NAME}.exe"
    server = dist / "devmesh-server.exe"
    if not gui.exists() or not server.exists():
        raise RuntimeError("PyInstaller completed without producing both required executables.")
    print(f"[DevMesh] Windows GUI: {gui}")
    print(f"[DevMesh] Windows gateway: {server}")
    return gui, server


def install_windows(source: Path, paths: dict[str, Path], *, desktop: bool = True) -> None:
    gui, server = build_windows(source)
    app_dir = paths["app"]
    app_dir.mkdir(parents=True, exist_ok=True)
    target_gui = app_dir / gui.name
    target_server = app_dir / server.name
    shutil.copy2(gui, target_gui)
    shutil.copy2(server, target_server)

    manager = app_dir / "scripts" / "bootstrap.py"
    manager.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "scripts" / "bootstrap.py", manager)
    (app_dir / "devmesh.cmd").write_text(
        f'@echo off\r\n"{sys.executable}" "{manager}" %*\r\n',
        encoding="utf-8",
    )
    write_metadata(app_dir, source=source, mode="windows-exe", paths=paths)
    create_windows_shortcut(target_gui, paths["start_menu"])
    if desktop:
        create_windows_shortcut(target_gui, paths["desktop"])
    print(f"[DevMesh] Installed {APP_NAME} to {app_dir}")


def install_source(source: Path, paths: dict[str, Path], *, desktop: bool = True) -> None:
    app_dir = paths["app"]
    verify_mcp_ui_source(source)
    copy_source(source, app_dir)
    verify_mcp_ui_source(app_dir)
    py = ensure_source_runtime(app_dir)
    write_metadata(app_dir, source=source, mode="source", paths=paths)
    if sys.platform.startswith("linux"):
        write_linux_launcher(paths, app_dir, py)
        if desktop:
            write_linux_desktop(paths, app_dir)
    else:
        print(f"[DevMesh] Source runtime prepared at {app_dir}")


def cmd_install(args: argparse.Namespace) -> None:
    source = Path(args.source or ROOT).expanduser().resolve()
    if not (source / "pyproject.toml").exists():
        raise SystemExit(f"Not a DevMesh source checkout: {source}")
    paths = platform_paths()
    if os.name == "nt":
        install_windows(source, paths, desktop=not args.no_desktop)
    else:
        install_source(source, paths, desktop=not args.no_desktop)
    print("[DevMesh] Installation complete")
    cmd_paths(argparse.Namespace())


def cmd_upgrade(args: argparse.Namespace) -> None:
    paths = platform_paths()
    app_dir = paths["app"]
    if not app_dir.exists():
        raise SystemExit("DevMesh is not installed. Run the installer first.")

    stop_installed_processes(paths)
    metadata = read_metadata(app_dir)
    if os.name == "nt":
        source = Path(args.source or metadata.get("source") or ROOT).expanduser().resolve()
        install_windows(source, paths, desktop=True)
        return

    source_value = args.source or metadata.get("source")
    if source_value:
        source = Path(source_value).expanduser()
        if source.exists() and not _same_path(source, app_dir):
            verify_mcp_ui_source(source)
            copy_source(source, app_dir)
            verify_mcp_ui_source(app_dir)
            ensure_source_runtime(app_dir)
            write_linux_launcher(paths, app_dir, venv_python(app_dir))
            write_linux_desktop(paths, app_dir)
            write_metadata(app_dir, source=source, mode="source", paths=paths)
            print("[DevMesh] Upgrade complete from local source")
            return

    if (app_dir / ".git").exists() and shutil.which("git"):
        _run(["git", "pull", "--ff-only"], cwd=app_dir)
        verify_mcp_ui_source(app_dir)
        ensure_source_runtime(app_dir)
        write_linux_launcher(paths, app_dir, venv_python(app_dir))
        write_linux_desktop(paths, app_dir)
        print("[DevMesh] Upgrade complete")
        return

    raise SystemExit(
        "No upgrade source is available. Re-run install.sh or use: devmesh upgrade --source /path/to/devmesh-studio"
    )


def _remove_path(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=False)
    except FileNotFoundError:
        pass


def stop_installed_processes(paths: dict[str, Path]) -> None:
    """Stop only DevMesh processes that belong to this user installation."""
    try:
        import psutil
    except Exception:
        return

    app_dir = paths["app"].expanduser().resolve()
    current_pid = os.getpid()
    matches = []
    for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
        try:
            if proc.pid == current_pid:
                continue
            exe_raw = proc.info.get("exe") or ""
            cmdline = [str(x) for x in (proc.info.get("cmdline") or [])]
            exe_path = Path(exe_raw).resolve() if exe_raw else None
            belongs = bool(exe_path and (exe_path == app_dir or app_dir in exe_path.parents))
            if not belongs:
                belongs = any(str(app_dir) in arg for arg in cmdline)
            if belongs:
                matches.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    for proc in matches:
        try:
            print(f"[DevMesh] Stopping installed process {proc.pid}")
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if matches:
        _, alive = psutil.wait_procs(matches, timeout=3)
        for proc in alive:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass


def purge_keyring_secrets() -> None:
    """Remove DevMesh secrets from the OS keyring when the backend is available."""
    try:
        import keyring
    except Exception:
        print("[DevMesh] Keyring module unavailable; file-based secrets will still be removed with the data directory.")
        return

    for name in ("jwt_secret",):
        try:
            keyring.delete_password("devmesh-studio", name)
            print(f"[DevMesh] Removed keyring secret: {name}")
        except Exception:
            # Missing credentials and unavailable desktop keyring backends are
            # both harmless during purge. Data-directory fallback is removed below.
            pass


def cmd_uninstall(args: argparse.Namespace) -> None:
    paths = platform_paths()
    app_dir = paths["app"]
    stop_installed_processes(paths)
    print(f"[DevMesh] Removing application files from {app_dir}")

    if os.name == "nt":
        _remove_path(paths["desktop"])
        _remove_path(paths["start_menu"])
        # A running Windows bootstrap cannot delete its own parent reliably.
        # Spawn a detached PowerShell cleanup after this process exits.
        ps = powershell()
        if app_dir.exists() and ps:
            target = str(app_dir).replace("'", "''")
            command = f"Start-Sleep -Milliseconds 700; Remove-Item -LiteralPath '{target}' -Recurse -Force -ErrorAction SilentlyContinue"
            subprocess.Popen(
                [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        elif app_dir.exists():
            print(f"[DevMesh] Remove this directory manually after exit: {app_dir}")
    else:
        _remove_path(paths["launcher"])
        _remove_path(paths["desktop"])
        _remove_path(app_dir)

    if args.purge:
        purge_keyring_secrets()
        for key in ("config", "data", "cache"):
            print(f"[DevMesh] Purging {key}: {paths[key]}")
            _remove_path(paths[key])
    else:
        print("[DevMesh] User data preserved. Use `devmesh uninstall --purge` to remove config/data/cache too.")
    print("[DevMesh] Uninstall complete")


def _open_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        _run(["open", str(path)], check=False)
    else:
        opener = shutil.which("xdg-open")
        if opener:
            _run([opener, str(path)], check=False)
        else:
            print(path)


def cmd_config(args: argparse.Namespace) -> None:
    paths = platform_paths()
    path = paths["config"]
    path.mkdir(parents=True, exist_ok=True)
    print(f"config: {path}")
    print(f"state : {paths['data']}")
    install_manifest = path / "install.json"
    if install_manifest.exists():
        print(f"install metadata: {install_manifest}")
    if getattr(args, "open", False):
        _open_directory(path)


def cmd_paths(args: argparse.Namespace) -> None:
    paths = platform_paths()
    print(f"platform : {platform.system()} {platform.machine()}")
    for key in ("app", "launcher", "desktop", "config", "data", "cache"):
        print(f"{key:8}: {paths[key]}")


def cmd_doctor(args: argparse.Namespace) -> None:
    paths = platform_paths()
    problems: list[str] = []
    print(f"DevMesh install doctor · {platform.system()} {platform.release()}")
    print(f"Python: {sys.version.split()[0]} · {sys.executable}")
    print(f"App: {'installed' if paths['app'].exists() else 'missing'} · {paths['app']}")
    print(f"Data: {paths['data']}")
    installed_widget = paths["app"] / "devmesh_studio" / "runtime" / "tool_widget.py"
    print(f"ChatGPT widget: {'installed' if installed_widget.is_file() else 'missing'} · {installed_widget}")
    if paths["app"].exists() and not installed_widget.is_file():
        problems.append("installed runtime is stale and has no ChatGPT MCP Apps widget")
    if not shutil.which("git"):
        problems.append("git is not available on PATH")
    if sys.platform.startswith("linux") and not shutil.which("xdg-open"):
        print("Note: xdg-open is unavailable; `devmesh config --open` will only print the path.")
    if problems:
        for item in problems:
            print(f"PROBLEM: {item}")
        raise SystemExit(1)
    print("Doctor: OK")


def cmd_build_windows(args: argparse.Namespace) -> None:
    source = Path(args.source or ROOT).expanduser().resolve()
    build_windows(source, dry_run=args.dry_run)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DevMesh Studio installer and lifecycle manager")
    sub = p.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install", help="Install DevMesh for the current user")
    install.add_argument("--source", help="DevMesh source checkout")
    install.add_argument("--no-desktop", action="store_true", help="Do not create a desktop shortcut")
    install.set_defaults(func=cmd_install)

    upgrade = sub.add_parser("upgrade", help="Upgrade the installed application while preserving user data")
    upgrade.add_argument("--source", help="Upgrade from this local source checkout")
    upgrade.set_defaults(func=cmd_upgrade)

    uninstall = sub.add_parser("uninstall", help="Remove DevMesh application files")
    uninstall.add_argument("--purge", action="store_true", help="Also remove config, database, cache and runtime data")
    uninstall.set_defaults(func=cmd_uninstall)

    config = sub.add_parser("config", help="Show the DevMesh config directory")
    config.add_argument("--open", action="store_true", help="Open the config directory in the file manager")
    config.set_defaults(func=cmd_config)

    paths = sub.add_parser("paths", help="Show all installation and state paths")
    paths.set_defaults(func=cmd_paths)

    doctor = sub.add_parser("doctor", help="Check the local installation prerequisites")
    doctor.set_defaults(func=cmd_doctor)

    build = sub.add_parser("build-windows", help="Build native Windows GUI and gateway executables")
    build.add_argument("--source", help="DevMesh source checkout")
    build.add_argument("--dry-run", action="store_true", help="Print the Windows PyInstaller commands without running them")
    build.set_defaults(func=cmd_build_windows)
    return p


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
