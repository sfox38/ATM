"""Tests for the diagnostics/traces MCP tools and get_history transitions mode."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.atm.mcp_view import _call_tool
from custom_components.atm.token_store import PermissionNode, PermissionTree, TokenRecord


def _token(permissions: PermissionTree | None = None, **caps) -> TokenRecord:
    tree = permissions or PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    return TokenRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x",
        created_at=utcnow(), created_by="u", permissions=tree, **caps,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, token, hass):
    return await _call_tool(name, args, token, hass, MagicMock())


# --- get_history transitions mode ---

class _FakeState:
    def __init__(self, state, when):
        self._s, self._w = state, when

    def as_dict(self):
        return {
            "entity_id": "light.kitchen", "state": self._s,
            "last_changed": self._w, "last_updated": self._w, "attributes": {"x": 1},
        }


class TestGetHistoryTransitions:
    def _patched(self, states):
        inst = MagicMock()
        inst.async_add_executor_job = AsyncMock(return_value={"light.kitchen": states})
        return patch("homeassistant.components.recorder.get_instance", return_value=inst)

    async def test_transitions_collapse_duplicates(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        now = utcnow()
        states = [
            _FakeState("on", now - timedelta(hours=3)),
            _FakeState("on", now - timedelta(hours=2)),
            _FakeState("off", now - timedelta(hours=1)),
            _FakeState("on", now),
        ]
        with self._patched(states):
            content, outcome, _ = await _call(
                "get_history", {"entity_id": "light.kitchen", "start_time": "24h"}, _token(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["mode"] == "transitions"
        assert [h["state"] for h in body["history"]] == ["on", "off", "on"]
        assert body["count"] == 3

    async def test_raw_mode_returns_full_dicts(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        now = utcnow()
        states = [_FakeState("on", now), _FakeState("off", now)]
        with self._patched(states):
            content, _, _ = await _call(
                "get_history", {"entity_id": "light.kitchen", "start_time": "24h", "mode": "raw"}, _token(), hass)
        body = _json(content)
        assert body["mode"] == "raw"
        assert len(body["history"]) == 2
        assert "attributes" in body["history"][0]

    async def test_inaccessible_entity_not_found(self, hass):
        content, outcome, _ = await _call(
            "get_history", {"entity_id": "sensor.secret", "start_time": "24h"}, _token(), hass)
        assert outcome in ("not_found", "denied")


# --- get_automation_traces ---

class _FakeTrace:
    def as_short_dict(self):
        return {
            "run_id": "run1", "state": "stopped", "script_execution": "finished",
            "last_step": "action/0", "timestamp": {"start": "2026-01-01T00:00:00+00:00"},
        }

    def as_dict(self):
        return {**self.as_short_dict(), "trace": {"trigger/0": [{"path": "trigger/0"}]}}


@pytest.fixture
def auto_env(hass: HomeAssistant):
    from homeassistant.components.trace.const import DATA_TRACE
    entry = MockConfigEntry(domain="test_integration", entry_id="e1")
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    auto = ent_reg.async_get_or_create(
        "automation", "test_integration", "auto_uid_1",
        config_entry=entry, suggested_object_id="morning",
    )
    hass.states.async_set(auto.entity_id, "on", {})
    hass.data[DATA_TRACE] = {"automation.auto_uid_1": {"run1": _FakeTrace()}}
    return {"entity_id": auto.entity_id}


def _auto_token(cap_traces="allow"):
    tree = PermissionTree(domains={"automation": PermissionNode(state="GREEN")})
    return _token(permissions=tree, cap_traces=cap_traces)


class TestGetAutomationTraces:
    async def test_deny_without_cap(self, hass, auto_env):
        _, outcome, _ = await _call("get_automation_traces", {"automation_id": "automation.morning"}, _auto_token("deny"), hass)
        assert outcome == "denied"

    async def test_list_traces(self, hass, auto_env):
        content, outcome, _ = await _call("get_automation_traces", {"automation_id": "automation.morning"}, _auto_token(), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["count"] == 1
        assert body["traces"][0]["run_id"] == "run1"

    async def test_specific_run_summary(self, hass, auto_env):
        content, _, _ = await _call(
            "get_automation_traces", {"automation_id": "automation.morning", "run_id": "run1", "summary": True}, _auto_token(), hass)
        body = _json(content)
        assert body["script_execution"] == "finished"
        assert "trace" not in body  # summary drops the heavy step tree

    async def test_unknown_automation_not_found(self, hass, auto_env):
        _, outcome, _ = await _call("get_automation_traces", {"automation_id": "automation.ghost"}, _auto_token(), hass)
        assert outcome == "not_found"


# --- get_system_health / check_config ---

class TestDiagnostics:
    async def test_system_health_deny(self, hass):
        _, outcome, _ = await _call("get_system_health", {}, _token(cap_diagnostics="deny"), hass)
        assert outcome == "denied"

    async def test_system_health_returns_version(self, hass):
        content, outcome, _ = await _call("get_system_health", {}, _token(cap_diagnostics="allow"), hass)
        assert outcome == "allowed"
        assert _json(content)["home_assistant_version"]

    async def test_system_health_redacts_integration_secrets(self, hass):
        # Per-integration health values are arbitrary; secret-keyed values and
        # URL-embedded credentials must be scrubbed before reaching the model.
        info = {
            "cloud": {"api_key": "supersecret", "can_reach_server": "ok"},
            "broker": {"url": "https://admin:hunter2@mqtt.local/x"},
        }
        with patch("homeassistant.components.system_health.get_info", AsyncMock(return_value=info)):
            content, outcome, _ = await _call("get_system_health", {}, _token(cap_diagnostics="allow"), hass)
        assert outcome == "allowed"
        body = _json(content)
        text = json.dumps(body)
        assert "supersecret" not in text          # sensitive-keyed value redacted
        assert "hunter2" not in text              # URL credentials scrubbed
        # Benign diagnostic values are preserved.
        assert body["integrations"]["cloud"]["can_reach_server"] == "ok"

    async def test_check_config_deny(self, hass):
        _, outcome, _ = await _call("check_config", {}, _token(cap_diagnostics="deny"), hass)
        assert outcome == "denied"

    async def test_check_config_valid(self, hass):
        fake = MagicMock(errors=[], warnings=[])
        with patch("homeassistant.helpers.check_config.async_check_ha_config_file", AsyncMock(return_value=fake)):
            content, outcome, _ = await _call("check_config", {}, _token(cap_diagnostics="allow"), hass)
        assert outcome == "allowed"
        body = _json(content)
        assert body["valid"] is True
        assert body["errors"] == []

    async def test_check_config_reports_errors(self, hass):
        err = MagicMock(message="bad yaml", domain="light")
        fake = MagicMock(errors=[err], warnings=[])
        with patch("homeassistant.helpers.check_config.async_check_ha_config_file", AsyncMock(return_value=fake)):
            content, _, _ = await _call("check_config", {}, _token(cap_diagnostics="allow"), hass)
        body = _json(content)
        assert body["valid"] is False
        assert body["errors"][0]["message"] == "bad yaml"
