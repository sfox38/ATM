"""Tests for the MESA storage backend, runtime construction, and settings.

Covers Phase 2 of the mesa-core integration: the dict-backed StorageBackend,
HA Store persistence round-trip, inheritance resolution through the host area
callback, orphan detection, and the mesa_mode setting.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atm.mesa import (
    ATMMesaBackend,
    async_setup_mesa,
    refresh_orphans,
)
from custom_components.atm.mesa_core import ControlMode, SemanticProfile
from custom_components.atm.token_store import GlobalSettings


def _profile(entity_id: str, control_mode: str = "autonomous", **boundaries) -> SemanticProfile:
    ob = {"control_mode": control_mode, **boundaries}
    return SemanticProfile.from_dict(
        entity_id,
        {"semantic_profile": {"semantic_tags": [], "operational_boundaries": ob}},
    )


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


def test_backend_crud_and_snapshot():
    backend = ATMMesaBackend()
    backend.write("light.a", {"x": 1})
    backend.write("__domain__:light", {"y": 2})
    assert backend.read("light.a") == {"x": 1}
    assert backend.read("missing") is None
    assert backend.list_keys() == ["__domain__:light", "light.a"]
    assert backend.list_keys(prefix="light.") == ["light.a"]

    snap = backend.snapshot()
    snap["light.a"]["x"] = 999  # snapshot must be a copy, not a live view
    assert backend.read("light.a") == {"x": 1}

    backend.delete("light.a")
    assert backend.read("light.a") is None


def test_backend_round_trips_through_initial_dict():
    backend = ATMMesaBackend({"light.a": {"x": 1}})
    assert backend.read("light.a") == {"x": 1}


# ---------------------------------------------------------------------------
# Runtime construction + persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_loads_persisted_profiles(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    async with runtime.lock:
        runtime.store.set("light.kitchen", _profile("light.kitchen", "confirm"))
        await runtime.async_save()

    # A fresh runtime over the same HA Store key must see the saved profile.
    reloaded = await async_setup_mesa(hass, "advisory")
    loaded = reloaded.store.get("light.kitchen")
    assert loaded is not None
    assert loaded.operational_boundaries.control_mode is ControlMode.CONFIRM


@pytest.mark.asyncio
async def test_setup_mode_maps_to_enforcer(hass: HomeAssistant):
    advisory = await async_setup_mesa(hass, "advisory")
    assert advisory.enforcer.mode == "advisory"
    off = await async_setup_mesa(hass, "off")
    assert off.enforcer.mode == "advisory"  # off never calls the enforcer
    enforced = await async_setup_mesa(hass, "enforced")
    assert enforced.enforcer.mode == "enforced"

    enforced.set_mode("advisory")
    assert enforced.enforcer.mode == "advisory"


@pytest.mark.asyncio
async def test_confirm_entity_blocks_with_no_channel_rule(hass: HomeAssistant):
    # With interactive=False, a confirm entity blocks with confirm_no_channel
    # BEFORE any challenge is issued. ATM routes this rule to its own approval
    # gate, so mesa-core's ConfirmationManager is never engaged.
    runtime = await async_setup_mesa(hass, "enforced")
    runtime.store.set("lock.front", _profile("lock.front", "confirm"))
    result = runtime.enforcer.evaluate(
        entity_id="lock.front",
        service="lock.lock",
        service_params={"entity_id": "lock.front"},
    )
    assert result.allowed is False
    assert result.rule_applied == "control_mode:confirm_no_channel"


# ---------------------------------------------------------------------------
# Inheritance through the area callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_area_profile_inherited_via_host_callback(
    hass: HomeAssistant,
):
    config_entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    config_entry.add_to_hass(hass)
    area_reg = __import__(
        "homeassistant.helpers.area_registry", fromlist=["async_get"]
    ).async_get(hass)
    area = area_reg.async_create("Bedroom")
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        "light", "test_integration", "uid_bed", suggested_object_id="bed"
    )
    ent_reg.async_update_entity(entry.entity_id, area_id=area.id)
    hass.states.async_set(entry.entity_id, "on", {})

    runtime = await async_setup_mesa(hass, "advisory")
    # Area-level profile says confirm; the entity has no profile of its own.
    runtime.store.set_area_profile(area.id, _profile(area.id, "confirm"))

    effective = runtime.store.get_effective(entry.entity_id)
    assert effective.operational_boundaries.control_mode is ControlMode.CONFIRM


# ---------------------------------------------------------------------------
# Orphans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_orphans_flags_unknown_entities(hass: HomeAssistant):
    hass.states.async_set("light.live", "on", {})
    runtime = await async_setup_mesa(hass, "advisory")
    runtime.store.set("light.live", _profile("light.live"))
    runtime.store.set("light.deleted", _profile("light.deleted"))

    refresh_orphans(hass, runtime)
    assert runtime.orphans == ["light.deleted"]


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------


def test_settings_mesa_mode_defaults_to_advisory():
    assert GlobalSettings().mesa_mode == "advisory"
    assert GlobalSettings.from_dict({}).mesa_mode == "advisory"


def test_settings_mesa_mode_round_trips_valid_values():
    for mode in ("off", "advisory", "enforced"):
        assert GlobalSettings.from_dict({"mesa_mode": mode}).mesa_mode == mode


def test_settings_mesa_mode_rejects_invalid():
    assert GlobalSettings.from_dict({"mesa_mode": "yolo"}).mesa_mode == "advisory"
    assert "mesa_mode" in GlobalSettings().to_dict()
