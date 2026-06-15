"""MESA semantic-safety runtime for ATM.

This module wires the vendored mesa-core library (custom_components.atm.mesa_core)
into the ATM integration. mesa-core is per-ENTITY policy (what is safe to touch
at all); ATM tokens are per-CALLER policy (who may touch what). The two layer:
ATM resolves and flattens targets first, then MESA evaluates the explicit entity
list. MESA is orthogonal to the token permission tree and applies even to
pass-through tokens.

Design notes that are easy to get wrong:

- The profile store is backed by an in-memory dict (ATMMesaBackend) persisted
  through HA's Store. Because the backend never blocks, mesa-core's synchronous
  APIs run directly on the event loop; the ``a*`` (to_thread) variants are never
  used here.
- The MesaEnforcer is constructed with ``interactive=False``. With no
  interaction channel, a ``control_mode: confirm`` entity is blocked with rule
  ``control_mode:confirm_no_channel`` BEFORE any confirmation challenge is
  issued, so mesa-core's ConfirmationManager stays empty and we never touch its
  private state. ATM interprets that block itself: in advisory mode it becomes a
  warning, in enforced mode it routes to ATM's admin approval gate.
- "enforced-ness" is recomputed host-side per entity from public data
  (``settings.mesa_mode == enforced`` or the effective profile's
  ``enforcement_mode``), mirroring MesaEnforcer._is_enforced.

mesa-core is a read-only dependency; never modify it and never reach into its
private state. Report any mesa-core bug to the maintainer.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr_mod
from homeassistant.helpers import entity_registry as er_mod
from homeassistant.helpers.storage import Store
from homeassistant.util.yaml import load_yaml as _load_yaml

from homeassistant.util import dt as dt_util

from .const import (
    MESA_APPROVED_EXECUTOR,
    MESA_CONFIRM_CAP,
    MESA_MODE_ENFORCED,
    MESA_MODE_OFF,
    MESA_STORAGE_KEY,
    MESA_STORAGE_VERSION,
)
from .mesa_core import (
    CallerContext,
    InheritanceResolver,
    MesaEnforcer,
    ProfileStore,
    TriggerValidator,
    import_from_integration,
)
from .mesa_core.backends import StorageBackend
from .mesa_core.exceptions import MesaError

if TYPE_CHECKING:
    from .approvals import PendingApproval
    from .data import ATMData
    from .mesa_core import SemanticProfile, ValidationIssue
    from .token_store import TokenRecord

_LOGGER = logging.getLogger(__name__)

_AUTOMATION_YAML = "automations.yaml"


class ATMMesaBackend(StorageBackend):
    """In-memory dict storage backend persisted via HA's Store.

    All reads/writes/deletes operate on an in-process dict, so mesa-core's
    synchronous API stays non-blocking on the event loop. Durability is the
    caller's responsibility: mutate, then call MesaRuntime.async_save().
    """

    def __init__(self, initial: dict[str, dict[str, Any]] | None = None) -> None:
        self._data: dict[str, dict[str, Any]] = dict(initial or {})

    def read(self, key: str) -> dict[str, Any] | None:
        value = self._data.get(key)
        return dict(value) if value is not None else None

    def write(self, key: str, data: dict[str, Any]) -> None:
        self._data[key] = dict(data)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def list_keys(self, prefix: str | None = None) -> list[str]:
        keys = sorted(self._data)
        if prefix is not None:
            keys = [k for k in keys if k.startswith(prefix)]
        return keys

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Deep-enough copy for persistence (profile docs are plain JSON)."""
        return {k: dict(v) for k, v in self._data.items()}


@dataclass
class MesaRuntime:
    """Holds the constructed mesa-core objects and ATM-side caches."""

    hass: HomeAssistant
    backend: ATMMesaBackend
    store: ProfileStore
    resolver: InheritanceResolver
    enforcer: MesaEnforcer
    validator: TriggerValidator
    ha_store: Store
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    trigger_issues: list[ValidationIssue] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)

    async def async_save(self) -> None:
        """Persist the current profile set to HA storage.

        Callers already hold ``self.lock`` for the mutate-then-save sequence.
        """
        await self.ha_store.async_save({"profiles": self.backend.snapshot()})

    def set_mode(self, mesa_mode: str) -> None:
        """Update the enforcer's global mode after a settings change.

        ``off`` never calls the enforcer (the verdict helper short-circuits), so
        it maps to advisory here for safety if the enforcer is ever consulted.
        """
        self.enforcer.mode = "enforced" if mesa_mode == MESA_MODE_ENFORCED else "advisory"


def _build_get_entity_area(hass: HomeAssistant) -> Callable[[str], str | None]:
    """Return a sync callback mapping entity_id to area_id (device fallback).

    Mirrors mcp_view._resolve_area_id but is defined here to avoid an import
    cycle (mcp_view imports this module for enforcement).
    """

    def _get_entity_area(entity_id: str) -> str | None:
        er = er_mod.async_get(hass)
        entry = er.async_get(entity_id)
        if entry is None:
            return None
        if entry.area_id:
            return entry.area_id
        if entry.device_id:
            device = dr_mod.async_get(hass).async_get(entry.device_id)
            if device and device.area_id:
                return device.area_id
        return None

    return _get_entity_area


def _build_get_state(hass: HomeAssistant) -> Callable[[str], str | None]:
    def _get_state(entity_id: str) -> str | None:
        state = hass.states.get(entity_id)
        return state.state if state is not None else None

    return _get_state


def read_automation_configs(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Read automations.yaml as a list of HA automation config dicts.

    Performs file I/O; run via hass.async_add_executor_job. Returns [] when the
    file is missing or malformed (the TriggerValidator tolerates an empty set).
    """
    path = hass.config.path(_AUTOMATION_YAML)
    if not os.path.isfile(path):
        return []
    try:
        data = _load_yaml(path)
    except Exception:  # noqa: BLE001 - a broken YAML file must not crash setup
        _LOGGER.warning("MESA: could not parse %s for trigger validation", path, exc_info=True)
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def build_caller_context(token: TokenRecord, session_id: str) -> CallerContext:
    """Map an ATM token to a MESA CallerContext.

    ATM has no per-user roles; the token's persona is its only role-like
    attribute, so MESA access_roles rules are persona-granular. caller_id uses
    the token id (stable across renames); the name is surfaced as display_name.
    """
    return CallerContext(
        caller_id=token.id,
        roles=[token.persona] if token.persona else [],
        is_authenticated=True,
        session_id=session_id,
        display_name=token.name,
    )


async def async_setup_mesa(hass: HomeAssistant, mesa_mode: str) -> MesaRuntime:
    """Construct the MESA runtime, loading any persisted profiles.

    Built even when the kill switch is on: the admin profile API must work
    regardless, and the enforcement gate is simply never reached when no proxy
    or MCP routes are registered.
    """
    ha_store = Store(hass, MESA_STORAGE_VERSION, MESA_STORAGE_KEY)
    raw = await ha_store.async_load() or {}
    backend = ATMMesaBackend(raw.get("profiles") or {})

    get_entity_area = _build_get_entity_area(hass)
    store = ProfileStore(backend=backend, get_entity_area=get_entity_area)
    resolver = InheritanceResolver(store=store, get_entity_area=get_entity_area)
    store.attach_resolver(resolver)
    enforcer = MesaEnforcer(
        store=store,
        resolver=resolver,
        mode="enforced" if mesa_mode == MESA_MODE_ENFORCED else "advisory",
        interactive=False,
        get_state=_build_get_state(hass),
    )
    validator = TriggerValidator(store=store)

    return MesaRuntime(
        hass=hass,
        backend=backend,
        store=store,
        resolver=resolver,
        enforcer=enforcer,
        validator=validator,
        ha_store=ha_store,
    )


async def async_import_sidecar_profiles(hass: HomeAssistant, runtime: MesaRuntime) -> int:
    """Import developer mesa_profile.json sidecars from installed integrations.

    Domains that already carry an operator-authored (source: user) domain
    profile are skipped so restarts never clobber manual edits. Returns the
    number of domain profiles imported.
    """
    components_dir = hass.config.path("custom_components")

    def _scan() -> list[SemanticProfile]:
        found: list[SemanticProfile] = []
        if not os.path.isdir(components_dir):
            return found
        for name in sorted(os.listdir(components_dir)):
            path = os.path.join(components_dir, name)
            if not os.path.isdir(path):
                continue
            try:
                profile = import_from_integration(path)
            except MesaError:
                _LOGGER.warning("MESA: malformed sidecar in %s; skipping", name, exc_info=True)
                continue
            if profile is not None:
                found.append(profile)
        return found

    profiles = await hass.async_add_executor_job(_scan)
    imported = 0
    async with runtime.lock:
        for profile in profiles:
            existing = runtime.store.get_domain_profile(profile.entity_id)
            if existing is not None and existing.metadata.source.value == "user":
                continue
            runtime.store.set_domain_profile(profile.entity_id, profile)
            imported += 1
        if imported:
            await runtime.async_save()
    return imported


async def async_refresh_trigger_issues(hass: HomeAssistant, runtime: MesaRuntime) -> None:
    """Re-run the TriggerValidator and cache the results on the runtime."""
    configs = await hass.async_add_executor_job(read_automation_configs, hass)
    runtime.trigger_issues = await hass.async_add_executor_job(
        runtime.validator.validate, lambda: configs
    )


def refresh_orphans(hass: HomeAssistant, runtime: MesaRuntime) -> None:
    """Recompute stored profiles whose entity no longer exists in the registry."""
    er = er_mod.async_get(hass)
    known = set(er.entities) | set(hass.states.async_entity_ids())
    runtime.orphans = runtime.store.find_orphans(known)


# ---------------------------------------------------------------------------
# Enforcement (Phase 3)
# ---------------------------------------------------------------------------


@dataclass
class MesaVerdict:
    """Per-entity MESA outcome for one flattened service call.

    allowed: entities that may proceed now. confirm: entities whose profile
    requires admin confirmation (enforced mode). blocked: (entity, rule, reason)
    for entities MESA refuses outright (read_only, prohibited, declared limit,
    privacy). warnings: advisory messages collected across all entities.
    """

    allowed: list[str] = field(default_factory=list)
    confirm: list[str] = field(default_factory=list)
    blocked: list[tuple[str, str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _host_enforced(settings_mode: str, result: Any) -> bool:
    """Recompute MesaEnforcer._is_enforced host-side from public result data."""
    if settings_mode == MESA_MODE_ENFORCED:
        return True
    boundaries = result.effective_profile.operational_boundaries
    return getattr(boundaries, "enforcement_mode", "advisory") == "enforced"


def evaluate_service_entities(
    runtime: MesaRuntime,
    settings_mode: str,
    token: TokenRecord,
    entities: list[str],
    *,
    domain: str,
    service: str,
    service_data: dict[str, Any],
    session_id: str,
    confirm_approved: bool = False,
) -> MesaVerdict:
    """Evaluate every flattened entity through the MesaEnforcer.

    The enforcer runs with interactive=False, so a confirm entity surfaces as a
    ``control_mode:confirm_no_channel`` block. We interpret that host-side: under
    enforcement it becomes a confirm (routed to the admin gate); under advisory
    it becomes an allowed-with-warning. ``confirm_approved=True`` (re-execution
    after admin approval) folds confirm entities into allowed.
    """
    verdict = MesaVerdict()
    caller = build_caller_context(token, session_id)
    service_str = f"{domain}.{service}"
    now = dt_util.now()
    for entity_id in entities:
        result = runtime.enforcer.evaluate(
            entity_id=entity_id,
            service=service_str,
            service_params={"entity_id": entity_id, **service_data},
            caller_context=caller,
            current_time=now,
        )
        verdict.warnings.extend(result.warnings)
        if result.allowed:
            verdict.allowed.append(entity_id)
            continue
        rule = result.rule_applied or "control_mode:blocked"
        if rule == "control_mode:confirm_no_channel":
            if confirm_approved or not _host_enforced(settings_mode, result):
                verdict.allowed.append(entity_id)
                # Advisory mode lets a confirm entity through; tell the agent why,
                # since otherwise the call looks identical to an unrestricted one.
                if not confirm_approved:
                    verdict.warnings.append(
                        f"{entity_id}: this action is marked 'confirm' in its MESA profile; "
                        "proceeding because MESA is in advisory mode (it would require admin "
                        "approval under enforced mode)."
                    )
            else:
                verdict.confirm.append(entity_id)
        else:
            verdict.blocked.append((entity_id, rule, result.reason))
    return verdict


def build_mesa_service_diff(
    domain: str,
    service: str,
    service_data: dict[str, Any],
    verdict: MesaVerdict,
) -> dict[str, Any]:
    """Build a service_preview diff for a MESA-gated approval (admin review UI)."""
    return {
        "kind": "service_preview",
        "summary": f"MESA confirmation for {domain}.{service}",
        "target": {"type": "service", "id": f"{domain}.{service}", "label": f"{domain}.{service}"},
        "preview": {
            "domain": domain,
            "service": service,
            "resolved_entity_ids": list(verdict.confirm + verdict.allowed),
            "service_data": dict(service_data),
            "mesa": {
                "confirm_entities": list(verdict.confirm),
                "allowed_entities": list(verdict.allowed),
                "blocked": [
                    {"entity_id": e, "rule": r, "reason": reason}
                    for e, r, reason in verdict.blocked
                ],
                "warnings": list(verdict.warnings),
            },
        },
    }


def _mesa_call_args(
    domain: str, service: str, service_data: dict[str, Any], entities: list[str]
) -> dict[str, Any]:
    """Saved-args payload for a MESA approval, re-runnable by the executor.

    Saves the explicit flattened entity list (the confirm + already-allowed
    entities) rather than the original area/name targets, so re-execution fires
    on exactly what was reviewed. The executor re-resolves scope per entity and
    re-runs MESA under confirm-approved semantics; entities that became
    prohibited or read_only since the request are still rejected.
    """
    return {
        "domain": domain,
        "service": service,
        "service_data": dict(service_data),
        "entity_id": list(entities),
    }


@dataclass
class MesaGateOutcome:
    """Result of applying the MESA gate to one service call."""

    decision: str  # "allow" | "deny" | "pending"
    entities: list[str] = field(default_factory=list)
    approval: PendingApproval | None = None
    blocked: list[tuple[str, str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def apply_mesa_to_call(
    hass: HomeAssistant,
    data: ATMData,
    token: TokenRecord,
    *,
    domain: str,
    service: str,
    service_data: dict[str, Any],
    entities: list[str],
    request_id: str,
    client_ip: str | None,
    session_id: str,
    confirm_approved: bool = False,
) -> MesaGateOutcome:
    """Apply MESA enforcement to an already-flattened, ATM-permitted entity list.

    Returns an outcome the caller maps to its own response shape:
    - allow: proceed with ``outcome.entities`` (a subset of the input).
    - deny: every entity was blocked; caller returns its standard Forbidden.
    - pending: at least one entity needs confirmation; an approval was created.

    ``mesa_mode == off`` or a missing runtime short-circuits to allow-all.
    """
    runtime = data.mesa
    settings = data.store.get_settings()
    if runtime is None or settings.mesa_mode == MESA_MODE_OFF:
        return MesaGateOutcome(decision="allow", entities=list(entities))

    verdict = evaluate_service_entities(
        runtime,
        settings.mesa_mode,
        token,
        entities,
        domain=domain,
        service=service,
        service_data=service_data,
        session_id=session_id,
        confirm_approved=confirm_approved,
    )

    if verdict.confirm and not confirm_approved:
        diff = build_mesa_service_diff(domain, service, service_data, verdict)
        approval = await create_mesa_approval(
            hass,
            data,
            token,
            args=_mesa_call_args(
                domain, service, service_data, verdict.confirm + verdict.allowed
            ),
            diff=diff,
            request_id=request_id,
            client_ip=client_ip,
        )
        return MesaGateOutcome(
            decision="pending",
            approval=approval,
            blocked=verdict.blocked,
            warnings=verdict.warnings,
        )

    if not verdict.allowed:
        return MesaGateOutcome(
            decision="deny", blocked=verdict.blocked, warnings=verdict.warnings
        )

    # NOTE (deferred, low severity): warnings flow to the token via mesa_advisory /
    # native speech regardless of cap_config_read, so an advisory warning reveals an
    # in-scope entity's control_mode even to a token that lacks profile-read access.
    # Not an enumeration oracle (the token targeted an entity it already has WRITE on).
    # If we ever want to hide it, gate warnings on cap_config_read at the surfacing sites.
    return MesaGateOutcome(
        decision="allow",
        entities=verdict.allowed,
        blocked=verdict.blocked,
        warnings=verdict.warnings,
    )


async def create_mesa_approval(
    hass: HomeAssistant,
    data: ATMData,
    token: TokenRecord,
    *,
    args: dict[str, Any],
    diff: dict[str, Any],
    request_id: str,
    client_ip: str | None,
) -> PendingApproval:
    """Create a PendingApproval for a MESA confirm, mirroring evaluate_capability.

    The record carries the MESA sentinel cap (skips the effective_cap recheck on
    approve) and the non-dispatchable executor key (re-runs the call under MESA
    confirm-approved semantics).
    """
    from .approvals import (
        create_approval_notification,
        create_pending_approval,
        fire_approval_requested_event,
    )

    async with data.store.async_lock:
        approval = await create_pending_approval(
            data.store,
            token_id=token.id,
            token_name=token.name,
            tool_name=MESA_APPROVED_EXECUTOR,
            cap_name=MESA_CONFIRM_CAP,
            args=args,
            diff=diff,
            request_id=request_id,
            client_ip=client_ip,
        )
    create_approval_notification(hass, approval)
    fire_approval_requested_event(hass, approval)
    return approval


def fire_mesa_blocked_event(
    hass: HomeAssistant, token: TokenRecord, blocked: list[tuple[str, str, str]]
) -> None:
    """Fire atm_mesa_blocked for each entity MESA refused (automation hooks)."""
    from .const import DOMAIN

    for entity_id, rule, _reason in blocked:
        hass.bus.async_fire(
            f"{DOMAIN}_mesa_blocked",
            {
                "token_id": token.id,
                "token_name": token.name,
                "entity_id": entity_id,
                "rule_applied": rule,
                "timestamp": dt_util.utcnow().isoformat(),
            },
        )
