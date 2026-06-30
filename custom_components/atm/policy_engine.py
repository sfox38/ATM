"""Permission resolution engine for ATM. No I/O."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from .const import (
    BLOCKED_DOMAINS,
    DOMAIN,
    SENSITIVE_ATTRIBUTES,
    SENSITIVE_KEY_SUBSTRINGS,
)
from .token_store import TokenRecord

_LOGGER = logging.getLogger(__name__)


class Permission(str, Enum):
    WRITE = "write"
    READ = "read"
    DENY = "deny"
    NO_ACCESS = "no_access"
    NOT_FOUND = "not_found"


class EntityCreationNotPermitted(Exception):
    def __init__(self, entity_id: str) -> None:
        self.entity_id = entity_id
        super().__init__(
            f"Entity {entity_id!r} is not in the entity registry; "
            "ATM does not permit entity creation via service calls."
        )


_RELATIVE_TIME_RE = re.compile(r"^(\d+)(h|d|w|m)$")
_ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def canonical_entity_id(entity_id: str, hass: HomeAssistant) -> str:
    """Resolve an entity_id (or registry id) to its canonical entity_id.

    Mirrors the alias canonicalization resolve() does internally, so callers that
    fetch state after a permission check use the same id resolve() granted on
    (avoids a false 404 when a registry id or alias was supplied).
    """
    entry = er.async_get(hass).async_get(entity_id)
    return entry.entity_id if entry else entity_id


def resolve(entity_id: str, token: TokenRecord, hass: HomeAssistant) -> Permission:
    """Resolve effective permission for entity_id against a token.

    Return values:
        Permission.WRITE      - write access (unrestricted for pass-through tokens)
        Permission.READ       - read-only access
        Permission.DENY       - explicitly denied; audit outcome: denied
        Permission.NO_ACCESS  - no grant found; audit outcome: denied
        Permission.NOT_FOUND  - ghost entity (not in states or registry); audit outcome: not_found

    Pass-through tokens return WRITE for all entities (after ATM blocklist check).
    The DUAL_GATE_SERVICES check is NOT performed here; it is the caller's responsibility.
    """
    registry = er.async_get(hass)

    # Resolve to canonical entity_id via entity registry
    entry = registry.async_get(entity_id)
    if entry:
        entity_id = entry.entity_id

    domain = entity_id.split(".")[0]

    # Ghost check (before pass-through short-circuit so ghosts are never accessible).
    # entry is already the registry lookup result; no second call needed.
    if hass.states.get(entity_id) is None and entry is None:
        return Permission.NOT_FOUND

    # ATM blocklist - applies even in pass-through mode
    if domain in BLOCKED_DOMAINS:
        return Permission.NO_ACCESS

    # Block ATM's own internal sensor entities regardless of pass_through or permission grants.
    # These live under the sensor domain so BLOCKED_DOMAINS does not catch them.
    if entry is not None and entry.platform == DOMAIN:
        return Permission.NO_ACCESS

    # Pass-through bypasses all entity permission resolution
    if token.pass_through:
        return Permission.WRITE

    permissions = token.permissions
    device_id = entry.device_id if entry else None

    entity_node = permissions.entities.get(entity_id)
    device_node = permissions.devices.get(device_id) if device_id else None
    domain_node = permissions.domains.get(domain)

    # Pass 1: RED check - walk entire ancestor chain before resolving any grant
    for node in (entity_node, device_node, domain_node):
        if node is not None and node.state == "RED":
            return Permission.DENY

    # Pass 2: most specific non-GREY grant
    for node in (entity_node, device_node, domain_node):
        if node is None:
            continue
        if node.state == "GREEN":
            return Permission.WRITE
        if node.state == "YELLOW":
            return Permission.READ

    return Permission.NO_ACCESS


def is_sensitive_key(key: Any) -> bool:
    """Whether an attribute/response key name marks its value as sensitive.

    True when the key is in the fixed SENSITIVE_ATTRIBUTES list or contains any
    SENSITIVE_KEY_SUBSTRINGS marker (case-insensitive). Used to drop secrets that
    third-party integrations surface under arbitrary keys.
    """
    if not isinstance(key, str):
        return False
    if key in SENSITIVE_ATTRIBUTES:
        return True
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_SUBSTRINGS)


def scrub_sensitive_attributes(state: State) -> dict[str, Any]:
    """Return a state dict with sensitive attributes removed."""
    d = state.as_dict()
    clean_attrs = {
        k: v for k, v in d.get("attributes", {}).items()
        if not is_sensitive_key(k)
    }
    return {**d, "attributes": clean_attrs}


def scrub_state_dict(d: dict) -> dict:
    """Return a copy of a raw state dict with sensitive attributes removed.

    Use this when the input is already a dict (e.g. from state history results).
    Use scrub_sensitive_attributes when working with State objects directly.
    """
    attrs = {k: v for k, v in d.get("attributes", {}).items() if not is_sensitive_key(k)}
    return {**d, "attributes": attrs}


def filter_entities_for_token(
    entities: list[State],
    token: TokenRecord,
    hass: HomeAssistant,
) -> list[dict[str, Any]]:
    """Filter a list of State objects to those accessible by token, scrub sensitive attributes.

    Always scrubs sensitive attributes and blocks the ATM domain, even in pass-through mode.
    Applies use_assist_exposure filtering for pass_through tokens (spec §2.6).
    """
    if token.pass_through:
        registry = er.async_get(hass)
        _expose_check = None
        if token.use_assist_exposure:
            from homeassistant.components.homeassistant.exposed_entities import (  # noqa: PLC0415
                async_should_expose as _should_expose,
            )
            _expose_check = lambda eid: _should_expose(hass, "conversation", eid)
        result = []
        for e in entities:
            eid = e.entity_id
            if eid.split(".")[0] in BLOCKED_DOMAINS:
                continue
            entry = registry.async_get(eid)
            if entry is not None and entry.platform == DOMAIN:
                continue
            if _expose_check is not None and not _expose_check(eid):
                continue
            result.append(scrub_sensitive_attributes(e))
        return result
    return [
        scrub_sensitive_attributes(e)
        for e in entities
        if resolve(e.entity_id, token, hass) in (Permission.READ, Permission.WRITE)
    ]


def expand_service_targets(
    *,
    entity_id: str | list[str] | None,
    device_id: str | list[str] | None,
    area_id: str | list[str] | None,
    service_domain: str,
    hass: HomeAssistant,
) -> tuple[set[str], list[str]]:
    """Expand service call targets without permission filtering or entity creation checks.

    Returns (device_area_candidates, explicit_entity_ids) where:
    - device_area_candidates: entity IDs from device/area/'all' expansion (deduplicated set)
    - explicit_entity_ids: entity IDs specified directly (not via 'all') that callers must
      validate against the entity registry before use

    Use resolve_service_targets for the full filtered result. Use this directly only when
    a raw count is needed (e.g. X-ATM-Entities-Requested header).
    """
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    candidates: set[str] = set()
    explicit_ids: list[str] = []

    if entity_id is not None:
        ids = [entity_id] if isinstance(entity_id, str) else list(entity_id)
        for eid in ids:
            if eid == "all":
                for state in hass.states.async_all():
                    if state.entity_id.split(".")[0] == service_domain:
                        candidates.add(state.entity_id)
            else:
                explicit_ids.append(eid)

    if device_id is not None:
        dids = [device_id] if isinstance(device_id, str) else list(device_id)
        for did in dids:
            for entry in entity_registry.entities.values():
                if (
                    entry.device_id == did
                    and entry.domain == service_domain
                    and not entry.disabled_by
                ):
                    candidates.add(entry.entity_id)

    if area_id is not None:
        aids = [area_id] if isinstance(area_id, str) else list(area_id)
        # Build indexes once to avoid O(A*E) and O(D*E) nested iteration.
        device_entity_index: dict[str, list[str]] = {}
        area_entity_index: dict[str, list[str]] = {}
        for entry in entity_registry.entities.values():
            if entry.domain == service_domain and not entry.disabled_by:
                if entry.device_id:
                    device_entity_index.setdefault(entry.device_id, []).append(entry.entity_id)
                if entry.area_id:
                    area_entity_index.setdefault(entry.area_id, []).append(entry.entity_id)
        for aid in aids:
            for eid in area_entity_index.get(aid, []):
                candidates.add(eid)
            for device in device_registry.devices.values():
                if device.area_id == aid:
                    for eid in device_entity_index.get(device.id, []):
                        candidates.add(eid)

    # No explicit targets: expand to all domain entities. This mirrors native HA
    # behavior (a service call with no targets affects all entities in the domain).
    # ATM still filters through resolve_service_targets before calling HA, so only
    # WRITE-permitted entities are included in the final call.
    if entity_id is None and device_id is None and area_id is None:
        for state in hass.states.async_all():
            if state.entity_id.split(".")[0] == service_domain:
                candidates.add(state.entity_id)

    return candidates, explicit_ids


def resolve_service_targets(
    *,
    entity_id: str | list[str] | None = None,
    device_id: str | list[str] | None = None,
    area_id: str | list[str] | None = None,
    service_domain: str,
    token: TokenRecord,
    hass: HomeAssistant,
) -> tuple[list[str], int]:
    """Resolve service call targets to a WRITE-permitted, deduplicated entity_id list.

    Returns (permitted_entities, raw_count) where raw_count is the number of
    candidate entities before permission filtering. This avoids a second call to
    expand_service_targets just to populate X-ATM-Entities-Requested.

    Raises:
        EntityCreationNotPermitted: if an explicit entity_id is not in the entity registry.

    Returns an empty list when no entities pass permission filtering; the caller must
    return 403 in that case.

    ATM never passes device_id, area_id, or 'all' through to HA. This function
    always returns an explicit entity list.
    """
    entity_registry = er.async_get(hass)

    candidates, explicit_ids = expand_service_targets(
        entity_id=entity_id,
        device_id=device_id,
        area_id=area_id,
        service_domain=service_domain,
        hass=hass,
    )

    # Entity creation check for explicit entity_ids; raises immediately if any are not in registry
    for eid in explicit_ids:
        if entity_registry.async_get(eid) is None:
            raise EntityCreationNotPermitted(eid)
        candidates.add(eid)

    raw_count = len(candidates)

    # Deduplicate while preserving order, then filter to WRITE-permitted entities
    seen: set[str] = set()
    permitted: list[str] = []
    for eid in candidates:
        if eid in seen:
            continue
        seen.add(eid)
        if resolve(eid, token, hass) == Permission.WRITE:
            permitted.append(eid)

    return permitted, raw_count


def filter_service_response(
    response_data: Any,
    token: TokenRecord,
    hass: HomeAssistant,
    _depth: int = 0,
) -> Any:
    """Recursively redact secrets and inaccessible entity IDs from service response data.

    Two redactions are applied: any string that is an entity ID the token cannot
    access becomes "<redacted>", and any dict value whose key name is sensitive
    (is_sensitive_key) becomes "<redacted>" without recursing into it.
    """
    if _depth > 10:
        # Depth limit reached. Still redact entity ID strings, but truncate
        # containers to empty rather than returning their raw contents - a dict
        # or list at this depth could contain entity IDs at deeper levels that
        # would bypass redaction if returned as-is.
        if isinstance(response_data, str) and _ENTITY_ID_RE.match(response_data):
            perm = resolve(response_data, token, hass)
            if perm in (Permission.NO_ACCESS, Permission.DENY, Permission.NOT_FOUND):
                return "<redacted>"
            return response_data
        if isinstance(response_data, (dict, list)):
            _LOGGER.warning(
                "filter_service_response: depth limit reached, truncating %s to empty",
                type(response_data).__name__,
            )
            return {} if isinstance(response_data, dict) else []
        return response_data
    if isinstance(response_data, str):
        if _ENTITY_ID_RE.match(response_data):
            perm = resolve(response_data, token, hass)
            if perm in (Permission.NO_ACCESS, Permission.DENY, Permission.NOT_FOUND):
                return "<redacted>"
        return response_data
    if isinstance(response_data, dict):
        return {
            k: "<redacted>" if is_sensitive_key(k)
            else filter_service_response(v, token, hass, _depth + 1)
            for k, v in response_data.items()
        }
    if isinstance(response_data, list):
        return [filter_service_response(item, token, hass, _depth + 1) for item in response_data]
    return response_data


def get_effective_hint(
    token: TokenRecord,
    entity_id: str,
    hass: HomeAssistant,
    entity_hints: dict[str, str] | None = None,
) -> str | None:
    """Return the most specific hint for an entity.

    Checks the per-token entity, device, then domain nodes. If none is set, falls
    back to the global entity_hints map (entity_id -> hint) when provided. The
    per-token node hint always wins over the global hint.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    permissions = token.permissions

    entity_node = permissions.entities.get(entity_id)
    if entity_node and entity_node.hint:
        return entity_node.hint

    if entry and entry.device_id:
        device_node = permissions.devices.get(entry.device_id)
        if device_node and device_node.hint:
            return device_node.hint

    domain = entity_id.split(".")[0]
    domain_node = permissions.domains.get(domain)
    if domain_node and domain_node.hint:
        return domain_node.hint

    if entity_hints:
        return entity_hints.get(entity_id)
    return None


def template_blocklist_vars() -> dict:
    """Return a variables dict that shadows HA template globals to block entity enumeration.

    Pass as **template_blocklist_vars() when building the variables dict for
    Template.async_render(). Jinja2 local variables shadow globals of the same name,
    so these stubs override HA's built-in functions that could bypass ATM filtering.

    Spec §3.4 requirements implemented here:
    - 'secrets' removed entirely (shadowed with None; attribute access raises AttributeError)
    - 'hass' replaced with None (safe subset is impractical to implement; None causes
      AttributeError on any access, which HA's template engine surfaces as a template error)
    - 'config_entry_attr()' blocked (returns None, preventing integration metadata leaks)
    - 'device_attr()' blocked (already present; prevents device metadata enumeration)
    - Enumeration helpers blocked (return empty iterables)
    """
    return {
        # Spec §3.4: remove secrets and replace hass with a safe (non-functional) substitute.
        "hass": None,
        "secrets": None,
        # Spec §3.4: block config_entry_attr() to prevent integration metadata enumeration.
        "config_entry_attr": lambda *a, **kw: None,
        "integration_entities": lambda *a, **kw: [],
        "area_entities": lambda *a, **kw: [],
        "area_devices": lambda *a, **kw: [],
        "device_entities": lambda *a, **kw: [],
        "expand": lambda *a, **kw: [],
        "label_entities": lambda *a, **kw: [],
        "label_areas": lambda *a, **kw: [],
        "floor_entities": lambda *a, **kw: [],
        "floor_areas": lambda *a, **kw: [],
        "device_attr": lambda *a, **kw: None,
        "device_id": lambda *a, **kw: None,
        "areas": lambda *a, **kw: [],
        "labels": lambda *a, **kw: [],
        "label_id": lambda *a, **kw: None,
        "label_name": lambda *a, **kw: None,
        "floors": lambda *a, **kw: [],
        "floor_id": lambda *a, **kw: None,
        "floor_name": lambda *a, **kw: None,
        "closest": lambda *a, **kw: None,
        "is_device_attr": lambda *a, **kw: False,
        "area_id": lambda *a, **kw: None,
        # State access - can reveal state/attributes of out-of-scope entities
        "state_translated": lambda *a, **kw: None,
        "state_attr_translated": lambda *a, **kw: None,
        "entity_name": lambda *a, **kw: None,
        "distance": lambda *a, **kw: None,
        "is_hidden_entity": lambda *a, **kw: False,
        # Device/area/config metadata enumeration
        "config_entry_id": lambda *a, **kw: None,
        "device_name": lambda *a, **kw: None,
        "area_name": lambda *a, **kw: None,
        "label_description": lambda *a, **kw: None,
        "label_devices": lambda *a, **kw: [],
        # HA issues (could reveal integration problems or installed components)
        "issues": lambda *a, **kw: [],
        "issue": lambda *a, **kw: None,
    }


def parse_relative_time(value: str) -> datetime:
    """Parse a relative time string to a UTC datetime.

    Supported formats: '24h' (hours), '7d' (days), '2w' (weeks), '1m' (30-day months).
    Raises ValueError for unrecognized formats.
    """
    match = _RELATIVE_TIME_RE.match(value.strip())
    if not match:
        raise ValueError(f"Unrecognized relative time format: {value!r}")
    n = int(match.group(1))
    unit = match.group(2)
    _MAX_DAYS = 366
    if unit == "h":
        if n > _MAX_DAYS * 24:
            raise ValueError(f"Relative time {value!r} exceeds the maximum allowed range of {_MAX_DAYS} days.")
        delta = timedelta(hours=n)
    elif unit == "d":
        if n > _MAX_DAYS:
            raise ValueError(f"Relative time {value!r} exceeds the maximum allowed range of {_MAX_DAYS} days.")
        delta = timedelta(days=n)
    elif unit == "w":
        if n > 52:
            raise ValueError(f"Relative time {value!r} exceeds the maximum allowed range of 52 weeks.")
        delta = timedelta(weeks=n)
    else:
        if n > 12:
            raise ValueError(f"Relative time {value!r} exceeds the maximum allowed range of 12 months.")
        delta = timedelta(days=30 * n)
    return utcnow() - delta


def resolve_intent_entities(
    hass: HomeAssistant,
    token: TokenRecord,
    *,
    domains: list[str] | None = None,
    device_classes: list[str] | None = None,
    name: str | None = None,
    area: str | None = None,
    floor: str | None = None,
) -> list[str]:
    """Resolve intent-based targeting (area/name/floor/domain/device_class) to entity_id list.

    Uses HA's async_match_targets for name/area/floor resolution to match native HA
    intent scoring behavior. Silently drops entities the token cannot WRITE. Never
    acknowledges blocked or inaccessible entities. Returns an empty list when nothing matches.
    """
    from homeassistant.helpers.intent import (  # noqa: PLC0415
        MatchTargetsConstraints,
        async_match_targets,
    )

    registry = er.async_get(hass)
    all_states = list(hass.states.async_all())

    if token.pass_through and token.use_assist_exposure:
        from homeassistant.components.homeassistant.exposed_entities import (  # noqa: PLC0415
            async_should_expose as _should_expose,
        )
        all_states = [s for s in all_states if _should_expose(hass, "conversation", s.entity_id)]

    permitted: list[State] = [
        s for s in all_states
        if s.entity_id.split(".")[0] not in BLOCKED_DOMAINS
        and not (
            (entry := registry.async_get(s.entity_id)) is not None
            and entry.platform == DOMAIN
        )
        and (token.pass_through or resolve(s.entity_id, token, hass) == Permission.WRITE)
    ]

    if not name and not area and not floor:
        if domains:
            domain_set = set(domains)
            permitted = [s for s in permitted if s.entity_id.split(".")[0] in domain_set]
        if device_classes:
            dc_set = set(device_classes)
            permitted = [s for s in permitted if s.attributes.get("device_class") in dc_set]
        return [s.entity_id for s in permitted]

    constraints = MatchTargetsConstraints(
        name=name,
        area_name=area,
        floor_name=floor,
        domains=domains,
        device_classes=device_classes,
        assistant=None,
    )
    result = async_match_targets(
        hass,
        constraints,
        states=permitted,
    )
    if not result.is_match:
        return []
    return [s.entity_id for s in result.states]
