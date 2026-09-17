from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from .paths import data_dir

SERVICE = "devmesh-studio"


class SecretStore:
    """Small keyring wrapper with a chmod-600 fallback for headless Linux.

    Passwords are never stored; only the Argon2 verifier is persisted as a
    normal setting. The JWT signing secret goes through this store.
    """

    def __init__(self):
        self.fallback = data_dir() / "secrets.json"

    def _keyring(self):
        try:
            import keyring
            return keyring
        except Exception:
            return None

    def get(self, name: str) -> str | None:
        kr = self._keyring()
        if kr is not None:
            try:
                value = kr.get_password(SERVICE, name)
                if value:
                    return value
            except Exception:
                pass
        if self.fallback.exists():
            try:
                return json.loads(self.fallback.read_text(encoding="utf-8")).get(name)
            except Exception:
                return None
        return None

    def set(self, name: str, value: str) -> None:
        kr = self._keyring()
        if kr is not None:
            try:
                kr.set_password(SERVICE, name, value)
                return
            except Exception:
                pass
        data = {}
        if self.fallback.exists():
            try:
                data = json.loads(self.fallback.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data[name] = value
        old = os.umask(0o077)
        try:
            self.fallback.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.fallback.chmod(0o600)
        finally:
            os.umask(old)

    def get_or_create(self, name: str, nbytes: int = 48) -> str:
        value = self.get(name)
        if value:
            return value
        value = secrets.token_urlsafe(nbytes)
        self.set(name, value)
        return value

    def rotate(self, name: str, nbytes: int = 48) -> str:
        """Replace a stored secret and return the new value."""
        value = secrets.token_urlsafe(nbytes)
        self.set(name, value)
        return value
