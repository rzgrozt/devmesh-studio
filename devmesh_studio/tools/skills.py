from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import ToolContext


GLOBAL_SKILL_DIRS = [
    Path.home() / ".codex" / "skills",
    Path.home() / ".claude" / "skills",
    Path.home() / ".config" / "opencode" / "skills",
    Path.home() / ".agents" / "skills",
]
REPO_SKILL_DIRS = [".codex/skills", ".claude/skills", ".opencode/skills", ".agents/skills"]


class SkillTools:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx

    @staticmethod
    def _parse(path: Path, source: str) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        name = path.parent.name
        description = ""
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                front = text[3:end]
                for line in front.splitlines():
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip('"\'')
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip('"\'')
        if not description:
            for line in text.splitlines():
                if line.startswith("#"):
                    description = line.lstrip("# ").strip()
                    break
        return {"name": name, "description": description, "path": str(path), "source": source}

    def _discover(self, repo_id: int | None = None) -> list[dict[str, Any]]:
        roots: list[tuple[Path, str]] = [(p, "global") for p in GLOBAL_SKILL_DIRS]
        if repo_id is not None:
            root = self.ctx.repos.root(repo_id)
            roots += [(root / rel, "repository") for rel in REPO_SKILL_DIRS]
        out = []
        seen = set()
        for root, source in roots:
            if not root.exists():
                continue
            for path in root.rglob("SKILL.md"):
                try:
                    item = self._parse(path, source)
                except OSError:
                    continue
                key = (item["name"], str(path))
                if key not in seen:
                    seen.add(key)
                    out.append(item)
        return sorted(out, key=lambda x: x["name"].casefold())

    def list(self, actor: str, repo_id: int | None = None) -> dict[str, Any]:
        args = {"repo_id": repo_id}
        with self.ctx.record(actor, repo_id, "skill_list", "skills", args):
            if repo_id is not None:
                self.ctx.permissions.require(actor, repo_id, "skill", "*", args, suggested_pattern="*")
            return {"skills": self._discover(repo_id)}

    def read(self, actor: str, repo_id: int | None, name: str) -> dict[str, Any]:
        args = {"repo_id": repo_id, "name": name}
        with self.ctx.record(actor, repo_id, "skill_read", name, args):
            if repo_id is not None:
                self.ctx.permissions.require(actor, repo_id, "skill", name, args, suggested_pattern=name)
            for item in self._discover(repo_id):
                if item["name"] == name:
                    text = Path(item["path"]).read_text(encoding="utf-8", errors="replace")
                    return {**item, "text": text[:200_000], "truncated": len(text) > 200_000}
            raise KeyError(f"skill not found: {name}")

    def search(self, actor: str, repo_id: int | None, query: str) -> dict[str, Any]:
        q = query.casefold()
        skills = self._discover(repo_id)
        return {"skills": [s for s in skills if q in s["name"].casefold() or q in s["description"].casefold()]}
