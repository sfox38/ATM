"""Tests for MESA runtime startup tasks and host callbacks.

Covers sidecar developer-profile import (with the user-source skip), the
CallerContext mapping, and TriggerValidator wiring.
"""

from __future__ import annotations

import json
import os

import pytest
from homeassistant.core import HomeAssistant

from custom_components.atm.mesa import (
    async_import_sidecar_profiles,
    async_refresh_trigger_issues,
    async_setup_mesa,
    build_caller_context,
)
from custom_components.atm.mesa_core import MetadataOrigin, SemanticProfile
from custom_components.atm.token_store import PermissionTree, TokenRecord
from homeassistant.util.dt import utcnow


def _write_sidecar(hass: HomeAssistant, domain: str, body: dict) -> None:
    base = hass.config.path("custom_components", domain)
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "mesa_profile.json"), "w", encoding="utf-8") as fh:
        json.dump(body, fh)


def _token(persona: str = "voice_assistant") -> TokenRecord:
    return TokenRecord(
        id="tok-1",
        name="my_token",
        token_hash="x",
        created_at=utcnow(),
        created_by="admin",
        persona=persona,
        permissions=PermissionTree(),
    )


@pytest.mark.asyncio
async def test_sidecar_import_loads_developer_profile(hass: HomeAssistant):
    _write_sidecar(
        hass,
        "my_integration",
        {"semantic_profile": {"semantic_tags": ["climate.heating"]}},
    )
    runtime = await async_setup_mesa(hass, "advisory")
    count = await async_import_sidecar_profiles(hass, runtime)
    assert count == 1
    # Sidecars import as integration-scope profiles keyed by the component name.
    integration_profile = runtime.store.get_integration_profile("my_integration")
    assert integration_profile is not None
    # Sidecar without metadata_origin is stamped developer (Spec 5.3).
    assert integration_profile.metadata.source is MetadataOrigin.DEVELOPER


@pytest.mark.asyncio
async def test_sidecar_import_skips_user_authored_integration(hass: HomeAssistant):
    _write_sidecar(
        hass,
        "my_integration",
        {"semantic_profile": {"semantic_tags": ["climate.heating"]}},
    )
    runtime = await async_setup_mesa(hass, "advisory")
    # Operator already authored an integration profile: import must not clobber it.
    user_profile = SemanticProfile.from_dict(
        "my_integration",
        {"semantic_profile": {"semantic_tags": ["operator.custom"]}},
        default_origin=MetadataOrigin.USER,
    )
    runtime.store.set_integration_profile("my_integration", user_profile)

    count = await async_import_sidecar_profiles(hass, runtime)
    assert count == 0
    kept = runtime.store.get_integration_profile("my_integration")
    assert kept.semantic_tags == ["operator.custom"]


@pytest.mark.asyncio
async def test_get_entity_integration_maps_to_platform(hass: HomeAssistant):
    """The host callback maps an entity to the integration (platform) that made it."""
    from homeassistant.helpers import entity_registry as er

    from custom_components.atm.mesa import _build_get_entity_integration

    entry = er.async_get(hass).async_get_or_create(
        "light", "hue", "uid_x", suggested_object_id="hue_lamp"
    )
    get_integration = _build_get_entity_integration(hass)
    assert get_integration(entry.entity_id) == "hue"
    assert get_integration("light.nonexistent") is None


@pytest.mark.asyncio
async def test_integration_profile_resolves_for_its_entities(hass: HomeAssistant):
    """An integration profile governs the entities that integration created, even
    when those entities live under a different entity domain (where a domain
    profile keyed by the component name would not apply)."""
    from homeassistant.helpers import entity_registry as er

    entry = er.async_get(hass).async_get_or_create(
        "switch", "yale_access_bluetooth", "uid_lock", suggested_object_id="front"
    )
    runtime = await async_setup_mesa(hass, "advisory")
    runtime.store.set_integration_profile(
        "yale_access_bluetooth",
        SemanticProfile.from_dict(
            "yale_access_bluetooth",
            {"semantic_profile": {"operational_boundaries": {"control_mode": "confirm"}}},
        ),
    )
    assert runtime.resolver.has_profile(entry.entity_id)


@pytest.mark.asyncio
async def test_trigger_validator_flags_none_declared_entity(hass: HomeAssistant):
    runtime = await async_setup_mesa(hass, "advisory")
    runtime.store.set(
        "input_boolean.guest_mode",
        SemanticProfile.from_dict(
            "input_boolean.guest_mode",
            {
                "semantic_profile": {
                    "operational_boundaries": {"triggers_automations": "none"},
                }
            },
        ),
    )
    # Author an automation that references the entity in a trigger.
    automations = hass.config.path("automations.yaml")
    with open(automations, "w", encoding="utf-8") as fh:
        fh.write(
            "- id: a1\n"
            "  trigger:\n"
            "    - platform: state\n"
            "      entity_id: input_boolean.guest_mode\n"
            "  action: []\n"
        )

    await async_refresh_trigger_issues(hass, runtime)
    assert any(
        issue.entity_id == "input_boolean.guest_mode" for issue in runtime.trigger_issues
    )


@pytest.mark.asyncio
async def test_refresh_orphans_covers_entity_area_integration(hass: HomeAssistant):
    """refresh_orphans flags entity, area, and integration profiles whose target
    is gone, and leaves live ones alone (mesa-core's find_orphans covers only the
    entity level, so ATM checks area + integration host-side)."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er

    from custom_components.atm.mesa import refresh_orphans

    # A live entity from the "hue" integration in a live area.
    area = ar.async_get(hass).async_create("Real Area")
    entry = er.async_get(hass).async_get_or_create(
        "light", "hue", "uid_live", suggested_object_id="live"
    )

    runtime = await async_setup_mesa(hass, "advisory")

    def _profile(key: str) -> SemanticProfile:
        return SemanticProfile.from_dict(
            key, {"semantic_profile": {"semantic_tags": ["lighting.ambient"]}}
        )

    # Live targets must NOT be flagged.
    runtime.store.set(entry.entity_id, _profile(entry.entity_id))
    runtime.store.set_area_profile(area.id, _profile(area.id))
    runtime.store.set_integration_profile("hue", _profile("hue"))
    # Dead targets must be flagged.
    runtime.store.set("light.ghost", _profile("light.ghost"))
    runtime.store.set_area_profile("ghost_area", _profile("ghost_area"))
    runtime.store.set_integration_profile("removed_integration", _profile("removed_integration"))

    refresh_orphans(hass, runtime)

    assert runtime.orphans == ["light.ghost"]
    assert runtime.orphan_areas == ["ghost_area"]
    assert runtime.orphan_integrations == ["removed_integration"]


def test_build_caller_context_maps_token():
    ctx = build_caller_context(_token("automation_builder"), session_id="sess-9")
    assert ctx.caller_id == "tok-1"
    assert ctx.display_name == "my_token"
    assert ctx.roles == ["automation_builder"]
    assert ctx.is_authenticated is True
    assert ctx.session_id == "sess-9"
