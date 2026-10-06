"""Secrets vault. Values live in the OS keyring (Windows Credential Manager, macOS
Keychain, Secret Service on Linux); only the key names are kept in config/vault_index.yaml.
Secrets never go into logs or prompts: tools read them here and use them directly.

Key names are dotted, for example ``login.github.password`` or ``ssh.homeserver``.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Protocol

import yaml

SERVICE = "starlight"
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class VaultError(Exception):
    pass


class VaultBackend(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeyringBackend:
    def __init__(self, service: str = SERVICE) -> None:
        import keyring

        self._keyring = keyring
        self.service = service

    def get(self, key: str) -> str | None:
        value: str | None = self._keyring.get_password(self.service, key)
        return value

    def set(self, key: str, value: str) -> None:
        self._keyring.set_password(self.service, key, value)

    def delete(self, key: str) -> None:
        import keyring.errors

        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            self._keyring.delete_password(self.service, key)


class MemoryBackend:
    """In-memory backend for tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class Vault:
    def __init__(self, index_file: Path, backend: VaultBackend | None = None) -> None:
        self.index_file = index_file
        self._backend = backend

    @property
    def backend(self) -> VaultBackend:
        if self._backend is None:
            try:
                self._backend = KeyringBackend()
            except Exception as exc:  # pragma: no cover - depends on the OS
                raise VaultError(f"no OS keyring available: {exc}") from exc
        return self._backend

    def names(self) -> list[str]:
        if not self.index_file.is_file():
            return []
        data = yaml.safe_load(self.index_file.read_text(encoding="utf-8")) or {}
        return sorted(data.get("keys") or [])

    def _write_index(self, keys: list[str]) -> None:
        self.index_file.parent.mkdir(parents=True, exist_ok=True)
        self.index_file.write_text(
            "# Names of secrets in the OS keyring. Values are never stored here.\n"
            + yaml.safe_dump({"keys": sorted(set(keys))}),
            encoding="utf-8",
        )

    def get(self, key: str) -> str | None:
        return self.backend.get(key)

    def require(self, key: str) -> str:
        value = self.get(key)
        if value is None:
            raise VaultError(f"secret '{key}' is not set. Run: starlight vault set {key}")
        return value

    def set(self, key: str, value: str) -> None:
        if not KEY_PATTERN.match(key):
            raise VaultError("key names use lowercase letters, digits, '.', '_' and '-'")
        self.backend.set(key, value)
        self._write_index([*self.names(), key])

    def delete(self, key: str) -> None:
        self.backend.delete(key)
        self._write_index([k for k in self.names() if k != key])
