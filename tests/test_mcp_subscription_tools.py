"""Tests for the bounded subscription MCP tool (watch_entity).

It blocks the tool call up to `timeout` (max 30s) and returns when the accessible
entity changes state, or reports no change on timeout.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import patch

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
        import custom_components.atm.mcp_view as mcp_view

        hass.states.async_set("light.kitchen", "on", {})
        token = _token()

        # Deterministic readiness instead of a fixed sleep: wrap the real
        # async_track_state_change_event so we fire the change only once the tool
        # has actually registered its listener (registration is synchronous, so the
        # listener is live the moment the wrapper sets the event).
        ready = asyncio.Event()
        real_track = mcp_view.async_track_state_change_event

        def _tracked(h, eids, cb):
            unsub = real_track(h, eids, cb)
            ready.set()
            return unsub

        with patch.object(mcp_view, "async_track_state_change_event", side_effect=_tracked):
            task = asyncio.create_task(
                _call("watch_entity", {"entity_id": "light.kitchen", "timeout": 5}, token, hass))
            await asyncio.wait_for(ready.wait(), timeout=2)
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
