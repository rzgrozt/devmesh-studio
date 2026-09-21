from __future__ import annotations

import os
import shutil
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


IS_WINDOWS = os.name == "nt"
_DLL_HANDLES: list[Any] = []


def configure_frozen_qt() -> None:
    """Make Qt's DLL/plugin lookup deterministic in frozen Windows builds."""
    if not (IS_WINDOWS and getattr(sys, "frozen", False)):
        return
    bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    roots = (bundle, Path(sys.executable).resolve().parent)
    for root in roots:
        for dll_dir in (root, root / "PyQt6" / "Qt6" / "bin", root / "_internal" / "PyQt6" / "Qt6" / "bin"):
            if dll_dir.is_dir() and hasattr(os, "add_dll_directory"):
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(str(dll_dir)))  # type: ignore[attr-defined]
                except OSError:
                    pass
        for plugins in (root / "PyQt6" / "Qt6" / "plugins", root / "_internal" / "PyQt6" / "Qt6" / "plugins"):
            if plugins.is_dir():
                os.environ.setdefault("QT_PLUGIN_PATH", str(plugins))
                return


def executable(name: str, *windows_locations: Path) -> Path | None:
    """Find a command without relying on Unix-only helpers such as ``which``."""
    found = shutil.which(name)
    if found:
        return Path(found)
    if IS_WINDOWS:
        for candidate in windows_locations:
            if candidate.is_file():
                return candidate
    return None


def command_shell() -> str:
    if IS_WINDOWS:
        return os.environ.get("COMSPEC") or "cmd.exe"
    # Keep DevMesh's established Bash semantics on Unix; SHELL may be fish or
    # another shell with incompatible quoting and startup behavior.
    return "/bin/bash"


def shell_argv(command: str | None = None) -> list[str]:
    """Return the native shell invocation for an interactive or one-shot command."""
    shell = command_shell()
    if not command:
        return [shell]
    if IS_WINDOWS:
        return [shell, "/d", "/s", "/c", command]
    return [shell, "-lc", command]


def split_command(value: str) -> list[str]:
    if not IS_WINDOWS:
        return shlex.split(value)
    try:
        import ctypes

        argc = ctypes.c_int()
        command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW  # type: ignore[attr-defined]
        command_line_to_argv.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
        argv = command_line_to_argv(value, ctypes.byref(argc))
        if not argv:
            raise ValueError("invalid Windows command line")
        try:
            return [argv[index] for index in range(argc.value)]
        finally:
            local_free = ctypes.windll.kernel32.LocalFree  # type: ignore[attr-defined]
            local_free.argtypes = [ctypes.c_void_p]
            local_free.restype = ctypes.c_void_p
            local_free(ctypes.cast(argv, ctypes.c_void_p))
    except (AttributeError, OSError):
        # Enables deterministic platform-unit tests on non-Windows hosts.
        return [part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'" else part for part in shlex.split(value, posix=False)]


def join_command(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if IS_WINDOWS else shlex.join(argv)


def process_group_kwargs(*, hidden: bool = False) -> dict[str, Any]:
    """Platform-safe flags for a child process owned by DevMesh."""
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if hidden:
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}
