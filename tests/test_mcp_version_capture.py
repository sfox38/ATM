"""Integration tests for configuration version capture.

Exercises the executor capture sites end-to-end with a real VersionStore for the
YAML-backed resources (automation, script, scene): create -> edit -> delete must
record one version each, with correct before/after. The other tool-test files pass
a MagicMock for data, so capture is a no-op there; here a real ATMData is supplied.
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atm.const import MAX_DIFF_INLINE_BYTES
from custom_components.atm.data import ATMData
from custom_components.atm.mcp_view import _call_tool, _read_automations_yaml, restore_version
from custom_components.atm.token_store import PermissionNode, PermissionTree, TokenRecord
from custom_components.atm.version_store import VersionStore


def _data() -> tuple[ATMData, VersionStore]:
    versions = VersionStore()
    data = ATMData(
        store=MagicMock(), rate_limiter=MagicMock(), audit=MagicMock(), versions=versions,
    )
    return data, versions


def _token(tree: PermissionTree | None = None, **caps) -> TokenRecord:
    return TokenRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x", created_at=utcnow(),
        created_by="u", permissions=tree or PermissionTree(domains={}), **caps,
    )


def _text(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


class TestAutomationCapture:
    @pytest.fixture
    def env(self, hass: HomeAssistant):
        hass.services.async_register("automation", "reload", lambda call: None)
        return hass

    async def test_create_edit_delete_record_history(self, hass, env):
        data, versions = _data()
        token = _token(cap_automation_write="allow")
        cfg = {
            "alias": "A",
            "trigger": [{"platform": "state", "entity_id": "input_boolean.x"}],
            "action": [{"service": "homeassistant.toggle", "entity_id": "input_boolean.x"}],
        }
        created = _text((await _call_tool("create_automation", {"config": cfg}, token, hass, data))[0])
        aid = created["id"]

        await _call_tool(
            "edit_automation", {"automation_id": aid, "config": dict(cfg, alias="A2")}, token, hass, data)
        await _call_tool("delete_automation", {"automation_id": aid}, token, hass, data)

        history = versions.list_for("automation", aid)
        assert [v.action for v in history] == ["delete", "edit", "create"]  # newest first
        delete_rec, edit_rec, create_rec = history
        assert create_rec.before is None and create_rec.after.get("alias") == "A"
        assert create_rec.token_name == token.name
        assert edit_rec.before.get("alias") == "A" and edit_rec.after.get("alias") == "A2"
        assert delete_rec.before.get("alias") == "A2" and delete_rec.after is None


class TestScriptCapture:
    @pytest.fixture
    def env(self, hass: HomeAssistant):
        hass.services.async_register("script", "reload", lambda call: None)
        return hass

    async def test_create_edit_delete_record_history(self, hass, env):
        data, versions = _data()
        token = _token(cap_script_write="allow")
        sid = "atm_test_script"
        cfg = {"alias": "S", "sequence": [{"service": "homeassistant.toggle", "entity_id": "input_boolean.x"}]}

        await _call_tool("create_script", {"script_id": sid, "config": cfg}, token, hass, data)
        await _call_tool(
            "edit_script", {"script_id": sid, "config": dict(cfg, alias="S2")}, token, hass, data)
        await _call_tool("delete_script", {"script_id": sid}, token, hass, data)

        history = versions.list_for("script", sid)
        assert [v.action for v in history] == ["delete", "edit", "create"]
        delete_rec, edit_rec, create_rec = history
        assert create_rec.before is None and create_rec.after.get("alias") == "S"
        assert edit_rec.before.get("alias") == "S" and edit_rec.after.get("alias") == "S2"
        assert delete_rec.before.get("alias") == "S2" and delete_rec.after is None


class TestSceneCapture:
    @pytest.fixture
    def light_entity(self, hass: HomeAssistant) -> str:
        entry = MockConfigEntry(domain="test_integration", entry_id="e1")
        entry.add_to_hass(hass)
        e = er.async_get(hass).async_get_or_create(
            "light", "test_integration", "uid_k", config_entry=entry, suggested_object_id="kitchen")
        hass.states.async_set(e.entity_id, "on", {})
        hass.services.async_register("scene", "reload", lambda call: None)
        return e.entity_id

    async def test_create_edit_delete_record_history(self, hass, light_entity):
        data, versions = _data()
        tree = PermissionTree(domains={
            "light": PermissionNode(state="GREEN"), "scene": PermissionNode(state="GREEN"),
        })
        token = _token(tree=tree, cap_scene_write="allow", cap_registry_read="allow")

        created = _text((await _call_tool(
            "create_scene", {"config": {"name": "Movie", "entities": {light_entity: "on"}}},
            token, hass, data))[0])
        sid = created["id"]
        await _call_tool(
            "edit_scene", {"scene_id": sid, "config": {"name": "Movie2", "entities": {light_entity: "off"}}},
            token, hass, data)
        await _call_tool("delete_scene", {"scene_id": sid}, token, hass, data)

        history = versions.list_for("scene", sid)
        assert [v.action for v in history] == ["delete", "edit", "create"]
        delete_rec, edit_rec, create_rec = history
        assert create_rec.before is None and create_rec.after.get("name") == "Movie"
        assert edit_rec.before.get("name") == "Movie" and edit_rec.after.get("name") == "Movie2"
        assert delete_rec.before.get("name") == "Movie2" and delete_rec.after is None


class TestAutomationRestore:
    @pytest.fixture
    def env(self, hass: HomeAssistant):
        hass.services.async_register("automation", "reload", lambda call: None)
        return hass

    @staticmethod
    def _cfg(alias: str) -> dict:
        return {
            "alias": alias,
            "trigger": [{"platform": "state", "entity_id": "input_boolean.x"}],
            "action": [{"service": "homeassistant.toggle", "entity_id": "input_boolean.x"}],
        }

    async def test_restore_existing_reapplies_as_rollback(self, hass, env):
        data, versions = _data()
        token = _token(cap_automation_write="allow")
        created = _text((await _call_tool("create_automation", {"config": self._cfg("A")}, token, hass, data))[0])
        aid = created["id"]
        await _call_tool("edit_automation", {"automation_id": aid, "config": self._cfg("B")}, token, hass, data)

        create_ver = versions.list_for("automation", aid)[-1]  # oldest is the create
        _result, outcome, _r = await restore_version(create_ver, "admin-1", hass, data)
        assert outcome == "allowed"

        items = _read_automations_yaml(os.path.join(hass.config.config_dir, "automations.yaml"))
        assert next(a for a in items if a.get("id") == aid)["alias"] == "A"  # config restored

        latest = versions.list_for("automation", aid)[0]
        assert latest.action == "rollback"
        assert latest.approved_by_user_id == "admin-1"
        assert latest.after.get("alias") == "A"

    async def test_restore_before_side_undoes_change(self, hass, env):
        # An edit version holds before=A, after=B. Restoring side="before" re-applies
        # the prior config (A); side="after" re-applies the change (B).
        data, versions = _data()
        token = _token(cap_automation_write="allow")
        created = _text((await _call_tool("create_automation", {"config": self._cfg("A")}, token, hass, data))[0])
        aid = created["id"]
        await _call_tool("edit_automation", {"automation_id": aid, "config": self._cfg("B")}, token, hass, data)

        edit_ver = versions.list_for("automation", aid)[0]  # newest is the edit (before A / after B)
        assert edit_ver.before.get("alias") == "A"
        assert edit_ver.after.get("alias") == "B"

        _result, outcome, _r = await restore_version(edit_ver, "admin-1", hass, data, side="before")
        assert outcome == "allowed"
        items = _read_automations_yaml(os.path.join(hass.config.config_dir, "automations.yaml"))
        assert next(a for a in items if a.get("id") == aid)["alias"] == "A"  # undone to before

    async def test_restore_missing_side_errors(self, hass, env):
        # Restoring the "before" of a create (before is None) is rejected cleanly.
        data, versions = _data()
        token = _token(cap_automation_write="allow")
        created = _text((await _call_tool("create_automation", {"config": self._cfg("A")}, token, hass, data))[0])
        aid = created["id"]
        create_ver = versions.list_for("automation", aid)[0]
        result, outcome, _r = await restore_version(create_ver, "admin-1", hass, data, side="before")
        assert outcome == "invalid_request"
        assert result.get("isError") is True

    async def test_restore_deleted_recreates_in_place(self, hass, env):
        data, versions = _data()
        token = _token(cap_automation_write="allow")
        created = _text((await _call_tool("create_automation", {"config": self._cfg("A")}, token, hass, data))[0])
        aid = created["id"]
        await _call_tool("delete_automation", {"automation_id": aid}, token, hass, data)

        delete_ver = versions.list_for("automation", aid)[0]  # newest is the delete
        result, outcome, _r = await restore_version(delete_ver, "admin-1", hass, data)
        assert outcome == "allowed"

        # Deleted automations are restored under their original ID.
        assert _text(result)["id"] == aid
        items = _read_automations_yaml(os.path.join(hass.config.config_dir, "automations.yaml"))
        assert sum(1 for a in items if a.get("id") == aid) == 1  # exactly one, in place

        latest = versions.list_for("automation", aid)[0]
        assert latest.action == "rollback"
        assert latest.approved_by_user_id == "admin-1"

    async def test_restore_deleted_is_idempotent(self, hass, env):
        # Repeated restores must update the original automation, not duplicate it.
        data, versions = _data()
        token = _token(cap_automation_write="allow")
        created = _text((await _call_tool("create_automation", {"config": self._cfg("A")}, token, hass, data))[0])
        aid = created["id"]
        await _call_tool("delete_automation", {"automation_id": aid}, token, hass, data)

        delete_ver = versions.list_for("automation", aid)[0]
        await restore_version(delete_ver, "admin-1", hass, data)
        await restore_version(delete_ver, "admin-1", hass, data)

        items = _read_automations_yaml(os.path.join(hass.config.config_dir, "automations.yaml"))
        assert sum(1 for a in items if a.get("id") == aid) == 1  # still exactly one

    async def test_restore_version_with_no_config_errors(self, hass, env):
        # A record whose before and after are both None has nothing to restore.
        data, _versions = _data()

        class _Rec:
            resource_type = "automation"
            resource_id = "x"
            before = None
            after = None

        result, outcome, _r = await restore_version(_Rec(), "admin-1", hass, data)
        assert outcome == "invalid_request"
        assert result.get("isError") is True


class TestRestoreEndpoint:
    @pytest.fixture
    def env(self, hass: HomeAssistant):
        hass.services.async_register("automation", "reload", lambda call: None)
        return hass

    async def test_post_restore_happy_path(self, hass, env):
        from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER

        from custom_components.atm.admin_view import ATMAdminVersionRestoreView
        from custom_components.atm.const import DOMAIN

        data, versions = _data()
        hass.data[DOMAIN] = data
        token = _token(cap_automation_write="allow")
        cfg = {
            "alias": "A",
            "trigger": [{"platform": "state", "entity_id": "input_boolean.x"}],
            "action": [{"service": "homeassistant.toggle", "entity_id": "input_boolean.x"}],
        }
        created = _text((await _call_tool("create_automation", {"config": cfg}, token, hass, data))[0])
        aid = created["id"]
        await _call_tool("edit_automation", {"automation_id": aid, "config": dict(cfg, alias="B")}, token, hass, data)
        create_ver = versions.list_for("automation", aid)[-1]

        user = MagicMock()
        user.is_admin = True
        user.id = "admin-7"
        state = {KEY_HASS_USER: user, KEY_AUTHENTICATED: True, "atm_rid": "rid"}
        req = MagicMock()
        req.__getitem__ = MagicMock(side_effect=lambda k: state.get(k))
        req.get = MagicMock(side_effect=lambda k, d=None: state.get(k, d))

        view = ATMAdminVersionRestoreView()
        view.hass = hass
        resp = await view.post(req, version_id=create_ver.id)
        assert resp.status == 200
        assert json.loads(resp.body)["restored"] is True

        items = _read_automations_yaml(os.path.join(hass.config.config_dir, "automations.yaml"))
        assert next(a for a in items if a.get("id") == aid)["alias"] == "A"
        assert versions.list_for("automation", aid)[0].approved_by_user_id == "admin-7"


class TestRawWriteCapture:
    async def test_yaml_config_records_create_then_edit(self, hass):
        data, versions = _data()
        token = _token(cap_yaml_edit="allow")
        await _call_tool("set_yaml_config", {"content": "default_config:\n"}, token, hass, data)
        await _call_tool("set_yaml_config", {"content": "default_config:\nfoo: bar\n"}, token, hass, data)

        # The test env may or may not ship a configuration.yaml, so assert on the
        # before/after content chain rather than on create-vs-edit of the first write.
        history = versions.list_for("yaml_config", "configuration.yaml")
        assert len(history) == 2
        second, first = history  # newest first
        assert first.after["content"] == "default_config:\n"
        assert second.before["content"] == "default_config:\n"
        assert second.after["content"] == "default_config:\nfoo: bar\n"

    async def test_write_file_records_create_then_edit(self, hass):
        data, versions = _data()
        token = _token(cap_filesystem="allow")
        rel = "www/atm_cap_test.js"
        target = os.path.join(hass.config.config_dir, "www", "atm_cap_test.js")
        if os.path.exists(target):  # the test config dir can persist across runs
            os.remove(target)
        await _call_tool("write_file", {"path": rel, "content": "v1"}, token, hass, data)
        await _call_tool("write_file", {"path": rel, "content": "v2"}, token, hass, data)

        history = versions.list_for("file", rel)
        assert [v.action for v in history] == ["edit", "create"]
        edit_rec, create_rec = history
        assert create_rec.before is None and create_rec.after["content"] == "v1"
        assert edit_rec.before["content"] == "v1" and edit_rec.after["content"] == "v2"

    async def test_oversized_content_stored_as_truncated_marker(self, hass):
        data, versions = _data()
        token = _token(cap_yaml_edit="allow")
        big = "x" * (MAX_DIFF_INLINE_BYTES + 50)
        await _call_tool("set_yaml_config", {"content": big}, token, hass, data)
        rec = versions.list_for("yaml_config", "configuration.yaml")[0]
        assert rec.after["content"] is None
        assert rec.after["truncated"] is True
        assert rec.after["bytes"] == len(big.encode("utf-8"))


class TestRawWriteRestore:
    async def test_restore_yaml_reapplies_and_records_rollback(self, hass):
        data, versions = _data()
        token = _token(cap_yaml_edit="allow")
        await _call_tool("set_yaml_config", {"content": "A\n"}, token, hass, data)
        await _call_tool("set_yaml_config", {"content": "B\n"}, token, hass, data)
        create_ver = versions.list_for("yaml_config", "configuration.yaml")[-1]  # after == "A\n"

        result, _outcome, _res = await restore_version(create_ver, "admin-1", hass, data)
        assert result.get("isError") is not True

        with open(hass.config.path("configuration.yaml"), encoding="utf-8") as f:
            assert f.read() == "A\n"
        newest = versions.list_for("yaml_config", "configuration.yaml")[0]
        assert newest.action == "rollback"
        assert newest.approved_by_user_id == "admin-1"

    async def test_restore_file_reapplies(self, hass):
        data, versions = _data()
        token = _token(cap_filesystem="allow")
        rel = "www/atm_restore_test.js"
        await _call_tool("write_file", {"path": rel, "content": "first"}, token, hass, data)
        await _call_tool("write_file", {"path": rel, "content": "second"}, token, hass, data)
        create_ver = versions.list_for("file", rel)[-1]  # after == "first"

        await restore_version(create_ver, "admin-2", hass, data)
        with open(os.path.join(hass.config.config_dir, "www", "atm_restore_test.js"), encoding="utf-8") as f:
            assert f.read() == "first"
        assert versions.list_for("file", rel)[0].action == "rollback"

    async def test_restore_truncated_version_refused(self, hass):
        data, versions = _data()
        token = _token(cap_yaml_edit="allow")
        big = "y" * (MAX_DIFF_INLINE_BYTES + 50)
        await _call_tool("set_yaml_config", {"content": big}, token, hass, data)
        rec = versions.list_for("yaml_config", "configuration.yaml")[0]
        result, outcome, _res = await restore_version(rec, "admin-3", hass, data)
        assert outcome == "invalid_request"
        assert "too large" in result["content"][0]["text"].lower()
