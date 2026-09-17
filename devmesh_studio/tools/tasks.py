from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .base import ToolContext


class TaskTools:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx

    def detect(self, repo_id: int) -> dict[str, list[str]]:
        root = self.ctx.repos.root(repo_id)
        tasks: dict[str, list[str]] = {}
        if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or (root / "tests").exists():
            tasks["test"] = ["pytest", "-q"]
        if (root / "ruff.toml").exists() or (root / ".ruff.toml").exists() or (root / "pyproject.toml").exists():
            tasks.setdefault("lint", ["ruff", "check", "."])
        if (root / "mypy.ini").exists() or (root / ".mypy.ini").exists():
            tasks["typecheck"] = ["mypy", "."]
        package = root / "package.json"
        if package.exists():
            try:
                scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
            except Exception:
                scripts = {}
            for key in ("test", "build", "lint", "typecheck", "check"):
                if key in scripts:
                    tasks[key] = ["npm", "run", key]
        if (root / "Cargo.toml").exists():
            tasks.setdefault("test", ["cargo", "test"])
            tasks.setdefault("build", ["cargo", "build"])
            tasks.setdefault("lint", ["cargo", "clippy"])
        if (root / "go.mod").exists():
            tasks.setdefault("test", ["go", "test", "./..."])
            tasks.setdefault("build", ["go", "build", "./..."])
        if (root / "Makefile").exists():
            tasks.setdefault("build", ["make"])
        return tasks

    def list(self, actor: str, repo_id: int) -> dict[str, Any]:
        args: dict[str, Any] = {}
        with self.ctx.record(actor, repo_id, "task_list", "tasks", args):
            return {"tasks": self.detect(repo_id)}

    def run(self, actor: str, repo_id: int, name: str, timeout: int = 600) -> dict[str, Any]:
        tasks = self.detect(repo_id)
        if name not in tasks:
            raise KeyError(f"unknown detected task: {name}")
        argv = tasks[name]
        args = {"name": name, "argv": argv, "timeout": timeout}
        with self.ctx.record(actor, repo_id, "task_run", name, args):
            self.ctx.permissions.require(actor, repo_id, "task", name, args, suggested_pattern=name)
            proc = subprocess.run(argv, cwd=self.ctx.repos.root(repo_id), capture_output=True, text=True, timeout=min(max(timeout, 1), 1800), shell=False)
            return {
                "task": name,
                "argv": argv,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-160_000:],
                "stderr": (proc.stderr or "")[-80_000:],
            }
