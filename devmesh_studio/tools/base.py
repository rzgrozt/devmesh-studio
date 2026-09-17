from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from typing import Any, Iterator

from devmesh_studio.core.permissions import PermissionEngine
from devmesh_studio.core.repository import RepositoryManager
from devmesh_studio.core.storage import Storage


class ToolContext:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.repos = RepositoryManager(storage)
        self.permissions = PermissionEngine(storage)
        self._last_call_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
            f"devmesh_last_call_{id(self)}", default=None
        )

    def clear_last_call(self) -> None:
        self._last_call_id.set(None)

    def last_call_id(self) -> int | None:
        return self._last_call_id.get()

    @contextmanager
    def record(self, actor: str, repo_id: int | None, tool: str, resource: str, args: dict[str, Any]) -> Iterator[int]:
        started = time.monotonic()
        call_id = self.storage.begin_call(actor, repo_id, tool, resource, args)
        self._last_call_id.set(call_id)
        try:
            yield call_id
        except Exception as exc:
            duration = int((time.monotonic() - started) * 1000)
            self.storage.end_call(call_id, "error", f"{type(exc).__name__}: {exc}", duration)
            raise
        else:
            duration = int((time.monotonic() - started) * 1000)
            self.storage.end_call(call_id, "ok", "completed", duration)
