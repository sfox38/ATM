"""Tests for the bounded subscription MCP tool (watch_entity).

It blocks the tool call up to `timeout` (max 30s) and returns when the accessible
entity changes state, or reports no change on timeout.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from custom_components.atm.mcp_view import _call_tool
from custom_components.atm.token_store import PermissionNode, PermissionTree, TokenRecord


def _token(**caps) -> TokenRecord:
    tree = PermissionTree(domains={"light": PermissionNode(state="GREEN")})
    base = {"cap_config_read": "allow"}
    base.update(caps)
    return TokenRecord(
        id=str(uuid.uuid4()), name="t", token_hash="x",
        created_at=utcnow(), created_by="u", permissions=tree, **base,
    )


def _json(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _call(name, args, token, hass):
    from unittest.mock import MagicMock
    return await _call_tool(name, args, token, hass, MagicMock())


class TestWatchEntity:
    async def test_deny(self, hass):
        _, outcome, _ = await _call("watch_entity", {"entity_id": "light.kitchen"}, _token(cap_config_read="deny"), hass)
        assert outcome == "denied"

    async def test_inaccessible_not_found(self, hass):
        _, outcome, _ = await _call("watch_entity", {"entity_id": "sensor.secret"}, _token(), hass)
        assert outcome == "not_found"

    async def test_returns_change(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        token = _token()
        task = asyncio.create_task(_call("watch_entity", {"entity_id": "light.kitchen", "timeout": 5}, token, hass))
        await asyncio.sleep(0.1)  # let the listener register and the call start waiting
        hass.states.async_set("light.kitchen", "off", {})
        content, outcome, _ = await task
        assert outcome == "allowed"
        body = _json(content)
        assert body["changed"] is True
        assert body["state"] == "off"

    async def test_timeout_no_change(self, hass):
        hass.states.async_set("light.kitchen", "on", {})
        content, outcome, _ = await _call("watch_entity", {"entity_id": "light.kitchen", "timeout": 1}, _token(), hass)
        assert outcome == "allowed"
        assert _json(content)["changed"] is False
