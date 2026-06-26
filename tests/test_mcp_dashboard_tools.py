"""Tests for the dashboard config tools (get/set_dashboard_config) + versioning.

set_dashboard_config writes a storage-mode dashboard's view/card layout (Confirm-
gated on cap_lovelace_write) and is versioned; get_dashboard_config reads it back
with out-of-scope entity ids redacted. Both go through the ws_dispatch lovelace
helpers, which use the lovelace integration's LovelaceConfig objects directly.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atm.data import ATMData
from custom_components.atm.mcp_view import _call_tool, restore_version
from custom_components.atm.token_store import PermissionNode, PermissionTree, TokenRecord
from custom_components.atm.version_store import VersionStore
from custom_components.atm.ws_dispatch import async_get_lovelace_config


def _token(tree: PermissionTree | None = None, **caps) -> TokenRecord:
    base = {"cap_lovelace_write": "allow"}
    base.update(caps)
    return TokenRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x", created_at=utcnow(),
        created_by="u", permissions=tree or PermissionTree(domains={}), **base,
    )


def _data() -> tuple[ATMData, VersionStore]:
    versions = VersionStore()
    data = ATMData(store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=versions)
    return data, versions


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, token, hass, data=None):
    return await _call_tool(name, args, token, hass, data if data is not None else MagicMock())


@pytest.fixture
async def lovelace_env(hass: HomeAssistant):
    assert await async_setup_component(hass, "lovelace", {"lovelace": {}})
    entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    entry.add_to_hass(hass)
    e = er.async_get(hass).async_get_or_create(
        "light", "test_integration", "uid_k", config_entry=entry, suggested_object_id="kitchen")
    hass.states.async_set(e.entity_id, "on", {})
    hass.states.async_set("sensor.secret", "1", {})  # out of scope
    return hass, e.entity_id


class TestGetDashboardConfig:
    async def test_deny_without_cap(self, hass, lovelace_env):
        h, _light = lovelace_env
        _, outcome, _ = await _call("get_dashboard_config", {}, _token(cap_lovelace_write="deny"), h)
        assert outcome == "denied"

    async def test_autogen_returns_not_found(self, hass, lovelace_env):
        h, _light = lovelace_env
        # The default dashboard has no stored config until something is saved.
        _, outcome, _ = await _call("get_dashboard_config", {}, _token(), h)
        assert outcome == "not_found"

    async def test_reads_and_redacts_out_of_scope_entities(self, hass, lovelace_env):
        h, light = lovelace_env
        token = _token(tree=PermissionTree(domains={"light": PermissionNode(state="GREEN")}))
        data, _v = _data()
        cfg = {"views": [{"cards": [{"type": "entities", "entities": [light, "sensor.secret"]}]}]}
        await _call("set_dashboard_config", {"config": cfg}, token, h, data)

        content, outcome, _ = await _call("get_dashboard_config", {}, token, h)
        assert outcome == "allowed"
        ents = _json(content)["config"]["views"][0]["cards"][0]["entities"]
        assert light in ents  # in scope, kept
        assert "sensor.secret" not in ents and "<redacted>" in ents  # out of scope, redacted


class TestSetDashboardConfig:
    async def test_deny_without_cap(self, hass, lovelace_env):
        h, _light = lovelace_env
        _, outcome, _ = await _call(
            "set_dashboard_config", {"config": {"views": []}}, _token(cap_lovelace_write="deny"), h)
        assert outcome == "denied"

    async def test_writes_and_records_version(self, hass, lovelace_env):
        h, _light = lovelace_env
        token = _token()
        data, versions = _data()
        cfg = {"views": [{"title": "A"}]}
        _c, outcome, _ = await _call("set_dashboard_config", {"config": cfg}, token, h, data)
        assert outcome == "allowed"
        assert await async_get_lovelace_config(h, None) == cfg  # persisted

        hist = versions.list_for("dashboard", "lovelace")  # default dashboard keyed "lovelace"
        assert len(hist) == 1
        assert hist[0].action == "create" and hist[0].before is None and hist[0].after == cfg

    async def test_second_set_is_an_edit(self, hass, lovelace_env):
        h, _light = lovelace_env
        token = _token()
        data, versions = _data()
        await _call("set_dashboard_config", {"config": {"views": [{"title": "A"}]}}, token, h, data)
        await _call("set_dashboard_config", {"config": {"views": [{"title": "B"}]}}, token, h, data)
        hist = versions.list_for("dashboard", "lovelace")
        assert [v.action for v in hist] == ["edit", "create"]
        assert hist[0].before == {"views": [{"title": "A"}]} and hist[0].after == {"views": [{"title": "B"}]}


class TestDashboardRestore:
    async def test_restore_reapplies_as_rollback(self, hass, lovelace_env):
        h, _light = lovelace_env
        token = _token()
        data, versions = _data()
        await _call("set_dashboard_config", {"config": {"views": [{"title": "A"}]}}, token, h, data)
        await _call("set_dashboard_config", {"config": {"views": [{"title": "B"}]}}, token, h, data)

        create_ver = versions.list_for("dashboard", "lovelace")[-1]  # the create (A)
        _r, outcome, _res = await restore_version(create_ver, "admin-1", h, data)
        assert outcome == "allowed"
        assert await async_get_lovelace_config(h, None) == {"views": [{"title": "A"}]}  # config restored

        latest = versions.list_for("dashboard", "lovelace")[0]
        assert latest.action == "rollback" and latest.approved_by_user_id == "admin-1"
