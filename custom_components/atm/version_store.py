"""Configuration version history store (SPEC Section 16).

Immutable before/after snapshots of agent-driven create/edit/delete of
automations, scripts, scenes, and helpers, with admin-only rollback. Records are
captured at the executor (ATM's single execution chokepoint), so both
directly-allowed and Confirm-approved changes are recorded exactly once.

Storage is a separate HA Store from tokens (``atm_versions``) holding a flat list
of records. Retention is per-resource FIFO: the newest ``MAX_VERSIONS_PER_RESOURCE``
versions per ``(resource_type, resource_id)`` are kept; older ones are evicted on
write. Records are stored raw (un-redacted) because a rollback must round-trip the
exact config; the data is admin-only and no more sensitive than the YAML it mirrors.
"""

from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import asdict, dataclass

from homeassistant.helpers.storage import Store
from homeassistant.util.dt import utcnow

from .const import (
    MAX_VERSIONS_PER_RESOURCE,
    VERSION_STORAGE_VERSION,
    VERSIONED_RESOURCE_TYPES,
)

_LOGGER = logging.getLogger(__name__)

VALID_ACTIONS = frozenset({"create", "edit", "delete", "rollback"})


@dataclass
class VersionRecord:
    """One immutable snapshot of a configuration change.

    ``before`` is None for a create; ``after`` is None for a delete. Configs are
    held raw so a rollback can re-apply the exact payload.
    """

    id: str
    resource_type: str
    resource_id: str
    alias: str | None
    action: str
    before: dict | None
    after: dict | None
    token_id: str | None
    token_name: str | None
    request_id: str | None
    approved_by_user_id: str | None
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "VersionRecord":
        return cls(
            id=raw["id"],
            resource_type=raw["resource_type"],
            resource_id=raw["resource_id"],
            alias=raw.get("alias"),
            action=raw["action"],
            before=raw.get("before"),
            after=raw.get("after"),
            token_id=raw.get("token_id"),
            token_name=raw.get("token_name"),
            request_id=raw.get("request_id"),
            approved_by_user_id=raw.get("approved_by_user_id"),
            timestamp=raw["timestamp"],
        )


class VersionStore:
    """In-memory version history with optional persistence to an HA Store.

    When a Store is provided, mutations are persisted immediately (config changes
    are low-frequency, so there is no flush cycle). Passing no store keeps the
    history in memory only, which the test suite relies on.
    """

    def __init__(self, store: Store | None = None) -> None:
        self._versions: list[VersionRecord] = []
        self._store = store

    async def record(
        self,
        *,
        resource_type: str,
        resource_id: str,
        action: str,
        before: dict | None,
        after: dict | None,
        alias: str | None = None,
        token_id: str | None = None,
        token_name: str | None = None,
        request_id: str | None = None,
        approved_by_user_id: str | None = None,
    ) -> VersionRecord:
        """Append a version record, evict beyond the per-resource cap, and persist.

        Returns the created record. Raises ValueError for an unknown resource type
        or action so a miswired call site fails loudly rather than storing junk.
        """
        if resource_type not in VERSIONED_RESOURCE_TYPES:
            raise ValueError(f"unknown resource_type: {resource_type!r}")
        if action not in VALID_ACTIONS:
            raise ValueError(f"unknown action: {action!r}")
        record = VersionRecord(
            id=uuid.uuid4().hex,
            resource_type=resource_type,
            resource_id=resource_id,
            alias=alias,
            action=action,
            # Deep-copy so a later mutation of the caller's config cannot
            # rewrite stored history: snapshots are immutable.
            before=copy.deepcopy(before),
            after=copy.deepcopy(after),
            token_id=token_id,
            token_name=token_name,
            request_id=request_id,
            approved_by_user_id=approved_by_user_id,
            timestamp=utcnow().isoformat(),
        )
        self._versions.append(record)
        self._evict(resource_type, resource_id)
        await self.async_save()
        return record

    def _evict(self, resource_type: str, resource_id: str) -> None:
        """Drop the oldest records for one resource beyond the retention cap.

        The cap is per resource, so a busy resource cannot evict another's history.
        """
        matching = [
            i for i, r in enumerate(self._versions)
            if r.resource_type == resource_type and r.resource_id == resource_id
        ]
        overflow = len(matching) - MAX_VERSIONS_PER_RESOURCE
        if overflow <= 0:
            return
        # matching is in insertion (oldest-first) order; drop the oldest overflow.
        drop = set(matching[:overflow])
        self._versions = [r for i, r in enumerate(self._versions) if i not in drop]

    def list_for(self, resource_type: str, resource_id: str) -> list[VersionRecord]:
        """Return one resource's versions, newest first."""
        return [
            r for r in reversed(self._versions)
            if r.resource_type == resource_type and r.resource_id == resource_id
        ]

    def list_recent(self, limit: int = 50) -> list[VersionRecord]:
        """Return the most recent versions across all resources, newest first."""
        recent = list(reversed(self._versions))
        return recent[:limit] if limit >= 0 else recent

    def get(self, version_id: str) -> VersionRecord | None:
        """Return a single record by id, or None if no record has that id."""
        return next((r for r in self._versions if r.id == version_id), None)

    async def async_save(self) -> None:
        """Persist the full history. No-op when no store is configured."""
        if self._store is None:
            return
        await self._store.async_save({
            "version": VERSION_STORAGE_VERSION,
            "versions": [r.to_dict() for r in self._versions],
        })

    async def async_load(self) -> None:
        """Populate the history from storage. No-op when no store is configured.

        A storage-version mismatch discards the on-disk history; individual
        corrupt records are skipped with a warning.
        """
        if self._store is None:
            return
        raw = await self._store.async_load()
        if not raw:
            return
        raw_version = raw.get("version")
        if raw_version != VERSION_STORAGE_VERSION:
            _LOGGER.warning(
                "Version storage version mismatch (got %s, expected %s); discarding on-disk history",
                raw_version, VERSION_STORAGE_VERSION,
            )
            return
        for r in raw.get("versions", []):
            try:
                self._versions.append(VersionRecord.from_dict(r))
            except (KeyError, TypeError) as exc:
                _LOGGER.warning("Skipping corrupt version record: %s", exc)

    async def async_wipe(self) -> None:
        """Clear all history from memory and write an empty snapshot to disk."""
        self._versions.clear()
        if self._store is None:
            return
        await self._store.async_save({
            "version": VERSION_STORAGE_VERSION,
            "versions": [],
        })

    def __len__(self) -> int:
        return len(self._versions)
