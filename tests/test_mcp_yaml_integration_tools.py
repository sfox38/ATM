"""Tests for raw YAML edit (get/set_yaml_config) and integration enable/disable."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atm.mcp_view import _EXECUTOR_REGISTRY, _call_tool
from custom_components.atm.token_store import PermissionTree, TokenRecord


def _token(**caps) -> TokenRecord:
    base = {"cap_yaml_edit": "allow", "cap_integration_write": "allow"}
    base.update(caps)
    return TokenRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x",
        created_at=utcnow(), created_by="u", permissions=PermissionTree(), **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, token, hass):
    return await _call_tool(name, args, token, hass, MagicMock())


class TestExecutorRegistration:
    def test_registered(self):
        assert "set_yaml_config" in _EXECUTOR_REGISTRY
        assert "set_integration_enabled" in _EXECUTOR_REGISTRY


class TestYamlConfig:
    async def test_get_deny(self, hass):
        _, outcome, _ = await _call("get_yaml_config", {}, _token(cap_yaml_edit="deny"), hass)
        assert outcome == "denied"

    async def test_get_and_set(self, hass):
        path = hass.config.path("configuration.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("default_config:\n")
        content, outcome, _ = await _call("get_yaml_config", {}, _token(), hass)
        assert outcome == "allowed"
        assert _json(content)["content"] == "default_config:\n"

        content, outcome, _ = await _call("set_yaml_config", {"content": "default_config:\nfrontend:\n"}, _token(), hass)
        assert outcome == "allowed"
        with open(path, encoding="utf-8") as f:
            assert f.read() == "default_config:\nfrontend:\n"

    async def test_set_non_string(self, hass):
        _, outcome, _ = await _call("set_yaml_config", {"content": {"a": 1}}, _token(), hass)
        assert outcome == "invalid_request"


class TestIntegrations:
    async def test_list_deny(self, hass):
        _, outcome, _ = await _call("list_integrations", {}, _token(cap_integration_write="deny"), hass)
        assert outcome == "denied"

    async def test_list_excludes_atm(self, hass):
        MockConfigEntry(domain="test_integration", title="Test", entry_id="e1").add_to_hass(hass)
        MockConfigEntry(domain="atm", title="ATM", entry_id="atm1").add_to_hass(hass)
        content, outcome, _ = await _call("list_integrations", {}, _token(), hass)
        assert outcome == "allowed"
        domains = {i["domain"] for i in _json(content)["integrations"]}
        assert "test_integration" in domains
        assert "atm" not in domains

    async def test_disable_calls_ha(self, hass):
        entry = MockConfigEntry(domain="test_integration", entry_id="e2")
        entry.add_to_hass(hass)
        with patch_set_disabled(hass) as mock:
            content, outcome, _ = await _call(
                "set_integration_enabled", {"entry_id": "e2", "enabled": False}, _token(), hass)
        assert outcome == "allowed"
        mock.assert_awaited_once()
        assert mock.await_args.args[1] == ConfigEntryDisabler.USER

    async def test_enable_passes_none(self, hass):
        entry = MockConfigEntry(domain="test_integration", entry_id="e3")
        entry.add_to_hass(hass)
        with patch_set_disabled(hass) as mock:
            _, outcome, _ = await _call(
                "set_integration_enabled", {"entry_id": "e3", "enabled": True}, _token(), hass)
        assert outcome == "allowed"
        assert mock.await_args.args[1] is None

    async def test_unknown_entry(self, hass):
        _, outcome, _ = await _call("set_integration_enabled", {"entry_id": "nope", "enabled": True}, _token(), hass)
        assert outcome == "not_found"

    async def test_atm_entry_refused(self, hass):
        MockConfigEntry(domain="atm", entry_id="atm2").add_to_hass(hass)
        _, outcome, _ = await _call("set_integration_enabled", {"entry_id": "atm2", "enabled": False}, _token(), hass)
        assert outcome == "not_found"

    async def test_non_bool(self, hass):
        MockConfigEntry(domain="test_integration", entry_id="e4").add_to_hass(hass)
        _, outcome, _ = await _call("set_integration_enabled", {"entry_id": "e4", "enabled": "yes"}, _token(), hass)
        assert outcome == "invalid_request"


def patch_set_disabled(hass):
    from unittest.mock import patch
    return patch.object(hass.config_entries, "async_set_disabled_by", new=AsyncMock(return_value=True))
