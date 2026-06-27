"""Tests for policy_engine.py."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atm.policy_engine import (
    EntityCreationNotPermitted,
    Permission,
    filter_entities_for_token,
    filter_service_response,
    parse_relative_time,
    resolve,
    resolve_service_targets,
    scrub_sensitive_attributes,
)
from custom_components.atm.token_store import PermissionNode, PermissionTree, TokenRecord


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_token(
    pass_through: bool = False,
    domains: dict | None = None,
    devices: dict | None = None,
    entities: dict | None = None,
    cap_restart: str = "deny",
) -> TokenRecord:
    return TokenRecord(
        id="test-id",
        name="test",
        token_hash="x",
        created_at=utcnow(),
        created_by="admin",
        pass_through=pass_through,
        cap_restart=cap_restart,
        permissions=PermissionTree(
            domains={k: PermissionNode(state=v) for k, v in (domains or {}).items()},
            devices={k: PermissionNode(state=v) for k, v in (devices or {}).items()},
            entities={k: PermissionNode(state=v) for k, v in (entities or {}).items()},
        ),
    )


def _node(state: str, hint: str | None = None) -> PermissionNode:
    return PermissionNode(state=state, hint=hint)


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="test_integration", entry_id="test_entry")
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def entity_reg(hass: HomeAssistant) -> er.EntityRegistry:
    return er.async_get(hass)


@pytest.fixture
def device_reg(hass: HomeAssistant) -> dr.DeviceRegistry:
    return dr.async_get(hass)


@pytest.fixture
def basic_env(hass: HomeAssistant, config_entry: MockConfigEntry):
    """Set up a minimal known entity environment in the real hass instance."""
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})
    hass.states.async_set("sensor.temp", "22", {"unit_of_measurement": "C"})
    hass.states.async_set("lock.front_door", "locked", {})
    hass.states.async_set("atm.internal_sensor", "on", {})
    return hass


@pytest.fixture
def registered_env(hass: HomeAssistant, config_entry: MockConfigEntry, entity_reg):
    """Set up entities that are registered in both the state machine and the entity registry."""
    lights = {}
    for slug, unique in [("kitchen", "uid_light_kitchen"), ("hallway", "uid_light_hallway")]:
        entry = entity_reg.async_get_or_create(
            "light", "test_integration", unique,
            config_entry=config_entry,
            suggested_object_id=slug,
        )
        hass.states.async_set(entry.entity_id, "on", {})
        lights[slug] = entry

    sensors = {}
    for slug, unique in [("temp", "uid_sensor_temp")]:
        entry = entity_reg.async_get_or_create(
            "sensor", "test_integration", unique,
            config_entry=config_entry,
            suggested_object_id=slug,
        )
        hass.states.async_set(entry.entity_id, "22", {})
        sensors[slug] = entry

    return {"lights": lights, "sensors": sensors}


@pytest.fixture
def device_env(hass: HomeAssistant, config_entry: MockConfigEntry, entity_reg, device_reg):
    """Set up device + area + entity environment for service target tests."""
    area_reg = __import__(
        "homeassistant.helpers.area_registry", fromlist=["async_get"]
    ).async_get(hass)
    area = area_reg.async_create("Living Room")

    device = device_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("test_integration", "device_living")},
        name="Living Room Hub",
    )
    device_reg.async_update_device(device.id, area_id=area.id)
    device = device_reg.async_get(device.id)

    # light.living_main - in device, domain light
    light_main = entity_reg.async_get_or_create(
        "light", "test_integration", "living_main",
        config_entry=config_entry,
        device_id=device.id,
    )
    hass.states.async_set(light_main.entity_id, "on", {})

    # light.living_accent - in device, domain light
    light_accent = entity_reg.async_get_or_create(
        "light", "test_integration", "living_accent",
        config_entry=config_entry,
        device_id=device.id,
    )
    hass.states.async_set(light_accent.entity_id, "off", {})

    # sensor.living_temp - in device, domain sensor (different domain - diagnostic)
    sensor = entity_reg.async_get_or_create(
        "sensor", "test_integration", "living_temp",
        config_entry=config_entry,
        device_id=device.id,
    )
    hass.states.async_set(sensor.entity_id, "21", {})

    return {
        "area": area,
        "device": device,
        "light_main": light_main,
        "light_accent": light_accent,
        "sensor": sensor,
    }


# ---------------------------------------------------------------------------
# resolve() - core two-pass algorithm
# ---------------------------------------------------------------------------


class TestResolveBasicInheritance:
    async def test_grey_entity_under_yellow_domain_is_read(self, hass, basic_env):
        token = _make_token(domains={"light": "YELLOW"})
        assert resolve("light.kitchen", token, hass) == Permission.READ

    async def test_grey_entity_under_green_domain_is_write(self, hass, basic_env):
        token = _make_token(domains={"light": "GREEN"})
        assert resolve("light.kitchen", token, hass) == Permission.WRITE

    async def test_grey_entity_under_grey_domain_is_no_access(self, hass, basic_env):
        token = _make_token()
        assert resolve("light.kitchen", token, hass) == Permission.NO_ACCESS

    async def test_green_entity_under_yellow_domain_is_write(self, hass, basic_env):
        token = _make_token(
            domains={"light": "YELLOW"},
            entities={"light.kitchen": "GREEN"},
        )
        assert resolve("light.kitchen", token, hass) == Permission.WRITE

    async def test_yellow_entity_under_green_domain_is_read(self, hass, basic_env):
        token = _make_token(
            domains={"light": "GREEN"},
            entities={"light.kitchen": "YELLOW"},
        )
        assert resolve("light.kitchen", token, hass) == Permission.READ

    async def test_red_entity_under_green_domain_is_deny(self, hass, basic_env):
        """Pass 1 must catch RED even when a more specific GREEN exists above it."""
        token = _make_token(
            domains={"light": "GREEN"},
            entities={"light.kitchen": "RED"},
        )
        assert resolve("light.kitchen", token, hass) == Permission.DENY

    async def test_green_entity_under_red_domain_is_deny(self, hass, basic_env):
        """RED on domain wins over GREEN on entity - two-pass is critical here."""
        token = _make_token(
            domains={"light": "RED"},
            entities={"light.kitchen": "GREEN"},
        )
        assert resolve("light.kitchen", token, hass) == Permission.DENY

    async def test_red_domain_denies_grey_entity(self, hass, basic_env):
        token = _make_token(domains={"light": "RED"})
        assert resolve("light.kitchen", token, hass) == Permission.DENY

    async def test_all_grey_chain_is_no_access(self, hass, basic_env):
        token = _make_token()
        assert resolve("sensor.temp", token, hass) == Permission.NO_ACCESS

    async def test_entity_level_overrides_domain_read_with_write(self, hass, basic_env):
        """Escalation: child GREEN beats parent YELLOW."""
        token = _make_token(
            domains={"sensor": "YELLOW"},
            entities={"sensor.temp": "GREEN"},
        )
        assert resolve("sensor.temp", token, hass) == Permission.WRITE

    async def test_entity_level_restricts_domain_write_to_read(self, hass, basic_env):
        """Restriction: child YELLOW beats parent GREEN."""
        token = _make_token(
            domains={"sensor": "GREEN"},
            entities={"sensor.temp": "YELLOW"},
        )
        assert resolve("sensor.temp", token, hass) == Permission.READ


class TestResolveDeviceLevel:
    async def test_grey_entity_under_green_device_is_write(self, hass, device_env):
        env = device_env
        device_id = env["device"].id
        token = _make_token(devices={device_id: "GREEN"})
        assert resolve(env["light_main"].entity_id, token, hass) == Permission.WRITE

    async def test_grey_entity_under_yellow_device_is_read(self, hass, device_env):
        env = device_env
        token = _make_token(devices={env["device"].id: "YELLOW"})
        assert resolve(env["light_main"].entity_id, token, hass) == Permission.READ

    async def test_red_device_denies_green_entity(self, hass, device_env):
        env = device_env
        device_id = env["device"].id
        entity_id = env["light_main"].entity_id
        token = _make_token(
            devices={device_id: "RED"},
            entities={entity_id: "GREEN"},
        )
        assert resolve(entity_id, token, hass) == Permission.DENY

    async def test_entity_overrides_device_green_with_red(self, hass, device_env):
        env = device_env
        device_id = env["device"].id
        entity_id = env["light_main"].entity_id
        token = _make_token(
            devices={device_id: "GREEN"},
            entities={entity_id: "RED"},
        )
        assert resolve(entity_id, token, hass) == Permission.DENY

    async def test_device_overrides_domain(self, hass, device_env):
        """Device GREEN + domain YELLOW -> WRITE (device wins as more specific)."""
        env = device_env
        token = _make_token(
            domains={"light": "YELLOW"},
            devices={env["device"].id: "GREEN"},
        )
        assert resolve(env["light_main"].entity_id, token, hass) == Permission.WRITE

    async def test_domain_red_beats_device_green(self, hass, device_env):
        """Domain RED + device GREEN -> DENY (Pass 1 catches domain RED)."""
        env = device_env
        token = _make_token(
            domains={"light": "RED"},
            devices={env["device"].id: "GREEN"},
        )
        assert resolve(env["light_main"].entity_id, token, hass) == Permission.DENY


class TestResolveGhostAndBlocklist:
    async def test_ghost_entity_returns_not_found(self, hass):
        token = _make_token(domains={"light": "GREEN"})
        result = resolve("light.ghost_entity", token, hass)
        assert result == Permission.NOT_FOUND

    async def test_ghost_entity_with_no_grants_returns_not_found(self, hass):
        token = _make_token()
        assert resolve("sensor.does_not_exist", token, hass) == Permission.NOT_FOUND

    async def test_atm_domain_is_blocked_regardless_of_grant(self, hass, basic_env):
        token = _make_token(domains={"atm": "GREEN"})
        assert resolve("atm.internal_sensor", token, hass) == Permission.NO_ACCESS

    async def test_atm_domain_blocked_with_no_grant(self, hass, basic_env):
        token = _make_token()
        assert resolve("atm.internal_sensor", token, hass) == Permission.NO_ACCESS

    async def test_atm_domain_blocked_even_in_pass_through(self, hass, basic_env):
        token = _make_token(pass_through=True)
        assert resolve("atm.internal_sensor", token, hass) == Permission.NO_ACCESS


class TestResolvePassThrough:
    async def test_pass_through_returns_write_for_all_entities(self, hass, basic_env):
        token = _make_token(pass_through=True)
        assert resolve("light.kitchen", token, hass) == Permission.WRITE
        assert resolve("sensor.temp", token, hass) == Permission.WRITE
        assert resolve("lock.front_door", token, hass) == Permission.WRITE

    async def test_pass_through_still_blocks_ghosts(self, hass):
        token = _make_token(pass_through=True)
        assert resolve("light.ghost", token, hass) == Permission.NOT_FOUND

    async def test_pass_through_ignores_permission_tree(self, hass, basic_env):
        """Even RED grants are ignored in pass-through mode."""
        token = _make_token(
            pass_through=True,
            domains={"light": "RED"},
            entities={"sensor.temp": "RED"},
        )
        assert resolve("light.kitchen", token, hass) == Permission.WRITE
        assert resolve("sensor.temp", token, hass) == Permission.WRITE


class TestResolveAliasCanonical:
    async def test_resolves_to_canonical_entity_id(self, hass, config_entry, entity_reg):
        """Resolution should use the canonical entity_id from the registry."""
        entry = entity_reg.async_get_or_create(
            "light", "test_platform", "unique_canonical",
            config_entry=config_entry,
        )
        canonical_id = entry.entity_id
        hass.states.async_set(canonical_id, "on", {})

        token = _make_token(entities={canonical_id: "GREEN"})
        assert resolve(canonical_id, token, hass) == Permission.WRITE

    async def test_permission_on_canonical_not_presented_id(self, hass, config_entry, entity_reg):
        """Permission is stored on canonical entity_id; it applies regardless of lookup form."""
        entry = entity_reg.async_get_or_create(
            "light", "test_platform", "unique_alias_test",
            config_entry=config_entry,
        )
        canonical_id = entry.entity_id
        hass.states.async_set(canonical_id, "on", {})

        token_with_canonical = _make_token(entities={canonical_id: "YELLOW"})
        assert resolve(canonical_id, token_with_canonical, hass) == Permission.READ


# ---------------------------------------------------------------------------
# scrub_sensitive_attributes()
# ---------------------------------------------------------------------------


class TestScrubSensitiveAttributes:
    def _state(self, entity_id: str, attrs: dict) -> State:
        return State(entity_id, "on", attrs)

    def test_removes_entity_picture(self):
        state = self._state("camera.front", {"entity_picture": "/api/camera/front", "name": "Front"})
        result = scrub_sensitive_attributes(state)
        assert "entity_picture" not in result["attributes"]
        assert result["attributes"]["name"] == "Front"

    def test_removes_stream_url(self):
        state = self._state("camera.front", {"stream_url": "rtsp://...", "name": "Cam"})
        result = scrub_sensitive_attributes(state)
        assert "stream_url" not in result["attributes"]

    def test_removes_access_token(self):
        state = self._state("camera.front", {"access_token": "secret123"})
        result = scrub_sensitive_attributes(state)
        assert "access_token" not in result["attributes"]

    def test_removes_still_image_url(self):
        state = self._state("camera.front", {"still_image_url": "http://..."})
        result = scrub_sensitive_attributes(state)
        assert "still_image_url" not in result["attributes"]

    def test_preserves_all_safe_attributes(self):
        attrs = {"friendly_name": "Test", "brightness": 255, "color_temp": 300}
        state = self._state("light.test", attrs)
        result = scrub_sensitive_attributes(state)
        assert result["attributes"] == attrs

    def test_preserves_entity_id_and_state(self):
        state = self._state("light.test", {})
        result = scrub_sensitive_attributes(state)
        assert result["entity_id"] == "light.test"
        assert result["state"] == "on"

    def test_scrubs_all_sensitive_at_once(self):
        state = self._state("camera.x", {
            "entity_picture": "x",
            "stream_url": "x",
            "access_token": "x",
            "still_image_url": "x",
            "safe_attr": "keep",
        })
        result = scrub_sensitive_attributes(state)
        assert set(result["attributes"].keys()) == {"safe_attr"}

    def test_scrubs_sensitive_key_substrings(self):
        # Keys not in the fixed list but matching a sensitive substring are dropped.
        state = self._state("sensor.thirdparty", {
            "api_key": "x",
            "password": "x",
            "client_secret": "x",
            "auth_token": "x",
            "session_id": "x",
            "friendly_name": "keep",
            "battery": 90,
        })
        result = scrub_sensitive_attributes(state)
        assert set(result["attributes"].keys()) == {"friendly_name", "battery"}


# ---------------------------------------------------------------------------
# filter_entities_for_token()
# ---------------------------------------------------------------------------


class TestFilterEntitiesForToken:
    def _states(self, hass: HomeAssistant) -> list[State]:
        return hass.states.async_all()

    async def test_empty_grant_returns_empty(self, hass, basic_env):
        token = _make_token()
        result = filter_entities_for_token(self._states(hass), token, hass)
        assert result == []

    async def test_domain_read_filters_to_that_domain(self, hass, basic_env):
        token = _make_token(domains={"light": "YELLOW"})
        result = filter_entities_for_token(self._states(hass), token, hass)
        entity_ids = [r["entity_id"] for r in result]
        assert "light.kitchen" in entity_ids
        assert "sensor.temp" not in entity_ids

    async def test_domain_green_includes_entity(self, hass, basic_env):
        token = _make_token(domains={"sensor": "GREEN"})
        result = filter_entities_for_token(self._states(hass), token, hass)
        entity_ids = [r["entity_id"] for r in result]
        assert "sensor.temp" in entity_ids

    async def test_red_entity_excluded(self, hass, basic_env):
        token = _make_token(
            domains={"light": "GREEN"},
            entities={"light.kitchen": "RED"},
        )
        result = filter_entities_for_token(self._states(hass), token, hass)
        entity_ids = [r["entity_id"] for r in result]
        assert "light.kitchen" not in entity_ids

    async def test_atm_domain_always_excluded(self, hass, basic_env):
        token = _make_token(domains={"atm": "GREEN"})
        result = filter_entities_for_token(self._states(hass), token, hass)
        entity_ids = [r["entity_id"] for r in result]
        assert not any(eid.startswith("atm.") for eid in entity_ids)

    async def test_pass_through_returns_all_except_atm(self, hass, basic_env):
        token = _make_token(pass_through=True)
        result = filter_entities_for_token(self._states(hass), token, hass)
        entity_ids = [r["entity_id"] for r in result]
        assert "light.kitchen" in entity_ids
        assert "sensor.temp" in entity_ids
        assert "lock.front_door" in entity_ids
        assert not any(eid.startswith("atm.") for eid in entity_ids)

    async def test_pass_through_still_scrubs_sensitive_attributes(self, hass):
        hass.states.async_set("camera.test", "idle", {
            "stream_url": "rtsp://secret",
            "access_token": "abc123",
            "name": "Test Cam",
        })
        token = _make_token(pass_through=True)
        states = [hass.states.get("camera.test")]
        result = filter_entities_for_token(states, token, hass)
        assert len(result) == 1
        assert "stream_url" not in result[0]["attributes"]
        assert "access_token" not in result[0]["attributes"]
        assert result[0]["attributes"]["name"] == "Test Cam"

    async def test_results_are_dicts_not_state_objects(self, hass, basic_env):
        token = _make_token(domains={"light": "GREEN"})
        result = filter_entities_for_token(self._states(hass), token, hass)
        for item in result:
            assert isinstance(item, dict)


# ---------------------------------------------------------------------------
# resolve_service_targets()
# ---------------------------------------------------------------------------


class TestResolveServiceTargetsEntityId:
    async def test_single_entity_with_write(self, hass, registered_env):
        light_id = registered_env["lights"]["kitchen"].entity_id
        token = _make_token(domains={"light": "GREEN"})
        result, _ = resolve_service_targets(
            entity_id=light_id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert light_id in result

    async def test_single_entity_with_read_excluded(self, hass, registered_env):
        light_id = registered_env["lights"]["kitchen"].entity_id
        token = _make_token(domains={"light": "YELLOW"})
        result, _ = resolve_service_targets(
            entity_id=light_id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert result == []

    async def test_red_entity_excluded_from_results(self, hass, registered_env):
        light_id = registered_env["lights"]["kitchen"].entity_id
        token = _make_token(
            domains={"light": "GREEN"},
            entities={light_id: "RED"},
        )
        result, _ = resolve_service_targets(
            entity_id=light_id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert result == []

    async def test_nonexistent_entity_raises_entity_creation_not_permitted(self, hass):
        token = _make_token(domains={"light": "GREEN"})
        with pytest.raises(EntityCreationNotPermitted) as exc_info:
            resolve_service_targets(
                entity_id="light.does_not_exist",
                service_domain="light",
                token=token,
                hass=hass,
            )
        assert exc_info.value.entity_id == "light.does_not_exist"

    async def test_all_expands_to_domain_entities(self, hass, registered_env):
        kitchen_id = registered_env["lights"]["kitchen"].entity_id
        hallway_id = registered_env["lights"]["hallway"].entity_id
        sensor_id = registered_env["sensors"]["temp"].entity_id
        token = _make_token(domains={"light": "GREEN"})
        result, _ = resolve_service_targets(
            entity_id="all",
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert kitchen_id in result
        assert hallway_id in result
        assert sensor_id not in result

    async def test_all_filters_by_write(self, hass, registered_env):
        kitchen_id = registered_env["lights"]["kitchen"].entity_id
        hallway_id = registered_env["lights"]["hallway"].entity_id
        token = _make_token(
            domains={"light": "GREEN"},
            entities={hallway_id: "RED"},
        )
        result, _ = resolve_service_targets(
            entity_id="all",
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert kitchen_id in result
        assert hallway_id not in result

    async def test_result_is_list_of_entity_ids_not_device_or_area(self, hass, registered_env):
        light_id = registered_env["lights"]["kitchen"].entity_id
        token = _make_token(domains={"light": "GREEN"})
        result, _ = resolve_service_targets(
            entity_id=light_id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        for item in result:
            assert "." in item
            assert not item.startswith("device.")
            assert not item.startswith("area.")

    async def test_list_of_entity_ids(self, hass, registered_env):
        kitchen_id = registered_env["lights"]["kitchen"].entity_id
        hallway_id = registered_env["lights"]["hallway"].entity_id
        token = _make_token(domains={"light": "GREEN"})
        result, _ = resolve_service_targets(
            entity_id=[kitchen_id, hallway_id],
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert kitchen_id in result
        assert hallway_id in result

    async def test_empty_result_when_no_entities_have_write(self, hass, registered_env):
        light_id = registered_env["lights"]["kitchen"].entity_id
        token = _make_token()
        result, _ = resolve_service_targets(
            entity_id=light_id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert result == []


class TestResolveServiceTargetsDeviceId:
    async def test_device_id_expands_to_service_domain_entities_only(self, hass, device_env):
        """Only light.* entities for the device should be returned, not sensor.*."""
        env = device_env
        token = _make_token(devices={env["device"].id: "GREEN"})
        result, _ = resolve_service_targets(
            device_id=env["device"].id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        entity_ids = set(result)
        assert env["light_main"].entity_id in entity_ids
        assert env["light_accent"].entity_id in entity_ids
        assert env["sensor"].entity_id not in entity_ids

    async def test_device_id_red_entity_silently_skipped(self, hass, device_env):
        env = device_env
        token = _make_token(
            devices={env["device"].id: "GREEN"},
            entities={env["light_accent"].entity_id: "RED"},
        )
        result, _ = resolve_service_targets(
            device_id=env["device"].id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert env["light_main"].entity_id in result
        assert env["light_accent"].entity_id not in result

    async def test_device_id_result_never_contains_device_id(self, hass, device_env):
        env = device_env
        token = _make_token(devices={env["device"].id: "GREEN"})
        result, _ = resolve_service_targets(
            device_id=env["device"].id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert env["device"].id not in result

    async def test_device_id_empty_when_all_read(self, hass, device_env):
        env = device_env
        token = _make_token(devices={env["device"].id: "YELLOW"})
        result, _ = resolve_service_targets(
            device_id=env["device"].id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert result == []


class TestResolveServiceTargetsAreaId:
    async def test_area_id_expands_to_service_domain_entities(self, hass, device_env):
        env = device_env
        token = _make_token(domains={"light": "GREEN"})
        result, _ = resolve_service_targets(
            area_id=env["area"].id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        entity_ids = set(result)
        assert env["light_main"].entity_id in entity_ids
        assert env["light_accent"].entity_id in entity_ids
        assert env["sensor"].entity_id not in entity_ids

    async def test_area_id_red_entity_silently_skipped(self, hass, device_env):
        env = device_env
        token = _make_token(
            domains={"light": "GREEN"},
            entities={env["light_accent"].entity_id: "RED"},
        )
        result, _ = resolve_service_targets(
            area_id=env["area"].id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert env["light_main"].entity_id in result
        assert env["light_accent"].entity_id not in result

    async def test_area_id_result_never_contains_area_id(self, hass, device_env):
        env = device_env
        token = _make_token(domains={"light": "GREEN"})
        result, _ = resolve_service_targets(
            area_id=env["area"].id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert env["area"].id not in result

    async def test_area_empty_after_permission_filter_returns_empty(self, hass, device_env):
        env = device_env
        token = _make_token()
        result, _ = resolve_service_targets(
            area_id=env["area"].id,
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert result == []


class TestResolveServiceTargetsUntargeted:
    async def test_untargeted_call_expands_to_all_writable_domain_entities(self, hass, registered_env):
        kitchen_id = registered_env["lights"]["kitchen"].entity_id
        sensor_id = registered_env["sensors"]["temp"].entity_id
        token = _make_token(domains={"light": "GREEN"})
        result, _ = resolve_service_targets(
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert kitchen_id in result
        assert sensor_id not in result

    async def test_untargeted_excludes_read_only_entities(self, hass, registered_env):
        token = _make_token(domains={"light": "YELLOW"})
        result, _ = resolve_service_targets(
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert result == []

    async def test_untargeted_excludes_red_entities(self, hass, registered_env):
        kitchen_id = registered_env["lights"]["kitchen"].entity_id
        token = _make_token(
            domains={"light": "GREEN"},
            entities={kitchen_id: "RED"},
        )
        result, _ = resolve_service_targets(
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert kitchen_id not in result


class TestResolveServiceTargetsPassThrough:
    async def test_pass_through_returns_all_domain_entities_with_write(self, hass, registered_env):
        kitchen_id = registered_env["lights"]["kitchen"].entity_id
        token = _make_token(pass_through=True)
        result, _ = resolve_service_targets(
            entity_id="all",
            service_domain="light",
            token=token,
            hass=hass,
        )
        assert kitchen_id in result

    async def test_pass_through_entity_creation_still_blocked(self, hass):
        token = _make_token(pass_through=True)
        with pytest.raises(EntityCreationNotPermitted):
            resolve_service_targets(
                entity_id="light.ghost_entity",
                service_domain="light",
                token=token,
                hass=hass,
            )

    async def test_pass_through_still_blocks_atm_domain(self, hass, registered_env):
        token = _make_token(pass_through=True)
        result, _ = resolve_service_targets(
            entity_id="all",
            service_domain="atm",
            token=token,
            hass=hass,
        )
        assert result == []


# ---------------------------------------------------------------------------
# filter_service_response()
# ---------------------------------------------------------------------------


class TestFilterServiceResponse:
    async def test_accessible_entity_id_preserved(self, hass, basic_env):
        token = _make_token(domains={"light": "GREEN"})
        result = filter_service_response("light.kitchen", token, hass)
        assert result == "light.kitchen"

    async def test_inaccessible_entity_id_redacted(self, hass, basic_env):
        token = _make_token()
        result = filter_service_response("light.kitchen", token, hass)
        assert result == "<redacted>"

    async def test_ghost_entity_id_redacted(self, hass):
        token = _make_token(domains={"light": "GREEN"})
        result = filter_service_response("light.ghost", token, hass)
        assert result == "<redacted>"

    async def test_non_entity_string_preserved(self, hass, basic_env):
        token = _make_token()
        assert filter_service_response("some plain string", token, hass) == "some plain string"
        assert filter_service_response("not_an_entity", token, hass) == "not_an_entity"

    async def test_dict_with_entity_id_values_redacted(self, hass, basic_env):
        token = _make_token()
        response = {"entity_id": "light.kitchen", "message": "ok"}
        result = filter_service_response(response, token, hass)
        assert result["entity_id"] == "<redacted>"
        assert result["message"] == "ok"

    async def test_nested_dict_entity_ids_redacted(self, hass, basic_env):
        token = _make_token()
        response = {"data": {"entity": "light.kitchen", "value": 42}}
        result = filter_service_response(response, token, hass)
        assert result["data"]["entity"] == "<redacted>"
        assert result["data"]["value"] == 42

    async def test_list_with_entity_ids_redacted(self, hass, basic_env):
        token = _make_token(domains={"light": "GREEN"})
        hass.states.async_set("sensor.secret", "on", {})
        response = ["light.kitchen", "sensor.secret", "not_an_entity"]
        result = filter_service_response(response, token, hass)
        assert result[0] == "light.kitchen"
        assert result[1] == "<redacted>"
        assert result[2] == "not_an_entity"

    async def test_non_string_values_preserved(self, hass, basic_env):
        token = _make_token()
        assert filter_service_response(42, token, hass) == 42
        assert filter_service_response(3.14, token, hass) == 3.14
        assert filter_service_response(None, token, hass) is None
        assert filter_service_response(True, token, hass) is True

    async def test_sensitive_keyed_values_redacted(self, hass, basic_env):
        token = _make_token()
        response = {
            "access_token": "abc123",
            "url": "https://example.com",
            "nested": {"api_key": "k", "count": 3},
            "message": "ok",
        }
        result = filter_service_response(response, token, hass)
        assert result["access_token"] == "<redacted>"
        assert result["nested"]["api_key"] == "<redacted>"
        assert result["nested"]["count"] == 3
        assert result["message"] == "ok"


# ---------------------------------------------------------------------------
# parse_relative_time()
# ---------------------------------------------------------------------------


class TestParseRelativeTime:
    def test_hours(self):
        with patch("custom_components.atm.policy_engine.utcnow") as mock_now:
            mock_now.return_value = datetime(2026, 4, 10, 12, 0, 0)
            result = parse_relative_time("24h")
        assert result == datetime(2026, 4, 9, 12, 0, 0)

    def test_days(self):
        with patch("custom_components.atm.policy_engine.utcnow") as mock_now:
            mock_now.return_value = datetime(2026, 4, 10, 0, 0, 0)
            result = parse_relative_time("7d")
        assert result == datetime(2026, 4, 3, 0, 0, 0)

    def test_weeks(self):
        with patch("custom_components.atm.policy_engine.utcnow") as mock_now:
            mock_now.return_value = datetime(2026, 4, 10, 0, 0, 0)
            result = parse_relative_time("2w")
        assert result == datetime(2026, 3, 27, 0, 0, 0)

    def test_months(self):
        with patch("custom_components.atm.policy_engine.utcnow") as mock_now:
            mock_now.return_value = datetime(2026, 4, 10, 0, 0, 0)
            result = parse_relative_time("1m")
        assert result == datetime(2026, 3, 11, 0, 0, 0)

    def test_whitespace_stripped(self):
        result = parse_relative_time("  6h  ")
        assert isinstance(result, datetime)

    def test_zero_value(self):
        result = parse_relative_time("0h")
        assert isinstance(result, datetime)

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            parse_relative_time("invalid")

    def test_invalid_unit_raises(self):
        with pytest.raises(ValueError):
            parse_relative_time("5x")

    def test_no_number_raises(self):
        with pytest.raises(ValueError):
            parse_relative_time("h")

    def test_result_is_in_the_past(self):
        result = parse_relative_time("1h")
        assert result < utcnow()


# ---------------------------------------------------------------------------
# Permission enum
# ---------------------------------------------------------------------------


class TestPermissionEnum:
    def test_values_are_distinct(self):
        values = {p.value for p in Permission}
        assert len(values) == len(Permission)

    def test_not_found_distinct_from_no_access(self):
        assert Permission.NOT_FOUND != Permission.NO_ACCESS

    def test_deny_distinct_from_no_access(self):
        assert Permission.DENY != Permission.NO_ACCESS


class TestEffectiveHint:
    """get_effective_hint: per-token node hint wins, global entity_hints is the fallback."""

    def test_global_fallback_when_no_node_hint(self, hass):
        from custom_components.atm.policy_engine import get_effective_hint
        token = _make_token(entities={"light.x": "GREEN"})
        assert get_effective_hint(token, "light.x", hass, {"light.x": "global note"}) == "global note"

    def test_node_hint_wins_over_global(self, hass):
        from custom_components.atm.policy_engine import get_effective_hint
        token = _make_token()
        token.permissions.entities["light.x"] = _node("GREEN", "node note")
        assert get_effective_hint(token, "light.x", hass, {"light.x": "global note"}) == "node note"

    def test_none_when_unset(self, hass):
        from custom_components.atm.policy_engine import get_effective_hint
        token = _make_token(entities={"light.x": "GREEN"})
        assert get_effective_hint(token, "light.x", hass, {}) is None
        assert get_effective_hint(token, "light.x", hass) is None
