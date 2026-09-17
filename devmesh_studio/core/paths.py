from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIRNAME = "DevMesh Studio"
UNIX_DIRNAME = "devmesh-studio"


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    override = os.getenv("DEVMESH_CONFIG_DIR")
    if override:
        return _ensure(Path(override).expanduser().resolve())

    if os.name == "nt":
        base = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return _ensure(base / APP_DIRNAME)

    if sys.platform == "darwin":
        return _ensure(Path.home() / "Library" / "Application Support" / APP_DIRNAME / "Config")

    base = Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return _ensure(base / UNIX_DIRNAME)


def data_dir() -> Path:
    override = os.getenv("DEVMESH_DATA_DIR")
    if override:
        return _ensure(Path(override).expanduser().resolve())

    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return _ensure(base / APP_DIRNAME / "Data")

    if sys.platform == "darwin":
        return _ensure(Path.home() / "Library" / "Application Support" / APP_DIRNAME / "Data")

    base = Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return _ensure(base / UNIX_DIRNAME)


def cache_dir() -> Path:
    override = os.getenv("DEVMESH_CACHE_DIR")
    if override:
        return _ensure(Path(override).expanduser().resolve())

    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return _ensure(base / APP_DIRNAME / "Cache")

    if sys.platform == "darwin":
        return _ensure(Path.home() / "Library" / "Caches" / APP_DIRNAME)

    base = Path(os.getenv("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return _ensure(base / UNIX_DIRNAME)


def db_path() -> Path:
    return data_dir() / "devmesh.db"


def runtime_dir() -> Path:
    return _ensure(data_dir() / "runtime")


def bin_dir() -> Path:
    return _ensure(data_dir() / "bin")
