#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

from bootstrap import APP_NAME, pyinstaller_command


ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = ROOT / "dist" / "release"


def version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def architecture() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)


def run(argv: list[str]) -> None:
    print("[release]", " ".join(argv), flush=True)
    subprocess.run(argv, cwd=ROOT, check=True)


def clean(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def build_binaries(target: str) -> tuple[Path, Path]:
    raw = ROOT / "dist" / f"{target}-raw"
    work = ROOT / "build" / f"pyinstaller-{target}"
    specs = ROOT / "build" / f"specs-{target}"
    clean(raw)
    work.mkdir(parents=True, exist_ok=True)
    specs.mkdir(parents=True, exist_ok=True)
    gui = pyinstaller_command(
        ROOT / "devmesh_gui.py",
        name=APP_NAME,
        console=False,
        dist=raw,
        work=work / "gui",
        spec=specs,
    )
    server = pyinstaller_command(
        ROOT / "devmesh_server.py",
        name="devmesh-server",
        console=True,
        dist=raw,
        work=work / "server",
        spec=specs,
    )
    run(gui)
    run(server)
    suffix = ".exe" if os.name == "nt" else ""
    gui_path = raw / f"{APP_NAME}{suffix}"
    server_path = raw / f"devmesh-server{suffix}"
    if target == "macos" and not gui_path.is_file():
        gui_path = raw / f"{APP_NAME}.app" / "Contents" / "MacOS" / APP_NAME
    if not gui_path.is_file() or not server_path.is_file():
        raise SystemExit("PyInstaller did not produce both DevMesh executables")
    return gui_path, server_path


def package_linux() -> None:
    if platform.system() != "Linux":
        raise SystemExit("Linux packages must be built on Linux")
    release = version()
    arch = architecture()
    gui, server = build_binaries("linux")
    clean(RELEASE_DIR)

    portable = RELEASE_DIR / f"devmesh-studio-{release}-linux-{arch}"
    portable.mkdir()
    shutil.copy2(gui, portable / APP_NAME)
    shutil.copy2(server, portable / "devmesh-server")
    shutil.copy2(ROOT / "LICENSE", portable / "LICENSE")
    shutil.copy2(ROOT / "README.md", portable / "README.md")
    launcher = portable / "devmesh"
    launcher.write_text('#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$HERE/DevMesh Studio" "$@"\n', encoding="utf-8")
    for executable in (portable / APP_NAME, portable / "devmesh-server", launcher):
        executable.chmod(0o755)
    archive = RELEASE_DIR / f"DevMesh-Studio-{release}-Linux-{arch}.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(portable, arcname=portable.name)

    fhs = RELEASE_DIR / "linux-fhs"
    app_dir = fhs / "opt" / "devmesh-studio"
    (fhs / "usr" / "bin").mkdir(parents=True)
    (fhs / "usr" / "share" / "applications").mkdir(parents=True)
    (fhs / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps").mkdir(parents=True)
    app_dir.mkdir(parents=True)
    shutil.copy2(gui, app_dir / APP_NAME)
    shutil.copy2(server, app_dir / "devmesh-server")
    shutil.copy2(ROOT / "packaging" / "linux" / "devmesh", fhs / "usr" / "bin" / "devmesh")
    shutil.copy2(ROOT / "packaging" / "linux" / "devmesh-studio.desktop", fhs / "usr" / "share" / "applications" / "devmesh-studio.desktop")
    shutil.copy2(ROOT / "assets" / "devmesh.svg", fhs / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "devmesh-studio.svg")
    for executable in (app_dir / APP_NAME, app_dir / "devmesh-server", fhs / "usr" / "bin" / "devmesh"):
        executable.chmod(0o755)

    appimage = RELEASE_DIR / "DevMesh.AppDir"
    shutil.copytree(fhs, appimage)
    shutil.copy2(ROOT / "packaging" / "linux" / "AppRun", appimage / "AppRun")
    shutil.copy2(ROOT / "packaging" / "linux" / "devmesh-studio.desktop", appimage / "devmesh-studio.desktop")
    shutil.copy2(ROOT / "assets" / "devmesh.svg", appimage / "devmesh-studio.svg")
    (appimage / "AppRun").chmod(0o755)


def package_macos() -> None:
    if platform.system() != "Darwin":
        raise SystemExit("macOS packages must be built on macOS")
    release = version()
    arch = architecture()
    gui, server = build_binaries("macos")
    clean(RELEASE_DIR)
    app = RELEASE_DIR / "DevMesh Studio.app"
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    shutil.copy2(gui, macos / APP_NAME)
    shutil.copy2(server, macos / "devmesh-server")
    shutil.copy2(ROOT / "assets" / "devmesh.svg", resources / "devmesh.svg")
    for executable in (macos / APP_NAME, macos / "devmesh-server"):
        executable.chmod(0o755)
    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "studio.devmesh.desktop",
        "CFBundleExecutable": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": release,
        "CFBundleVersion": release,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    }
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)
    archive = RELEASE_DIR / f"DevMesh-Studio-{release}-macOS-{arch}.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(app, arcname=app.name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build native DevMesh release layouts")
    parser.add_argument("target", choices=("linux", "macos"))
    args = parser.parse_args()
    package_linux() if args.target == "linux" else package_macos()


if __name__ == "__main__":
    main()
