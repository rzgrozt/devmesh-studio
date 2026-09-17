#!/usr/bin/env python3
"""Compatibility helper for older DevMesh installations.

The new installer creates the desktop entry automatically. This helper now only
recreates that entry for an already-installed Linux copy.
"""
from __future__ import annotations

import sys

from scripts.bootstrap import platform_paths, write_linux_desktop


def main() -> None:
    if not sys.platform.startswith("linux"):
        raise SystemExit("Desktop-entry recreation is only needed on Linux. Use install.ps1 on Windows.")

    paths = platform_paths()
    app_dir = paths["app"]
    if not app_dir.exists() or not paths["launcher"].exists():
        raise SystemExit("DevMesh is not installed yet. Run ./install.sh first.")

    write_linux_desktop(paths, app_dir)


if __name__ == "__main__":
    main()
