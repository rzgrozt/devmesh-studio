from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from .models import PermissionDecision
from .storage import Storage


class PermissionEngine:
    def __init__(self, storage: Storage):
        self.storage = storage

    @staticmethod
    def _matches(pattern: str, resource: str) -> bool:
        # Match both complete resources and basename-like file patterns.
        return fnmatch.fnmatch(resource, pattern) or fnmatch.fnmatch(Path(resource).name, pattern)

    def decide(
        self,
        actor: str,
        repo_id: int | None,
        action: str,
        resource: str,
        args: dict[str, Any],
        *,
        suggested_pattern: str | None = None,
    ) -> PermissionDecision:
        # Actions initiated by clicking controls in the local desktop UI are
        # already explicit human approvals. Remote actors (ChatGPT/MCP clients)
        # always go through the configured permission rules below.
        if actor == "desktop":
            return PermissionDecision("allow", "explicit local desktop action")
        consumed = self.storage.consume_matching_approval(actor, repo_id, action, resource, args)
        if consumed == "allow":
            return PermissionDecision("allow", "approved once")
        if consumed == "deny":
            return PermissionDecision("deny", "request was denied")

        if repo_id is None:
            # Non-repository actions are conservative unless the caller gives a
            # specific rule elsewhere.
            approval = self.storage.create_approval(actor, None, action, resource, args, suggested_pattern)
            return PermissionDecision("ask", "approval required", approval)

        rules = self.storage.list_permissions(repo_id)
        matching = [r for r in rules if r["action"] in {"*", action} and self._matches(r["pattern"], resource)]
        if not matching:
            approval = self.storage.create_approval(actor, repo_id, action, resource, args, suggested_pattern)
            return PermissionDecision("ask", "no matching rule", approval)

        # Highest priority wins; for ties later DB rule wins, mirroring the
        # intuitive "specific/later override" permission behavior.
        chosen = sorted(matching, key=lambda r: (int(r["priority"]), int(r["id"])))[-1]
        effect = chosen["effect"]
        if effect == "ask":
            approval = self.storage.create_approval(actor, repo_id, action, resource, args, suggested_pattern)
            return PermissionDecision("ask", f"matched {chosen['pattern']}", approval)
        return PermissionDecision(effect, f"matched {chosen['pattern']}")

    def require(
        self,
        actor: str,
        repo_id: int | None,
        action: str,
        resource: str,
        args: dict[str, Any],
        *,
        suggested_pattern: str | None = None,
    ) -> None:
        decision = self.decide(actor, repo_id, action, resource, args, suggested_pattern=suggested_pattern)
        if decision.effect == "allow":
            return
        if decision.effect == "deny":
            raise PermissionError(f"denied: {action} {resource} ({decision.reason})")
        raise ApprovalRequired(decision.approval_id or 0, action, resource, decision.reason)


class ApprovalRequired(PermissionError):
    def __init__(self, approval_id: int, action: str, resource: str, reason: str):
        self.approval_id = approval_id
        self.action = action
        self.resource = resource
        self.reason = reason
        super().__init__(f"approval required (#{approval_id}) for {action}: {resource}")
