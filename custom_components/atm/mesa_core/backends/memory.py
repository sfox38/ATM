"""In-memory storage backend. Not persistent; for testing and development."""

from __future__ import annotations

import copy
from typing import Any

from custom_components.atm.mesa_core import backends


class MemoryBackend(backends.StorageBackend):
    def __init__(self, initial_data: dict[str, dict[str, Any]] | None = None) -> None:
        self._data: dict[str, dict[str, Any]] = copy.deepcopy(initial_data or {})

    def read(self, key: str) -> dict[str, Any] | None:
        value = self._data.get(key)
        return copy.deepcopy(value) if value is not None else None

    def write(self, key: str, data: dict[str, Any]) -> None:
        self._data[key] = copy.deepcopy(data)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def list_keys(self, prefix: str | None = None) -> list[str]:
        keys = sorted(self._data)
        if prefix is not None:
            keys = [k for k in keys if k.startswith(prefix)]
        return keys
