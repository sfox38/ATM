"""Token storage CRUD for ATM. No business logic lives here."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util.dt import parse_datetime, utcnow

from .const import (
    CAP_ALLOW,
    CAP_DENY,
    CAP_MODES,
    CAPABILITY_NAMES,
    CONFIRM_AVAILABLE_CAPS,
    DEFAULT_RATE_LIMIT_BURST,
    DEFAULT_RATE_LIMIT_REQUESTS,
    MESA_MODE_ADVISORY,
    MESA_MODES,
    PERSONA_CUSTOM,
    PERSONA_NAMES,
    STORAGE_KEY,
    STORAGE_VERSION,
    TOKEN_HEX_LENGTH,
    TOKEN_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

VALID_NODE_STATES = frozenset({"GREY", "YELLOW", "GREEN", "RED"})


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return parse_datetime(value)


@dataclass
class PermissionNode:
    """Permission state for one node in the hierarchy (domain, device, or entity).

    state is one of GREY (inherit), YELLOW (read), GREEN (write), RED (deny).
    hint is an optional human-readable label used in the context endpoint.
    """

    state: str = "GREY"
    hint: str | None = None

    def to_dict(self) -> dict:
        return {"state": self.state, "hint": self.hint}

    @classmethod
    def from_dict(cls, data: dict) -> PermissionNode:
        state = data.get("state", "GREY")
        if state not in VALID_NODE_STATES:
            raise ValueError(f"Invalid permission node state: {state!r}")
        return cls(state=state, hint=data.get("hint"))


@dataclass
class PermissionTree:
    """Three-level permission hierarchy: domains, devices, and entities.

    Each level is a dict keyed by the relevant ID. Nodes with state GREY are
    omitted from storage; their absence implies inheritance.
    """

    domains: dict[str, PermissionNode] = field(default_factory=dict)
    devices: dict[str, PermissionNode] = field(default_factory=dict)
    entities: dict[str, PermissionNode] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "domains": {k: v.to_dict() for k, v in self.domains.items()},
            "devices": {k: v.to_dict() for k, v in self.devices.items()},
            "entities": {k: v.to_dict() for k, v in self.entities.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> PermissionTree:
        return cls(
            domains={k: PermissionNode.from_dict(v) for k, v in data.get("domains", {}).items()},
            devices={k: PermissionNode.from_dict(v) for k, v in data.get("devices", {}).items()},
            entities={k: PermissionNode.from_dict(v) for k, v in data.get("entities", {}).items()},
        )


@dataclass
class TokenRecord:
    """Active ATM token with its configuration and permission tree.

    Capability flags are tri-state strings ("deny" / "allow" / "confirm").
    "confirm" routes the request through the admin-approval gate; only caps
    in CONFIRM_AVAILABLE_CAPS may be set to "confirm".
    """

    id: str
    name: str
    token_hash: str
    created_at: datetime
    created_by: str
    expires_at: datetime | None = None
    revoked: bool = False
    last_used_at: datetime | None = None
    updated_at: datetime | None = None
    pass_through: bool = False
    use_assist_exposure: bool = False
    # When False (default), the MCP tools/list is gated to the tools this token
    # can actually use (its caps and write scope). True announces the full tool
    # surface (diagnostic / opt-out). Not a capability: a presentation toggle.
    announce_all_tools: bool = False
    rate_limit_requests: int = DEFAULT_RATE_LIMIT_REQUESTS
    rate_limit_burst: int = DEFAULT_RATE_LIMIT_BURST
    cap_automation_write: str = CAP_DENY
    cap_script_write: str = CAP_DENY
    cap_config_read: str = CAP_DENY
    cap_template_render: str = CAP_DENY
    cap_restart: str = CAP_DENY
    cap_physical_control: str = CAP_DENY
    cap_service_response: str = CAP_DENY
    cap_broadcast: str = CAP_DENY
    cap_log_read: str = CAP_DENY
    cap_search: str = CAP_DENY
    cap_registry_read: str = CAP_DENY
    cap_traces: str = CAP_DENY
    cap_diagnostics: str = CAP_DENY
    cap_scene_write: str = CAP_DENY
    cap_helper_write: str = CAP_DENY
    cap_integration_write: str = CAP_DENY
    cap_lovelace_write: str = CAP_DENY
    cap_registry_write: str = CAP_DENY
    cap_backup: str = CAP_DENY
    cap_filesystem: str = CAP_DENY
    cap_yaml_edit: str = CAP_DENY
    persona: str = PERSONA_CUSTOM
    permissions: PermissionTree = field(default_factory=PermissionTree)

    def caps_dict(self) -> dict[str, str]:
        """Return the cap_*->mode mapping for this token."""
        return {name: getattr(self, name) for name in CAPABILITY_NAMES}

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked": self.revoked,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "pass_through": self.pass_through,
            **({"use_assist_exposure": self.use_assist_exposure} if self.pass_through else {}),
            "announce_all_tools": self.announce_all_tools,
            "rate_limit_requests": self.rate_limit_requests,
            "rate_limit_burst": self.rate_limit_burst,
            "persona": self.persona,
            "permissions": self.permissions.to_dict(),
        }
        d.update(self.caps_dict())
        return d

    def to_storage_dict(self) -> dict:
        d = self.to_dict()
        d["token_hash"] = self.token_hash
        # use_assist_exposure is only meaningful for pass_through tokens (spec §2.6).
        # to_dict() already conditionally includes it; storage mirrors that behavior
        # so scoped tokens do not accumulate a stale field over their lifetime.
        if self.pass_through:
            d["use_assist_exposure"] = self.use_assist_exposure
        return d

    @classmethod
    def from_dict(cls, data: dict) -> TokenRecord:
        cap_kwargs: dict[str, str] = {}
        for cap_name in CAPABILITY_NAMES:
            value = data.get(cap_name)
            if value not in CAP_MODES:
                value = CAP_DENY
            cap_kwargs[cap_name] = value
        persona = data.get("persona", PERSONA_CUSTOM)
        if persona not in PERSONA_NAMES:
            persona = PERSONA_CUSTOM
        return cls(
            id=data["id"],
            name=data["name"],
            token_hash=data["token_hash"],
            created_at=_parse_dt(data["created_at"]),
            created_by=data["created_by"],
            expires_at=_parse_dt(data.get("expires_at")),
            revoked=data.get("revoked", False),
            last_used_at=_parse_dt(data.get("last_used_at")),
            pass_through=data.get("pass_through", False),
            use_assist_exposure=data.get("use_assist_exposure", False),
            announce_all_tools=data.get("announce_all_tools", False),
            rate_limit_requests=data.get("rate_limit_requests", DEFAULT_RATE_LIMIT_REQUESTS),
            rate_limit_burst=data.get("rate_limit_burst", DEFAULT_RATE_LIMIT_BURST),
            persona=persona,
            updated_at=_parse_dt(data.get("updated_at")),
            permissions=PermissionTree.from_dict(data.get("permissions", {})),
            **cap_kwargs,
        )

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return utcnow() >= self.expires_at

    def is_valid(self) -> bool:
        return not self.revoked and not self.is_expired()


@dataclass
class ArchivedTokenRecord:
    """Archived snapshot of a revoked or expired token. Retained for audit purposes.

    Spec §2.4: only audit-trail fields are retained. pass_through, capability flags,
    rate limit parameters, and permission tree are NOT stored in archived records.
    """

    id: str
    name: str
    token_hash: str
    created_at: datetime
    created_by: str
    revoked_at: datetime
    revoked: bool = False
    expires_at: datetime | None = None
    last_used_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "revoked": self.revoked,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }

    def to_storage_dict(self) -> dict:
        d = self.to_dict()
        d["token_hash"] = self.token_hash
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ArchivedTokenRecord:
        return cls(
            id=data["id"],
            name=data["name"],
            token_hash=data["token_hash"],
            created_at=_parse_dt(data["created_at"]),
            created_by=data["created_by"],
            revoked_at=_parse_dt(data["revoked_at"]),
            revoked=data.get("revoked", False),
            expires_at=_parse_dt(data.get("expires_at")),
            last_used_at=_parse_dt(data.get("last_used_at")),
            # pass_through and other privilege fields are intentionally not loaded even
            # if present in older storage records (spec §2.4 excludes them from archives).
        )


def _clamp_int(value: object, valid: set[int], default: int) -> int:
    try:
        converted = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return converted if converted in valid else default


@dataclass
class GlobalSettings:
    """Integration-wide settings persisted to storage."""

    kill_switch: bool = False
    disable_all_logging: bool = False
    log_allowed: bool = True
    log_denied: bool = True
    log_rate_limited: bool = True
    log_entity_names: bool = True
    log_client_ip: bool = True
    notify_on_rate_limit: bool = False
    notify_on_approval: bool = True
    audit_flush_interval: int = 15
    audit_log_maxlen: int = 10000
    mesa_mode: str = MESA_MODE_ADVISORY
    # Experimental, admin-only convenience: inject a (+)/MESA-pill control into HA's
    # native config list pages so profiles can be created in context. Default off
    # because it DOM-patches the HA frontend (fragile across HA updates).
    mesa_inject_enabled: bool = False

    def to_dict(self) -> dict:
        return {
            "kill_switch": self.kill_switch,
            "disable_all_logging": self.disable_all_logging,
            "log_allowed": self.log_allowed,
            "log_denied": self.log_denied,
            "log_rate_limited": self.log_rate_limited,
            "log_entity_names": self.log_entity_names,
            "log_client_ip": self.log_client_ip,
            "notify_on_rate_limit": self.notify_on_rate_limit,
            "notify_on_approval": self.notify_on_approval,
            "audit_flush_interval": self.audit_flush_interval,
            "audit_log_maxlen": self.audit_log_maxlen,
            "mesa_mode": self.mesa_mode,
            "mesa_inject_enabled": self.mesa_inject_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GlobalSettings:
        mesa_mode = data.get("mesa_mode", MESA_MODE_ADVISORY)
        return cls(
            kill_switch=bool(data.get("kill_switch", False)),
            disable_all_logging=bool(data.get("disable_all_logging", False)),
            log_allowed=bool(data.get("log_allowed", True)),
            log_denied=bool(data.get("log_denied", True)),
            log_rate_limited=bool(data.get("log_rate_limited", True)),
            log_entity_names=bool(data.get("log_entity_names", True)),
            log_client_ip=bool(data.get("log_client_ip", True)),
            notify_on_rate_limit=bool(data.get("notify_on_rate_limit", False)),
            notify_on_approval=bool(data.get("notify_on_approval", True)),
            audit_flush_interval=_clamp_int(data.get("audit_flush_interval"), {0, 5, 10, 15, 30, 60}, 15),
            audit_log_maxlen=_clamp_int(data.get("audit_log_maxlen"), {100, 1000, 5000, 10000}, 10000),
            mesa_mode=mesa_mode if mesa_mode in MESA_MODES else MESA_MODE_ADVISORY,
            mesa_inject_enabled=bool(data.get("mesa_inject_enabled", False)),
        )


class TokenStore:
    """Manages persistent storage and in-memory state for ATM tokens."""

    def __init__(self, hass: HomeAssistant, store: Store) -> None:
        self._hass = hass
        self._store = store
        self._tokens: dict[str, TokenRecord] = {}
        self._archived: dict[str, ArchivedTokenRecord] = {}
        self._pending_approvals: list[dict] = []
        self._settings: GlobalSettings = GlobalSettings()
        # Global per-entity hints (entity_id -> hint text), surfaced to every token
        # in the context endpoint. Distinct from per-token permission-node hints.
        self._entity_hints: dict[str, str] = {}
        self.async_lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    async def async_create(cls, hass: HomeAssistant) -> TokenStore:
        """Create a TokenStore and load persisted data from HA storage."""
        store = _ATMStore(hass, STORAGE_VERSION, STORAGE_KEY)
        instance = cls(hass, store)
        await instance.async_load()
        return instance

    async def async_load(self) -> None:
        """Load token and settings data from the HA storage file.

        Applies the v1 -> v2 migration in place when older storage is detected.
        """
        raw = await self._store.async_load() or {}
        migrated = _migrate_storage_v1_to_v2(raw)

        tokens: dict[str, TokenRecord] = {}
        for r in raw.get("tokens", []):
            try:
                record = TokenRecord.from_dict(r)
                tokens[record.id] = record
            except (KeyError, TypeError, ValueError) as exc:
                _LOGGER.warning("Skipping corrupt token record %r: %s", r.get("id", "?"), exc)
        self._tokens = tokens

        archived: dict[str, ArchivedTokenRecord] = {}
        for r in raw.get("archived_tokens", []):
            try:
                record = ArchivedTokenRecord.from_dict(r)
                archived[record.id] = record
            except (KeyError, TypeError, ValueError) as exc:
                _LOGGER.warning("Skipping corrupt archived token record %r: %s", r.get("id", "?"), exc)
        self._archived = archived

        self._settings = GlobalSettings.from_dict(raw.get("settings", {}))
        self._pending_approvals = list(raw.get("pending_approvals", []))
        self._entity_hints = {
            str(k): str(v) for k, v in (raw.get("entity_hints") or {}).items()
        }

        if migrated:
            _LOGGER.info("ATM storage migrated from v1 to v2; saving canonical form")
            await self.async_save()

    async def async_save(self) -> None:
        """Persist the current in-memory state to HA storage."""
        await self._store.async_save({
            "version": STORAGE_VERSION,
            "tokens": [t.to_storage_dict() for t in self._tokens.values()],
            "archived_tokens": [a.to_storage_dict() for a in self._archived.values()],
            "pending_approvals": self._pending_approvals,
            "settings": self._settings.to_dict(),
            "entity_hints": self._entity_hints,
        })

    def get_pending_approvals(self) -> list[dict]:
        """Return the pending-approvals array. Mutations require async_lock."""
        return self._pending_approvals

    def set_pending_approvals(self, approvals: list[dict]) -> None:
        """Replace the pending-approvals array in memory. Caller must save."""
        self._pending_approvals = approvals

    async def async_create_token(
        self,
        name: str,
        created_by: str,
        expires_at: datetime | None = None,
        pass_through: bool = False,
        use_assist_exposure: bool = False,
        rate_limit_requests: int = DEFAULT_RATE_LIMIT_REQUESTS,
        rate_limit_burst: int = DEFAULT_RATE_LIMIT_BURST,
    ) -> tuple[TokenRecord, str]:
        """Generate a new token, store it, and return (record, raw_token).

        The raw_token value is returned exactly once and never stored. Callers
        must pass it to the client immediately and discard it.
        """
        raw_token = TOKEN_PREFIX + secrets.token_hex(TOKEN_HEX_LENGTH // 2)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        now = utcnow()
        record = TokenRecord(
            id=str(uuid.uuid4()),
            name=name,
            token_hash=token_hash,
            created_at=now,
            created_by=created_by,
            expires_at=expires_at,
            pass_through=pass_through,
            use_assist_exposure=use_assist_exposure if pass_through else False,
            rate_limit_requests=rate_limit_requests,
            rate_limit_burst=rate_limit_burst if rate_limit_requests > 0 else 0,
            updated_at=now,
        )
        self._tokens[record.id] = record
        await self.async_save()
        return record, raw_token

    def get_token_by_id(self, token_id: str) -> TokenRecord | None:
        return self._tokens.get(token_id)

    def get_token_by_hash(self, token_hash: str) -> TokenRecord | None:
        """Find a token by constant-time comparison of SHA-256 hashes."""
        for token in self._tokens.values():
            if hmac_compare(token.token_hash, token_hash):
                return token
        return None

    def get_archived_by_hash(self, token_hash: str) -> ArchivedTokenRecord | None:
        for record in self._archived.values():
            if hmac_compare(record.token_hash, token_hash):
                return record
        return None

    def list_tokens(self) -> list[TokenRecord]:
        return list(self._tokens.values())

    def list_archived(self) -> list[ArchivedTokenRecord]:
        return list(self._archived.values())

    def active_token_count(self) -> int:
        return len(self._tokens)

    def name_slug_exists(self, name: str, exclude_token_id: str | None = None) -> bool:
        """Return True if a token with an equivalent slug already exists.

        exclude_token_id skips that token, so a rename can keep (or re-case) its
        own name without colliding with itself.
        """
        slug = token_name_slug(name)
        return any(
            token_name_slug(t.name) == slug
            for tid, t in self._tokens.items()
            if tid != exclude_token_id
        )

    async def async_archive_token(
        self,
        token_id: str,
        revoked: bool,
        revoked_at: datetime | None = None,
    ) -> ArchivedTokenRecord | None:
        """Move a token from the active list to the archive and persist.

        Returns None if the token_id is not found.
        """
        token = self._tokens.pop(token_id, None)
        if token is None:
            return None
        archived = ArchivedTokenRecord(
            id=token.id,
            name=token.name,
            token_hash=token.token_hash,
            created_at=token.created_at,
            created_by=token.created_by,
            revoked_at=revoked_at or utcnow(),
            revoked=revoked,
            expires_at=token.expires_at,
            last_used_at=token.last_used_at,
        )
        self._archived[archived.id] = archived
        await self.async_save()
        return archived

    async def async_patch_token(
        self,
        token_id: str,
        **kwargs,
    ) -> TokenRecord | None:
        """Update mutable capability and rate-limit fields on a token and persist.

        Callers must hold self.async_lock before calling to prevent concurrent writes.
        When rate_limit_requests is set to 0, rate_limit_burst is forced to 0.
        Returns None if the token is not found.
        """
        token = self._tokens.get(token_id)
        if token is None:
            return None
        mutable_fields = {
            "name",
            "pass_through",
            "use_assist_exposure",
            "announce_all_tools",
            "rate_limit_requests",
            "rate_limit_burst",
            "persona",
        }
        cap_fields = set(CAPABILITY_NAMES)
        cap_changed = False
        for key, value in kwargs.items():
            if key in cap_fields:
                if value not in CAP_MODES:
                    raise ValueError(
                        f"Invalid capability mode for {key!r}: {value!r}"
                    )
                if value == "confirm" and key not in CONFIRM_AVAILABLE_CAPS:
                    raise ValueError(
                        f"Capability {key!r} does not support 'confirm' mode"
                    )
                setattr(token, key, value)
                cap_changed = True
            elif key in mutable_fields:
                if key == "persona" and value not in PERSONA_NAMES:
                    raise ValueError(f"Unknown persona: {value!r}")
                setattr(token, key, value)
        if token.rate_limit_requests == 0:
            token.rate_limit_burst = 0
        # If any capability changed, re-derive persona from the resulting cap set.
        # This keeps the persona label honest: applying a preset and then tweaking
        # any cap drops the token to "custom" (or to a different matching preset).
        if cap_changed:
            from .personas import detect_persona  # noqa: PLC0415 - avoid circular import at module load
            token.persona = detect_persona(token.caps_dict())
        token.updated_at = utcnow()
        await self.async_save()
        return token

    async def async_set_permissions(
        self,
        token_id: str,
        permissions: PermissionTree,
    ) -> TokenRecord | None:
        """Replace the entire permission tree for a token and persist."""
        token = self._tokens.get(token_id)
        if token is None:
            return None
        token.permissions = permissions
        token.updated_at = utcnow()
        await self.async_save()
        return token

    async def async_patch_permission_node(
        self,
        token_id: str,
        node_type: str,
        node_id: str,
        state: str,
        hint: str | None = None,
    ) -> TokenRecord | None:
        """Set or clear a single permission node and persist.

        Setting state to GREY removes the node entirely (GREY is the default
        and is not stored explicitly).
        """
        token = self._tokens.get(token_id)
        if token is None:
            return None
        if node_type not in ("domains", "devices", "entities"):
            return None
        collection = getattr(token.permissions, node_type, None)
        if collection is None:
            return None
        if state == "GREY":
            collection.pop(node_id, None)
        else:
            collection[node_id] = PermissionNode(state=state, hint=hint)
        token.updated_at = utcnow()
        await self.async_save()
        return token

    def update_last_used(self, token_id: str, timestamp: datetime) -> None:
        """Record a last-used timestamp in memory only.

        Flushed periodically to storage by the interval registered in __init__.py
        and immediately on HA shutdown.
        """
        token = self._tokens.get(token_id)
        if token is not None:
            token.last_used_at = timestamp

    async def async_flush_last_used(self) -> None:
        """Flush in-memory last_used_at timestamps to storage."""
        await self.async_save()

    async def async_delete_archived(self, token_id: str) -> bool:
        """Permanently delete an archived token record. Returns False if not found."""
        async with self.async_lock:
            if token_id not in self._archived:
                return False
            del self._archived[token_id]
            await self.async_save()
        return True

    async def async_rotate_token(self, token_id: str) -> tuple[TokenRecord, str] | None:
        """Replace the token hash with a freshly generated value and persist.

        Returns (updated_record, raw_token) on success, or None if the token is not found.
        The raw token is returned exactly once and never stored. Callers must pass it to
        the client immediately and discard it.
        """
        token = self._tokens.get(token_id)
        if token is None:
            return None
        raw_token = TOKEN_PREFIX + secrets.token_hex(TOKEN_HEX_LENGTH // 2)
        token.token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token.updated_at = utcnow()
        await self.async_save()
        return token, raw_token

    def get_settings(self) -> GlobalSettings:
        return self._settings

    def get_entity_hints(self) -> dict[str, str]:
        """Return the global entity-hint map (entity_id -> hint). Mutate via async_set_entity_hint."""
        return self._entity_hints

    async def async_set_entity_hint(self, entity_id: str, hint: str | None) -> None:
        """Set or clear the global hint for an entity, then persist. Falsy hint clears it."""
        if hint:
            self._entity_hints[entity_id] = hint
        else:
            self._entity_hints.pop(entity_id, None)
        await self.async_save()

    async def async_patch_settings(self, **kwargs) -> GlobalSettings:
        """Update any GlobalSettings fields by name and persist."""
        for key, value in kwargs.items():
            if hasattr(self._settings, key):
                setattr(self._settings, key, value)
        await self.async_save()
        return self._settings

    async def async_wipe(self) -> None:
        """Clear all tokens, archived records, and settings, then persist."""
        self._tokens.clear()
        self._archived.clear()
        self._settings = GlobalSettings()
        self._entity_hints = {}
        await self.async_save()


def token_name_slug(name: str) -> str:
    """Normalize a token name to a slug (lowercase, hyphens to underscores)."""
    return name.lower().replace("-", "_")


def hmac_compare(stored_hash: str, presented_hash: str) -> bool:
    """Constant-time comparison of two SHA-256 hex digests."""
    return hmac.compare_digest(stored_hash, presented_hash)


_LEGACY_ALLOW_TO_CAP = {
    "allow_automation_write": "cap_automation_write",
    "allow_script_write": "cap_script_write",
    "allow_config_read": "cap_config_read",
    "allow_template_render": "cap_template_render",
    "allow_restart": "cap_restart",
    "allow_physical_control": "cap_physical_control",
    "allow_service_response": "cap_service_response",
    "allow_broadcast": "cap_broadcast",
    "allow_log_read": "cap_log_read",
}


class _ATMStore(Store):
    """Subclass of HA's Store that wires our v1 -> v2 migration into the load path.

    HA's Store invokes `_async_migrate_func` when the stored major version differs
    from the constructor's version. The default implementation raises
    NotImplementedError; we override it to run `_migrate_storage_v1_to_v2` in place.
    """

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict
    ) -> dict:
        if old_major_version < STORAGE_VERSION:
            _migrate_storage_v1_to_v2(old_data)
        return old_data


def _migrate_storage_v1_to_v2(raw: dict) -> bool:
    """Migrate raw storage data from v1 to v2 in place.

    v1 used boolean allow_* flags. v2 uses cap_* tri-state strings
    ("deny" / "allow" / "confirm"), introduces persona, and adds the
    pending_approvals array. Returns True if any migration was applied.
    """
    if not raw:
        return False
    needs_save = False
    for token in raw.get("tokens", []) or []:
        for old_key, new_key in _LEGACY_ALLOW_TO_CAP.items():
            if old_key in token:
                token[new_key] = CAP_ALLOW if token.pop(old_key) else CAP_DENY
                needs_save = True
        if "persona" not in token:
            token["persona"] = PERSONA_CUSTOM
            needs_save = True
    # Archived records do not retain capability flags (spec §2.4),
    # but if a legacy archive carried them we still drop the keys.
    for archived in raw.get("archived_tokens", []) or []:
        for old_key in _LEGACY_ALLOW_TO_CAP:
            if old_key in archived:
                archived.pop(old_key, None)
                needs_save = True
    if "pending_approvals" not in raw:
        raw["pending_approvals"] = []
        needs_save = True
    return needs_save
