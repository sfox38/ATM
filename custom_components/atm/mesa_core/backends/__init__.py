"""Storage backends for ProfileStore (Module Proposal 4.3)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StorageBackend(ABC):
    """Abstract storage backend. Host servers may implement their own."""

    @abstractmethod
    def read(self, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def write(self, key: str, data: dict[str, Any]) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def list_keys(self, prefix: str | None = None) -> list[str]: ...


from custom_components.atm.mesa_core.backends.jsonfile import JsonFileBackend  # noqa: E402
from custom_components.atm.mesa_core.backends.memory import MemoryBackend  # noqa: E402
from custom_components.atm.mesa_core.backends.sqlite import SqliteBackend  # noqa: E402

__all__ = ["JsonFileBackend", "MemoryBackend", "SqliteBackend", "StorageBackend"]
