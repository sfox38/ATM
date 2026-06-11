"""Circular buffer audit log for ATM. Optionally persists entries to HA storage."""

from __future__ import annotations

import json
import logging
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from homeassistant.util.dt import parse_datetime, utcnow

from .const import AUDIT_LOG_MAXLEN, AUDIT_STORAGE_VERSION
from .token_store import GlobalSettings

if TYPE_CHECKING:
    from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

# Outcome values:
#   allowed        - request completed successfully
#   denied         - blocked by permission rules, blocklist, dual-gate, or RED/NO_ACCESS
#   not_found      - entity absent from hass.states and entity registry (ghost pre-check)
#   rate_limited   - token exceeded its rate limit
#   not_implemented - MCP method received but not supported (e.g. resources/templates/list)
#   invalid_request - request was structurally invalid (e.g. template render with bad syntax)
Outcome = Literal["allowed", "denied", "not_found", "rate_limited", "not_implemented", "invalid_request"]

_REDACTED = "[redacted]"

MAX_QUERY_LIMIT = 500
MAX_AUDIT_PAYLOAD_BYTES = 2048


def generate_request_id() -> str:
    """Return a new UUID string for use as X-ATM-Request-ID."""
    return str(uuid.uuid4())


@dataclass
class AuditEntry:
    request_id: str
    timestamp: datetime
    token_id: str
    token_name: str
    method: str
    resource: str
    outcome: Outcome
    client_ip: str
    pass_through: bool = False
    payload: str | None = None

    def to_dict(self) -> dict:
        d = {
            "request_id": self.request_id,
            "timestamp": self.timestamp.isoformat(),
            "token_id": self.token_id,
            "token_name": self.token_name,
            "method": self.method,
            "resource": self.resource,
            "outcome": self.outcome,
            "client_ip": self.client_ip,
            "pass_through": self.pass_through,
        }
        if self.payload is not None:
            d["payload"] = self.payload
        return d


class AuditLog:
    """Circular buffer audit log.

    Logging toggles from GlobalSettings are evaluated at record time, not at
    query time. Sensor counters in sensor.py are updated independently of
    these toggles and always reflect total activity.

    When a Store is provided, the log can be persisted to disk via async_save()
    and restored on startup via async_load(). Passing no store keeps the log
    purely in-memory (test instances, or when audit_flush_interval is 0).
    """

    def __init__(self, maxlen: int = AUDIT_LOG_MAXLEN, store: Store | None = None) -> None:
        self._log: deque[AuditEntry] = deque(maxlen=maxlen)
        self._store = store

    def record(
        self,
        *,
        request_id: str,
        token_id: str,
        token_name: str,
        method: str,
        resource: str,
        outcome: Outcome,
        client_ip: str,
        settings: GlobalSettings,
        pass_through: bool = False,
        payload: dict | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Append an audit entry, subject to the current logging settings.

        Returns without writing if the master kill switch or the relevant
        per-outcome toggle is off. Redaction of resource and client_ip is
        applied when the corresponding setting is off.
        """
        if settings.disable_all_logging:
            return

        if outcome == "allowed" and not settings.log_allowed:
            return
        if outcome in ("denied", "not_found", "not_implemented", "invalid_request") and not settings.log_denied:
            return
        if outcome == "rate_limited" and not settings.log_rate_limited:
            return

        logged_resource = resource if settings.log_entity_names else _REDACTED
        logged_ip = client_ip if settings.log_client_ip else _REDACTED

        logged_payload: str | None = None
        if payload is not None:
            try:
                s = json.dumps(payload, default=str)
                logged_payload = s if len(s) <= MAX_AUDIT_PAYLOAD_BYTES else s[:MAX_AUDIT_PAYLOAD_BYTES] + "...[truncated]"
            except (TypeError, ValueError):
                logged_payload = None

        self._log.append(AuditEntry(
            request_id=request_id,
            timestamp=timestamp or utcnow(),
            token_id=token_id,
            token_name=token_name,
            method=method,
            resource=logged_resource,
            outcome=outcome,
            client_ip=logged_ip,
            pass_through=pass_through,
            payload=logged_payload,
        ))

    _VALID_OUTCOMES = frozenset({"allowed", "denied", "not_found", "rate_limited", "not_implemented", "invalid_request", "pending_approval"})

    def query(
        self,
        *,
        token_id: str | None = None,
        outcome: str | None = None,
        client_ip: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry] | None:
        """Return filtered, paginated audit entries in reverse-chronological order.

        Returns None when outcome is not a recognised value, allowing the caller
        to return a 400 error rather than silently returning an empty list.
        limit is capped at MAX_QUERY_LIMIT (500). offset is applied after filtering.
        """
        if outcome is not None and outcome not in self._VALID_OUTCOMES:
            return None
        limit = min(limit, MAX_QUERY_LIMIT)

        entries = list(self._log)

        if token_id is not None:
            entries = [e for e in entries if e.token_id == token_id]
        if outcome is not None:
            entries = [e for e in entries if e.outcome == outcome]
        if client_ip is not None:
            entries = [e for e in entries if client_ip in e.client_ip]

        entries.reverse()
        return entries[offset:offset + limit]

    def clear(self) -> None:
        """Remove all in-memory entries. Called by tests and the wipe handler."""
        self._log.clear()

    def resize(self, maxlen: int) -> None:
        """Replace the deque with a new one of the given maxlen.

        If maxlen is smaller than the current length, the oldest entries are
        dropped automatically by the deque constructor.
        """
        self._log = deque(self._log, maxlen=maxlen)

    async def async_save(self) -> None:
        """Snapshot the in-memory buffer to HA storage.

        No-op when no store is configured (test instances or Never mode).
        """
        if self._store is None:
            return
        await self._store.async_save({
            "version": AUDIT_STORAGE_VERSION,
            "entries": [e.to_dict() for e in self._log],
        })

    async def async_load(self) -> None:
        """Populate the in-memory buffer from HA storage.

        Corrupt or unrecognised entries are skipped with a warning. No-op when
        no store is configured.
        """
        if self._store is None:
            return
        raw = await self._store.async_load()
        if not raw:
            return
        raw_version = raw.get("version")
        if raw_version != AUDIT_STORAGE_VERSION:
            _LOGGER.warning(
                "Audit storage version mismatch (got %s, expected %s); discarding on-disk log",
                raw_version, AUDIT_STORAGE_VERSION,
            )
            return
        for r in raw.get("entries", []):
            try:
                ts = parse_datetime(r["timestamp"])
                if ts is None:
                    raise ValueError(f"unparseable timestamp: {r['timestamp']!r}")
                outcome = r["outcome"]
                if outcome not in self._VALID_OUTCOMES:
                    raise ValueError(f"unrecognised outcome: {outcome!r}")
                self._log.append(AuditEntry(
                    request_id=r["request_id"],
                    timestamp=ts,
                    token_id=r["token_id"],
                    token_name=r["token_name"],
                    method=r["method"],
                    resource=r["resource"],
                    outcome=outcome,
                    client_ip=r["client_ip"],
                    pass_through=r.get("pass_through", False),
                    payload=r.get("payload"),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                _LOGGER.warning("Skipping corrupt audit entry: %s", exc)

    async def async_wipe(self) -> None:
        """Clear all entries from memory and write an empty snapshot to disk."""
        self._log.clear()
        if self._store is None:
            return
        await self._store.async_save({
            "version": AUDIT_STORAGE_VERSION,
            "entries": [],
        })

    def __len__(self) -> int:
        return len(self._log)
