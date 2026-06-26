"""Tests for scene CRUD MCP tools (create/edit/delete_scene, list_scenes).

Scene writes mirror the automation/script YAML pattern: Confirm-gated on
cap_scene_write, with every referenced member entity required to be WRITE-
accessible to the token. list_scenes is a cap_registry_read read.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atm.mcp_view import _EXECUTOR_REGISTRY, _call_tool, _read_scenes_yaml
from custom_components.atm.token_store import PermissionNode, PermissionTree, TokenRecord


def _token(permissions: PermissionTree | None = None, **caps) -> TokenRecord:
    tree = permissions or PermissionTree(domains={
        "light": PermissionNode(state="GREEN"),
        "scene": PermissionNode(state="GREEN"),
    })
    base = {"cap_scene_write": "allow", "cap_registry_read": "allow"}
    base.update(caps)
    return TokenRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x",
        created_at=utcnow(), created_by="u", permissions=tree, **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


@pytest.fixture
def scene_env(hass: HomeAssistant):
    entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    entry.add_to_hass(hass)
    e = er.async_get(hass).async_get_or_create(
        "light", "test_integration", "uid_k", config_entry=entry, suggested_object_id="kitchen")
    hass.states.async_set(e.entity_id, "on", {})
    hass.states.async_set("sensor.secret", "1", {})  # ungranted
    # Dummy scene.reload so _execute's reload call succeeds.
    hass.services.async_register("scene", "reload", lambda call: None)
    return hass


async def _call(name, args, token, hass):
    return await _call_tool(name, args, token, hass, MagicMock())


class TestExecutorRegistration:
    def test_scene_executors_registered(self):
        for name in ("create_scene", "edit_scene", "delete_scene"):
            assert name in _EXECUTOR_REGISTRY


class TestCreateScene:
    async def test_deny_without_cap(self, hass, scene_env):
        content, outcome, _ = await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}},
            _token(cap_scene_write="deny"), hass)
        assert outcome == "denied"

    async def test_create_writes_yaml(self, hass, scene_env):
        content, outcome, _ = await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _token(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["name"] == "Movie"
        assert body["id"].startswith("atm_")
        stored = _read_scenes_yaml(hass.config.path("scenes.yaml"))
        assert any(s["id"] == body["id"] for s in stored)

    async def test_rejects_unwritable_member(self, hass, scene_env):
        content, outcome, _ = await _call(
            "create_scene", {"config": {"name": "Bad", "entities": {"sensor.secret": "1"}}}, _token(), hass)
        assert outcome == "denied"
        assert "sensor.secret" in content["content"][0]["text"]

    async def test_invalid_config(self, hass, scene_env):
        content, outcome, _ = await _call("create_scene", {"config": {"name": "NoEntities"}}, _token(), hass)
        assert outcome == "invalid_request"


class TestEditScene:
    async def test_edit_by_id(self, hass, scene_env):
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _token(), hass))[0])
        sid = created["id"]
        content, outcome, _ = await _call(
            "edit_scene", {"scene_id": sid, "config": {"name": "Movie2", "entities": {"light.kitchen": "off"}}}, _token(), hass)
        assert outcome == "allowed"
        stored = {s["id"]: s for s in _read_scenes_yaml(hass.config.path("scenes.yaml"))}
        assert stored[sid]["name"] == "Movie2"

    async def test_edit_unknown(self, hass, scene_env):
        content, outcome, _ = await _call(
            "edit_scene", {"scene_id": "nope", "config": {"name": "x", "entities": {"light.kitchen": "on"}}}, _token(), hass)
        assert outcome == "denied"

    async def test_missing_and_out_of_scope_same_reason(self, hass, scene_env):
        # No-oracle: an out-of-scope scene gives the same generic reason as a missing
        # one (the write-scope hint), and never leaks the existing member name. The
        # attacker can write sensor.secret (so the new-member check passes) but not
        # the existing member light.kitchen.
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _token(), hass))[0])
        sid = created["id"]
        attacker = _token(PermissionTree(domains={"sensor": PermissionNode(state="GREEN")}))
        out_of_scope = (await _call(
            "edit_scene", {"scene_id": sid, "config": {"name": "x", "entities": {"sensor.secret": "1"}}}, attacker, hass))[0]["content"][0]["text"]
        missing = (await _call(
            "edit_scene", {"scene_id": "nope", "config": {"name": "x", "entities": {"sensor.secret": "1"}}}, attacker, hass))[0]["content"][0]["text"]
        suffix = ", or it controls entities outside your write scope."
        assert out_of_scope.endswith(suffix)
        assert missing.endswith(suffix)
        assert "light.kitchen" not in out_of_scope  # existing member not leaked

    async def test_edit_out_of_scope_existing_members_denied(self, hass, scene_env):
        # Owner creates a scene over light.kitchen; an attacker who can write a new
        # member (sensor.secret) but NOT the existing member light.kitchen cannot
        # replace it: the existing-member ownership check denies.
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _token(), hass))[0])
        sid = created["id"]
        attacker = _token(PermissionTree(domains={"sensor": PermissionNode(state="GREEN")}))
        content, outcome, _ = await _call(
            "edit_scene", {"scene_id": sid, "config": {"name": "Hijacked", "entities": {"sensor.secret": "1"}}}, attacker, hass)
        assert outcome == "denied"
        # Unchanged on disk.
        stored = {s["id"]: s for s in _read_scenes_yaml(hass.config.path("scenes.yaml"))}
        assert stored[sid]["name"] == "Movie"


class TestDeleteScene:
    async def test_delete(self, hass, scene_env):
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _token(), hass))[0])
        sid = created["id"]
        content, outcome, _ = await _call("delete_scene", {"scene_id": sid}, _token(), hass)
        assert outcome == "allowed"
        assert all(s["id"] != sid for s in _read_scenes_yaml(hass.config.path("scenes.yaml")))

    async def test_delete_unknown(self, hass, scene_env):
        _, outcome, _ = await _call("delete_scene", {"scene_id": "nope"}, _token(), hass)
        assert outcome == "denied"

    async def test_delete_out_of_scope_existing_members_denied(self, hass, scene_env):
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _token(), hass))[0])
        sid = created["id"]
        attacker = _token(PermissionTree(domains={"scene": PermissionNode(state="GREEN")}))
        _, outcome, _ = await _call("delete_scene", {"scene_id": sid}, attacker, hass)
        assert outcome == "denied"
        # Still present on disk.
        assert any(s["id"] == sid for s in _read_scenes_yaml(hass.config.path("scenes.yaml")))

    async def test_delete_removes_registry_orphan(self, hass, scene_env):
        # Finding #9: delete must purge the scene's entity-registry entry so it
        # does not linger as an "unavailable" orphan.
        created = _json((await _call(
            "create_scene", {"config": {"name": "Movie", "entities": {"light.kitchen": "on"}}}, _token(), hass))[0])
        sid = created["id"]
        reg = er.async_get(hass)
        scene_entry = reg.async_get_or_create("scene", "homeassistant", sid, suggested_object_id="movie")
        assert reg.async_get(scene_entry.entity_id) is not None
        _, outcome, _ = await _call("delete_scene", {"scene_id": sid}, _token(), hass)
        assert outcome == "allowed"
        assert reg.async_get(scene_entry.entity_id) is None  # orphan purged


class TestListScenes:
    async def test_lists_accessible(self, hass, scene_env):
        hass.states.async_set("scene.movie", "scening", {"friendly_name": "Movie", "id": "atm_abc"})
        content, outcome, _ = await _call("list_scenes", {}, _token(), hass)
        assert outcome == "allowed"
        body = _json(content)
        sc = next(s for s in body["scenes"] if s["entity_id"] == "scene.movie")
        assert sc["scene_id"] == "atm_abc"

    async def test_deny_without_cap(self, hass, scene_env):
        _, outcome, _ = await _call("list_scenes", {}, _token(cap_registry_read="deny"), hass)
        assert outcome == "denied"
