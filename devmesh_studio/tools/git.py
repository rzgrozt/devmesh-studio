from __future__ import annotations

import shlex
from typing import Any

from .base import ToolContext


class GitTools:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx

    def _run(self, actor: str, repo_id: int, tool: str, argv: list[str], action: str, resource: str, timeout: int = 60) -> dict[str, Any]:
        args = {"argv": argv}
        with self.ctx.record(actor, repo_id, tool, resource, args):
            self.ctx.permissions.require(actor, repo_id, action, resource, args, suggested_pattern=resource)
            proc = self.ctx.repos.git(repo_id, argv, timeout=timeout)
            return {
                "returncode": proc.returncode,
                "stdout": proc.stdout[-120_000:],
                "stderr": proc.stderr[-40_000:],
            }

    def status(self, actor: str, repo_id: int) -> dict[str, Any]:
        result = self._run(actor, repo_id, "git_status", ["status", "--porcelain=v2", "--branch"], "git_read", "git status")
        result["repository"] = self.ctx.repos.info(repo_id)
        return result

    def diff(self, actor: str, repo_id: int, staged: bool = False, path: str | None = None) -> dict[str, Any]:
        argv = ["diff"]
        if staged:
            argv.append("--staged")
        if path:
            self.ctx.repos.resolve(repo_id, path)
            argv += ["--", path]
        return self._run(actor, repo_id, "git_diff", argv, "git_read", "git diff")

    def log(self, actor: str, repo_id: int, limit: int = 30) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        fmt = "%h%x09%an%x09%ad%x09%s"
        return self._run(actor, repo_id, "git_log", ["log", f"-{limit}", "--date=iso", f"--pretty=format:{fmt}"], "git_read", "git log")

    def show(self, actor: str, repo_id: int, ref: str = "HEAD", path: str | None = None) -> dict[str, Any]:
        # ref is passed as one argv item; no shell interpretation.
        argv = ["show", "--stat", "--patch", ref]
        if path:
            self.ctx.repos.resolve(repo_id, path)
            argv += ["--", path]
        return self._run(actor, repo_id, "git_show", argv, "git_read", f"git show {ref}")

    def branches(self, actor: str, repo_id: int) -> dict[str, Any]:
        return self._run(actor, repo_id, "git_branches", ["branch", "--all", "--no-color"], "git_read", "git branch")

    def checkout(self, actor: str, repo_id: int, branch: str, create: bool = False) -> dict[str, Any]:
        argv = ["checkout"] + (["-b"] if create else []) + [branch]
        action = "git_branch" if create else "git_checkout"
        return self._run(actor, repo_id, "git_checkout", argv, action, f"git checkout {branch}")

    def add(self, actor: str, repo_id: int, paths: list[str]) -> dict[str, Any]:
        if not paths:
            raise ValueError("paths required")
        for path in paths:
            self.ctx.repos.resolve(repo_id, path)
        return self._run(actor, repo_id, "git_add", ["add", "--", *paths], "git_stage", "git add")

    def commit(self, actor: str, repo_id: int, message: str) -> dict[str, Any]:
        if not message.strip():
            raise ValueError("commit message required")
        return self._run(actor, repo_id, "git_commit", ["commit", "-m", message], "git_commit", "git commit", timeout=120)

    def restore(self, actor: str, repo_id: int, paths: list[str], staged: bool = False) -> dict[str, Any]:
        if not paths:
            raise ValueError("paths required")
        for path in paths:
            self.ctx.repos.resolve(repo_id, path)
        argv = ["restore"] + (["--staged"] if staged else []) + ["--", *paths]
        action = "git_stage" if staged else "git_restore"
        return self._run(actor, repo_id, "git_restore", argv, action, "git restore")

    def push(self, actor: str, repo_id: int, remote: str = "origin", branch: str | None = None) -> dict[str, Any]:
        argv = ["push", remote]
        if branch:
            argv.append(branch)
        return self._run(actor, repo_id, "git_push", argv, "git_push", f"git push {remote}", timeout=180)
