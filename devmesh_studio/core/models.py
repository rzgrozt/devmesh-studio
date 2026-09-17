from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

PermissionEffect = Literal["allow", "ask", "deny"]

@dataclass(slots=True)
class Repository:
    id: int
    name: str
    path: str
    enabled: bool = True
    created_at: int = 0

@dataclass(slots=True)
class PermissionDecision:
    effect: PermissionEffect
    reason: str
    approval_id: int | None = None
