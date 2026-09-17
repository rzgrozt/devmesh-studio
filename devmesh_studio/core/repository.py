from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .storage import Storage


SECRET_BASENAMES = {".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519"}


class RepositoryManager:
    def __init__(self, storage: Storage):
        self.storage = storage

    def repo(self, repo_id: int | str) -> dict[str, Any]:
        repo = self.storage.get_repository(repo_id)
        if not repo or not repo.get("enabled"):
            raise KeyError(f"unknown or disabled repository: {repo_id}")
        return repo

    def root(self, repo_id: int | str) -> Path:
        return Path(self.repo(repo_id)["path"]).resolve()

    def resolve(self, repo_id: int | str, relative: str | Path = ".", *, must_exist: bool = False) -> Path:
        root = self.root(repo_id)
        rel = Path(str(relative))
        if rel.is_absolute():
            candidate = rel.resolve(strict=False)
        else:
            candidate = (root / rel).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PermissionError(f"path escapes repository root: {relative}") from exc
        if must_exist and not candidate.exists():
            raise FileNotFoundError(candidate)
        return candidate

    def relative(self, repo_id: int | str, path: str | Path) -> str:
        return str(Path(path).resolve().relative_to(self.root(repo_id)))

    @staticmethod
    def is_secret_like(path: Path) -> bool:
        name = path.name.lower()
        return (
            name in SECRET_BASENAMES
            or name.startswith(".env.")
            or name.endswith(".pem")
            or name.endswith(".key")
            or "credentials" in name
            or "secret" in name
        )

    def git(self, repo_id: int | str, argv: list[str], timeout: int = 30, check: bool = False) -> subprocess.CompletedProcess[str]:
        root = self.root(repo_id)
        proc = subprocess.run(
            ["git", *argv],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if check and proc.returncode:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(argv)} failed")
        return proc

    def info(self, repo_id: int | str) -> dict[str, Any]:
        repo = self.repo(repo_id)
        root = Path(repo["path"])
        inside = self.git(repo_id, ["rev-parse", "--is-inside-work-tree"]).returncode == 0
        branch = None
        head = None
        dirty = 0
        if inside:
            branch_p = self.git(repo_id, ["branch", "--show-current"])
            branch = branch_p.stdout.strip() or "(detached)"
            head_p = self.git(repo_id, ["rev-parse", "--short", "HEAD"])
            head = head_p.stdout.strip() if head_p.returncode == 0 else None
            status_p = self.git(repo_id, ["status", "--porcelain"])
            dirty = len([line for line in status_p.stdout.splitlines() if line.strip()]) if status_p.returncode == 0 else 0

        skip = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".mypy_cache", ".pytest_cache"}
        file_count = 0
        extension_counts: dict[str, int] = {}
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in files:
                file_count += 1
                suffix = Path(name).suffix.lower() or "(no extension)"
                extension_counts[suffix] = extension_counts.get(suffix, 0) + 1
        top_extensions = sorted(extension_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
        return {
            **repo,
            "git": inside,
            "branch": branch,
            "head": head,
            "dirty_files": dirty,
            "file_count": file_count,
            "top_extensions": [{"extension": ext, "count": count} for ext, count in top_extensions],
        }
