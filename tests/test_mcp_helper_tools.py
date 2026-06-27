"""Tests for helper CRUD MCP tools (create/edit/delete_helper, list_helpers).

Helper writes are Confirm-gated on cap_helper_write and execute via the
in-process WS command dispatcher (ws_dispatch). list_helpers is a
cap_registry_read read scoped to accessible helper entities.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.dt import utcnow

from custom_components.atm.data import ATMData
from custom_components.atm.mcp_view import _EXECUTOR_REGISTRY, _call_tool
from custom_components.atm.token_store import PermissionNode, PermissionTree, TokenRecord
from custom_components.atm.version_store import VersionStore


def _token(**caps) -> TokenRecord:
    tree = PermissionTree(domains={"input_boolean": PermissionNode(state="GREEN")})
    base = {"cap_helper_write": "allow", "cap_registry_read": "allow"}
    base.update(caps)
    return TokenRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x",
        created_at=utcnow(), created_by="u", permissions=tree, **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, token, hass):
    return await _call_tool(name, args, token, hass, MagicMock())


@pytest.fixture
async def helper_env(hass: HomeAssistant):
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})
    return hass


class TestExecutorRegistration:
    def test_helper_executors_registered(self):
        for name in ("create_helper", "edit_helper", "delete_helper"):
            assert name in _EXECUTOR_REGISTRY


class TestCreateHelper:
    async def test_deny_without_cap(self, hass, helper_env, hass_admin_user):
        _, outcome, _ = await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "X"}},
            _token(cap_helper_write="deny"), hass)
        assert outcome == "denied"

    async def test_create(self, hass, helper_env, hass_admin_user):
        content, outcome, _ = await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "Guest mode"}}, _token(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["helper"]["name"] == "Guest mode"
        assert "id" in body["helper"]
        await hass.async_block_till_done()
        assert any(s.entity_id.startswith("input_boolean.") for s in hass.states.async_all())

    async def test_bad_type(self, hass, helper_env, hass_admin_user):
        _, outcome, _ = await _call(
            "create_helper", {"helper_type": "light", "config": {"name": "X"}}, _token(), hass)
        assert outcome == "invalid_request"

    async def test_empty_config(self, hass, helper_env, hass_admin_user):
        _, outcome, _ = await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {}}, _token(), hass)
        assert outcome == "invalid_request"


class TestEditDeleteHelper:
    async def test_edit(self, hass, helper_env, hass_admin_user):
        created = _json((await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "A"}}, _token(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()  # let the helper entity register
        content, outcome, _ = await _call(
            "edit_helper", {"helper_type": "input_boolean", "helper_id": hid, "config": {"name": "B"}}, _token(), hass)
        assert outcome == "allowed"
        assert _json(content)["helper"]["name"] == "B"

    async def test_delete(self, hass, helper_env, hass_admin_user):
        created = _json((await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "Temp"}}, _token(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()  # let the helper entity register
        _, outcome, _ = await _call(
            "delete_helper", {"helper_type": "input_boolean", "helper_id": hid}, _token(), hass)
        assert outcome == "allowed"

    async def test_delete_unknown(self, hass, helper_env, hass_admin_user):
        _, outcome, _ = await _call(
            "delete_helper", {"helper_type": "input_boolean", "helper_id": "does_not_exist"}, _token(), hass)
        assert outcome == "not_found"

    async def test_edit_missing_not_found(self, hass, helper_env, hass_admin_user):
        # A non-existent helper is not_found regardless of scope (existence check).
        _, outcome, _ = await _call(
            "edit_helper", {"helper_type": "input_boolean", "helper_id": "does_not_exist", "config": {"name": "X"}},
            _token(), hass)
        assert outcome == "not_found"

    async def test_edit_cap_only_not_entity_scoped(self, hass, helper_env, hass_admin_user):
        # Helper authoring is cap-gated, not entity-scoped.
        created = _json((await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "Owned"}}, _token(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()
        unscoped = TokenRecord(
            id=str(uuid.uuid4()), name="a", token_hash="x", created_at=utcnow(),
            created_by="u", permissions=PermissionTree(domains={}),
            cap_helper_write="allow", cap_registry_read="allow",
        )
        content, outcome, _ = await _call(
            "edit_helper", {"helper_type": "input_boolean", "helper_id": hid, "config": {"name": "Renamed"}},
            unscoped, hass)
        assert outcome == "allowed"
        assert _json(content)["helper"]["name"] == "Renamed"

    async def test_delete_cap_only_not_entity_scoped(self, hass, helper_env, hass_admin_user):
        # Helper deletion is cap-gated, not entity-scoped.
        created = _json((await _call(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "Owned"}}, _token(), hass))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()
        unscoped = TokenRecord(
            id=str(uuid.uuid4()), name="a", token_hash="x", created_at=utcnow(),
            created_by="u", permissions=PermissionTree(domains={}),
            cap_helper_write="allow", cap_registry_read="allow",
        )
        _, outcome, _ = await _call(
            "delete_helper", {"helper_type": "input_boolean", "helper_id": hid}, unscoped, hass)
        assert outcome == "allowed"


class TestVersionCapture:
    """create/edit/delete_helper record version history.

    The other helper tests pass a MagicMock for data, so capture is a no-op there;
    here a real VersionStore is supplied so the helper read-before path that
    populates `before` for edit and delete is actually exercised end-to-end.
    """

    @staticmethod
    def _data():
        versions = VersionStore()
        data = ATMData(
            store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=versions,
        )
        return data, versions

    async def test_create_edit_delete_record_history(self, hass, helper_env, hass_admin_user):
        data, versions = self._data()
        token = _token()

        created = _json((await _call_tool(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "A"}},
            token, hass, data))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()

        await _call_tool(
            "edit_helper",
            {"helper_type": "input_boolean", "helper_id": hid, "config": {"name": "B"}},
            token, hass, data)
        await hass.async_block_till_done()

        await _call_tool(
            "delete_helper", {"helper_type": "input_boolean", "helper_id": hid},
            token, hass, data)

        history = versions.list_for("helper", f"input_boolean:{hid}")
        assert [v.action for v in history] == ["delete", "edit", "create"]  # newest first
        delete_rec, edit_rec, create_rec = history

        assert create_rec.before is None
        assert create_rec.after == {"name": "A"}
        assert create_rec.token_name == token.name
        # edit_helper reads the prior config into `before`.
        assert edit_rec.before is not None and edit_rec.before.get("name") == "A"
        assert edit_rec.after == {"name": "B"}
        # delete_helper read the prior config; no `after`
        assert delete_rec.before is not None and delete_rec.before.get("name") == "B"
        assert delete_rec.after is None

    async def test_restore_existing_helper_reapplies_as_rollback(self, hass, helper_env, hass_admin_user):
        from custom_components.atm.mcp_view import restore_version

        data, versions = self._data()
        token = _token()
        created = _json((await _call_tool(
            "create_helper", {"helper_type": "input_boolean", "config": {"name": "A"}},
            token, hass, data))[0])
        hid = created["helper"]["id"]
        await hass.async_block_till_done()
        await _call_tool(
            "edit_helper", {"helper_type": "input_boolean", "helper_id": hid, "config": {"name": "B"}},
            token, hass, data)
        await hass.async_block_till_done()

        rkey = f"input_boolean:{hid}"
        create_ver = versions.list_for("helper", rkey)[-1]  # the create (name A)
        _r, outcome, _res = await restore_version(create_ver, "admin-9", hass, data)
        assert outcome == "allowed"

        latest = versions.list_for("helper", rkey)[0]
        assert latest.action == "rollback"
        assert latest.approved_by_user_id == "admin-9"
        assert latest.after.get("name") == "A"


class TestListHelpers:
    async def test_lists_accessible(self, hass, helper_env, hass_admin_user):
        await _call("create_helper", {"helper_type": "input_boolean", "config": {"name": "Listed"}}, _token(), hass)
        await hass.async_block_till_done()
        content, outcome, _ = await _call("list_helpers", {}, _token(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert any(h["helper_type"] == "input_boolean" and h["helper_id"] for h in body["helpers"])

    async def test_deny_without_cap(self, hass, helper_env, hass_admin_user):
        _, outcome, _ = await _call("list_helpers", {}, _token(cap_registry_read="deny"), hass)
        assert outcome == "denied"
