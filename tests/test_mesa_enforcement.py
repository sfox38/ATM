"""Tests for MESA per-entity enforcement classification.

Exercises evaluate_service_entities over a real MesaRuntime: the advisory vs
enforced vs off behaviour, the interactive=False confirm routing, per-profile
enforcement_mode override, and confirm-approved folding.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.atm.mesa import (
    async_setup_mesa,
    build_mesa_service_diff,
    evaluate_service_entities,
)
from custom_components.atm.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.atm.token_store import PermissionTree, TokenRecord


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


def _set_profile(runtime, entity_id, control_mode, *, enforcement_mode=None):
    # Stamp source: user, mirroring operator-authored profiles. An unknown-origin
    # autonomous declaration is clamped to confirm by MESA (untrusted loosening).
    ob = {"control_mode": control_mode}
    if enforcement_mode is not None:
        ob["enforcement_mode"] = enforcement_mode
    runtime.store.set(
        entity_id,
        SemanticProfile.from_dict(
            entity_id,
            {"semantic_profile": {"operational_boundaries": ob}},
            default_origin=MetadataOrigin.USER,
        ),
    )


def _evaluate(runtime, mode, entities, **kw):
    return evaluate_service_entities(
        runtime, mode, _token(), entities,
        domain="light", service="turn_on", service_data={}, session_id="s", **kw,
    )


@pytest.mark.asyncio
async def test_autonomous_allowed(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.a", "autonomous")
    verdict = _evaluate(runtime, "enforced", ["light.a"])
    assert verdict.allowed == ["light.a"]
    assert verdict.confirm == [] and verdict.blocked == []


@pytest.mark.asyncio
async def test_prohibited_blocks_under_enforced(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.a", "prohibited")
    verdict = _evaluate(runtime, "enforced", ["light.a"])
    assert verdict.allowed == []
    assert verdict.blocked and verdict.blocked[0][1] == "control_mode:prohibited"


@pytest.mark.asyncio
async def test_prohibited_warns_but_allows_under_advisory(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    _set_profile(runtime, "light.a", "prohibited")
    verdict = _evaluate(runtime, "advisory", ["light.a"])
    assert verdict.allowed == ["light.a"]
    assert verdict.blocked == []


@pytest.mark.asyncio
async def test_read_only_blocks_even_under_advisory(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    _set_profile(runtime, "light.a", "read_only")
    verdict = _evaluate(runtime, "advisory", ["light.a"])
    assert verdict.allowed == []
    assert verdict.blocked and verdict.blocked[0][1] == "control_mode:read_only"


@pytest.mark.asyncio
async def test_confirm_routes_to_confirm_under_enforced(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.a", "confirm")
    verdict = _evaluate(runtime, "enforced", ["light.a"])
    assert verdict.confirm == ["light.a"]
    assert verdict.allowed == [] and verdict.blocked == []


@pytest.mark.asyncio
async def test_confirm_allows_with_warning_under_advisory(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    _set_profile(runtime, "light.a", "confirm")
    verdict = _evaluate(runtime, "advisory", ["light.a"])
    assert verdict.allowed == ["light.a"]
    assert verdict.confirm == []
    # The agent must be told the action was confirm-gated but allowed because
    # MESA is advisory (otherwise advisory is indistinguishable from off).
    assert any("light.a" in w and "advisory" in w for w in verdict.warnings)


@pytest.mark.asyncio
async def test_confirm_approved_emits_no_advisory_warning(hass: HomeAssistant):
    # Re-execution after admin approval must not re-warn (it was approved, not
    # waved through by advisory mode).
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.a", "confirm")
    verdict = _evaluate(runtime, "enforced", ["light.a"], confirm_approved=True)
    assert verdict.allowed == ["light.a"]
    assert verdict.warnings == []


@pytest.mark.asyncio
async def test_per_profile_enforced_overrides_global_advisory(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    _set_profile(runtime, "light.a", "confirm", enforcement_mode="enforced")
    verdict = _evaluate(runtime, "advisory", ["light.a"])
    assert verdict.confirm == ["light.a"]


@pytest.mark.asyncio
async def test_confirm_approved_folds_into_allowed(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.a", "confirm")
    verdict = _evaluate(runtime, "enforced", ["light.a"], confirm_approved=True)
    assert verdict.allowed == ["light.a"]
    assert verdict.confirm == []


@pytest.mark.asyncio
async def test_mixed_entities_split_correctly(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.ok", "autonomous")
    _set_profile(runtime, "light.gate", "confirm")
    _set_profile(runtime, "light.no", "prohibited")
    verdict = _evaluate(runtime, "enforced", ["light.ok", "light.gate", "light.no"])
    assert verdict.allowed == ["light.ok"]
    assert verdict.confirm == ["light.gate"]
    assert [b[0] for b in verdict.blocked] == ["light.no"]


@pytest.mark.asyncio
async def test_diff_includes_mesa_block(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "enforced")
    _set_profile(runtime, "light.gate", "confirm")
    _set_profile(runtime, "light.ok", "autonomous")
    verdict = _evaluate(runtime, "enforced", ["light.gate", "light.ok"])
    diff = build_mesa_service_diff("light", "turn_on", {"brightness_pct": 50}, verdict)
    assert diff["kind"] == "service_preview"
    assert diff["preview"]["mesa"]["confirm_entities"] == ["light.gate"]
    assert diff["preview"]["resolved_entity_ids"] == ["light.gate", "light.ok"]
