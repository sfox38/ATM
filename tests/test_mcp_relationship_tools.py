"""Tests for get_relationships and describe_entity (reverse/forward references).

Automation references go through mesa-core's public entities_by_role; script and
scene references are extracted by ATM. Forward references are scoped to entities
the token can access; reverse references describe the accessible entity itself.
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import MagicMock

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atm.mcp_view import _call_tool
from custom_components.atm.token_store import PermissionNode, PermissionTree, TokenRecord


def _token(cap_search: str = "allow", **caps) -> TokenRecord:
    tree = PermissionTree(domains={
        "light": PermissionNode(state="GREEN"),
        "automation": PermissionNode(state="GREEN"),
    })
    return TokenRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x",
        created_at=utcnow(), created_by="u",
        cap_search=cap_search, permissions=tree, **caps,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, token, hass):
    data = MagicMock()
    data.mesa = None  # deterministic: describe_entity skips the MESA block
    return await _call_tool(name, args, token, hass, data)


def _write(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)


@pytest.fixture
def rel_env(hass: HomeAssistant):
    entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)

    for slug, uid in (("kitchen", "uid_k"), ("bedroom", "uid_b")):
        e = ent_reg.async_get_or_create("light", "test_integration", uid, config_entry=entry, suggested_object_id=slug)
        hass.states.async_set(e.entity_id, "on", {})
    auto = ent_reg.async_get_or_create(
        "automation", "test_integration", "auto1", config_entry=entry, suggested_object_id="morning")
    hass.states.async_set(auto.entity_id, "on", {})
    # sensor.secret stays ungranted/denied.
    hass.states.async_set("sensor.secret", "1", {})

    _write(os.path.join(hass.config.config_dir, "automations.yaml"), [
        {
            "id": "auto1", "alias": "Morning",
            "trigger": [{"platform": "state", "entity_id": "light.kitchen"}],
            "action": [{"service": "light.turn_on", "target": {"entity_id": "light.bedroom"}}],
        },
    ])
    _write(hass.config.path("scripts.yaml"), {
        "greet": {"alias": "Greet", "sequence": [
            {"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]},
    })
    _write(hass.config.path("scenes.yaml"), [
        {"id": "s1", "name": "Evening", "entities": {"light.kitchen": "on"}},
    ])
    return {"automation": auto.entity_id}


class TestGetRelationships:
    async def test_deny_without_cap(self, hass, rel_env):
        _, outcome, _ = await _call("get_relationships", {"entity_id": "light.kitchen"}, _token(cap_search="deny"), hass)
        assert outcome == "denied"

    async def test_reverse_references(self, hass, rel_env):
        content, outcome, _ = await _call("get_relationships", {"entity_id": "light.kitchen"}, _token(), hass)
        assert outcome == "allowed"
        body = _json(content)
        by_kind = {r["kind"]: r for r in body["referenced_by"]}
        assert by_kind["automation"]["name"] == "Morning"
        assert by_kind["automation"]["roles"] == ["trigger"]
        assert by_kind["script"]["id"] == "greet"
        assert by_kind["scene"]["name"] == "Evening"

    async def test_forward_references_scoped(self, hass, rel_env):
        content, _, _ = await _call("get_relationships", {"entity_id": rel_env["automation"]}, _token(), hass)
        body = _json(content)
        # The automation references both lights; both are accessible.
        assert set(body["references"]) == {"light.bedroom", "light.kitchen"}

    async def test_forward_excludes_out_of_scope(self, hass, rel_env):
        # Add an action targeting sensor.secret (denied); it must not appear.
        _write(os.path.join(hass.config.config_dir, "automations.yaml"), [
            {
                "id": "auto1", "alias": "Morning",
                "trigger": [{"platform": "state", "entity_id": "light.kitchen"}],
                "action": [{"service": "homeassistant.update_entity", "target": {"entity_id": "sensor.secret"}}],
            },
        ])
        content, _, _ = await _call("get_relationships", {"entity_id": rel_env["automation"]}, _token(), hass)
        assert "sensor.secret" not in _json(content)["references"]

    async def test_inaccessible_not_found(self, hass, rel_env):
        _, outcome, _ = await _call("get_relationships", {"entity_id": "sensor.secret"}, _token(), hass)
        assert outcome == "not_found"


class TestDescribeEntity:
    async def test_deny_without_cap(self, hass, rel_env):
        _, outcome, _ = await _call("describe_entity", {"entity_id": "light.kitchen"}, _token(cap_search="deny"), hass)
        assert outcome == "denied"

    async def test_describe(self, hass, rel_env):
        content, outcome, _ = await _call("describe_entity", {"entity_id": "light.kitchen"}, _token(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["domain"] == "light"
        assert body["state"] == "on"
        assert body["writable"] is True
        kinds = {r["kind"] for r in body["referenced_by"]}
        assert kinds == {"automation", "script", "scene"}
        assert "mesa_control_mode" not in body  # MagicMock data => skipped via mode check below

    async def test_inaccessible_not_found(self, hass, rel_env):
        _, outcome, _ = await _call("describe_entity", {"entity_id": "sensor.secret"}, _token(), hass)
        assert outcome == "not_found"
