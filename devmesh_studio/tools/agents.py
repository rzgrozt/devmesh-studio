from __future__ import annotations

import asyncio
import shutil
from typing import Any

from .base import ToolContext


DELEGATES = {
    "codex": {
        "label": "Codex CLI",
        "propose": ["codex", "exec", "--ephemeral", "--sandbox", "read-only", "{task}"],
        "apply": ["codex", "exec", "--ephemeral", "--sandbox", "workspace-write", "{task}"],
    },
    "claude": {
        "label": "Claude Code",
        "propose": ["claude", "-p", "--permission-mode", "plan", "--output-format", "text", "{task}"],
    },
    "opencode": {
        "label": "OpenCode",
        "propose": ["opencode", "run", "{task}"],
    },
}


class AgentTools:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx

    def list(self, actor: str) -> dict[str, Any]:
        args: dict[str, Any] = {}
        with self.ctx.record(actor, None, "agent_list", "agents", args):
            return {
                "agents": [
                    {
                        "id": key,
                        "label": cfg["label"],
                        "available": bool(shutil.which(key)),
                        "executable": shutil.which(key),
                        "modes": [m for m in ("propose", "apply") if m in cfg],
                    }
                    for key, cfg in DELEGATES.items()
                ]
            }

    async def delegate(self, actor: str, repo_id: int, agent: str, task: str, mode: str = "propose", timeout: int = 900) -> dict[str, Any]:
        if agent not in DELEGATES:
            raise KeyError(f"unknown agent delegate: {agent}")
        cfg = DELEGATES[agent]
        if mode not in cfg:
            raise ValueError(f"{agent} does not expose mode {mode}")
        if not task.strip():
            raise ValueError("task is required")
        if len(task.encode()) > 100_000:
            raise ValueError("task exceeds 100 KB")
        args = {"agent": agent, "task_sha256": self.ctx.storage.sha(task), "mode": mode, "timeout": timeout}
        with self.ctx.record(actor, repo_id, "agent_delegate", agent, args):
            self.ctx.permissions.require(actor, repo_id, "agent", agent, args, suggested_pattern=agent)
            template = cfg[mode]
            argv = [p.replace("{task}", task) for p in template]
            exe = shutil.which(argv[0])
            if not exe:
                raise RuntimeError(f"agent executable not found: {argv[0]}")
            argv[0] = exe
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self.ctx.repos.root(repo_id),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=min(max(timeout, 10), 3600))
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise TimeoutError(f"agent timed out after {timeout}s")
            return {
                "agent": agent,
                "mode": mode,
                "returncode": proc.returncode,
                "stdout": stdout.decode(errors="replace")[-200_000:],
                "stderr": stderr.decode(errors="replace")[-100_000:],
            }
