"""Tests for the MESA confirm-to-approval adapter.

Covers apply_mesa_to_call decision routing (allow/deny/pending), the saved
approval record shape (sentinel cap, non-dispatchable executor, explicit entity
list), confirm-approved re-execution folding, and the admin approve sentinel
skip (the MESA cap must not be auto-rejected by effective_cap).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.atm.audit import AuditLog
from custom_components.atm.const import (
    MESA_APPROVED_EXECUTOR,
    MESA_CONFIRM_CAP,
)
from custom_components.atm.data import ATMData
from custom_components.atm.mesa import apply_mesa_to_call, async_setup_mesa
from custom_components.atm.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.atm.rate_limiter import RateLimiter
from custom_components.atm.token_store import (
    GlobalSettings,
    PermissionTree,
    TokenRecord,
    TokenStore,
)


def _token() -> TokenRecord:
    return TokenRecord(
        id="tok",
        name="t",
        token_hash="x",
        created_at=utcnow(),
        created_by="admin",
        persona="power_user",
        permissions=PermissionTree(),
    )


def _make_store(mesa_mode: str) -> MagicMock:
    store = MagicMock(spec=TokenStore)
    store._pending = []
    store.async_save = AsyncMock()
    store.async_lock = asyncio.Lock()
    store.get_pending_approvals = MagicMock(side_effect=lambda: store._pending)
    store.set_pending_approvals = MagicMock(
        side_effect=lambda lst: setattr(store, "_pending", lst)
    )
    store.get_settings = MagicMock(return_value=GlobalSettings(mesa_mode=mesa_mode))
    return store


async def _make_data(hass: HomeAssistant, mesa_mode: str) -> ATMData:
    runtime = await async_setup_mesa(hass, mesa_mode)
    data = ATMData(
        store=_make_store(mesa_mode),
        rate_limiter=MagicMock(spec=RateLimiter),
        audit=MagicMock(spec=AuditLog),
        mesa=runtime,
    )
    return data


def _set_profile(data, entity_id, control_mode):
    data.mesa.store.set(
        entity_id,
        SemanticProfile.from_dict(
            entity_id,
            {"semantic_profile": {"operational_boundaries": {"control_mode": control_mode}}},
            default_origin=MetadataOrigin.USER,
        ),
    )


async def _apply(hass, data, entities, **kw):
    return await apply_mesa_to_call(
        hass, data, _token(),
        domain="light", service="turn_on", service_data={},
        entities=entities, request_id="rid", client_ip=None, session_id="rid", **kw,
    )


@pytest.mark.asyncio
async def test_off_mode_allows_all(hass: HomeAssistant):
    data = await _make_data(hass, "off")
    _set_profile(data, "light.a", "prohibited")
    outcome = await _apply(hass, data, ["light.a"])
    assert outcome.decision == "allow"
    assert outcome.entities == ["light.a"]


@pytest.mark.asyncio
async def test_all_blocked_denies(hass: HomeAssistant):
    data = await _make_data(hass, "enforced")
    _set_profile(data, "light.a", "prohibited")
    outcome = await _apply(hass, data, ["light.a"])
    assert outcome.decision == "deny"
    assert outcome.blocked


@pytest.mark.asyncio
async def test_confirm_creates_pending_approval(hass: HomeAssistant):
    data = await _make_data(hass, "enforced")
    _set_profile(data, "light.gate", "confirm")
    _set_profile(data, "light.ok", "autonomous")

    with patch("homeassistant.components.persistent_notification.async_create"):
        outcome = await _apply(hass, data, ["light.gate", "light.ok"])

    assert outcome.decision == "pending"
    approval = outcome.approval
    assert approval.cap_name == MESA_CONFIRM_CAP
    assert approval.tool_name == MESA_APPROVED_EXECUTOR
    # The saved args carry the explicit confirm + allowed entity list so the
    # executor re-runs exactly what was reviewed.
    assert approval.args["entity_id"] == ["light.gate", "light.ok"]
    assert approval.args["domain"] == "light"
    assert data.store._pending  # persisted


@pytest.mark.asyncio
async def test_confirm_approved_folds_to_allow(hass: HomeAssistant):
    data = await _make_data(hass, "enforced")
    _set_profile(data, "light.gate", "confirm")
    outcome = await _apply(hass, data, ["light.gate"], confirm_approved=True)
    assert outcome.decision == "allow"
    assert outcome.entities == ["light.gate"]


@pytest.mark.asyncio
async def test_advisory_confirm_allows_and_surfaces_warning(hass: HomeAssistant):
    # Under advisory, a confirm entity is allowed through and the outcome carries
    # a warning for the caller to surface (the mesa_advisory / native speech field).
    data = await _make_data(hass, "advisory")
    _set_profile(data, "light.a", "confirm")
    outcome = await _apply(hass, data, ["light.a"])
    assert outcome.decision == "allow"
    assert outcome.entities == ["light.a"]
    assert any("light.a" in w and "advisory" in w for w in outcome.warnings)


@pytest.mark.asyncio
async def test_confirm_approved_reexec_emits_no_advisory_warning(hass: HomeAssistant):
    # The approved re-execution path must not re-warn (the action was approved,
    # not waved through by advisory mode).
    data = await _make_data(hass, "enforced")
    _set_profile(data, "light.a", "confirm")
    outcome = await _apply(hass, data, ["light.a"], confirm_approved=True)
    assert outcome.decision == "allow"
    assert outcome.warnings == []


@pytest.mark.asyncio
async def test_missing_runtime_allows_all(hass: HomeAssistant):
    data = await _make_data(hass, "enforced")
    data.mesa = None
    outcome = await _apply(hass, data, ["light.a"])
    assert outcome.decision == "allow"
    assert outcome.entities == ["light.a"]
