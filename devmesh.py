#!/usr/bin/env python3
"""Source-development launcher for DevMesh Studio.

End-user installs should use ``install.sh`` on Linux or ``install.ps1`` on
Windows. This launcher intentionally keeps the old repository-local .venv flow
for development and quick source runs.
"""
from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def has_runtime() -> bool:
    if not PYTHON.exists():
        return False
    probe = subprocess.run(
        [str(PYTHON), "-c", "import PyQt6, fastapi, jwt, argon2, keyring, psutil; import devmesh_studio"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def install() -> None:
    print("[DevMesh] Preparing local application environment…")
    if not PYTHON.exists():
        venv.EnvBuilder(with_pip=True).create(VENV)
    subprocess.check_call([str(PYTHON), "-m", "pip", "install", "--upgrade", "pip"], cwd=ROOT)
    subprocess.check_call([str(PYTHON), "-m", "pip", "install", "-e", "."], cwd=ROOT)


def main() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit("DevMesh Studio requires Python 3.11 or newer.")
    if not has_runtime():
        install()
    os.execv(str(PYTHON), [str(PYTHON), "-m", "devmesh_studio", *sys.argv[1:]])


if __name__ == "__main__":
    main()
