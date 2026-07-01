"""Tests for integration setup/unload orchestration in __init__.py.

These cover async_setup_entry's wiring decisions, not the HA primitives it drives:
the kill-switch gate on proxy/MCP route registration (admin routes + panel always
register), MESA degrading to off without failing setup, and async_unload_entry
cleaning up hass.data. Real timers and background tasks are neutralized so the test
asserts the orchestration without scheduling anything that would linger past it.
The end-to-end route wiring is covered separately by the real-HTTP scaffold test.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.atm as atm_init
from custom_components.atm.const import DOMAIN


def _settings(kill_switch: bool = False):
    return SimpleNamespace(
        kill_switch=kill_switch,
        audit_log_maxlen=1000,
        mesa_mode="off",
        audit_flush_interval=0,
    )


def _mock_store(kill_switch: bool = False) -> MagicMock:
    store = MagicMock()
    store.get_settings.return_value = _settings(kill_switch)
    store.list_tokens.return_value = []
    store.get_pending_approvals.return_value = []
    store.async_flush_last_used = AsyncMock()
    store.async_lock = asyncio.Lock()
    return store


def _fake_bg(coro, name=None):
    # Don't schedule background loops in the test; close the coroutine so there is
    # no "never awaited" warning, and hand back a cancel-able stand-in.
    coro.close()
    return MagicMock(cancel=MagicMock())


async def _run_setup(hass: HomeAssistant, *, kill_switch: bool = False, mesa_fail: bool = False):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    hass.http = MagicMock()
    store = _mock_store(kill_switch)

    mesa = AsyncMock(side_effect=RuntimeError("boom")) if mesa_fail else AsyncMock(return_value=None)

    with patch.object(atm_init.TokenStore, "async_create", AsyncMock(return_value=store)), \
         patch("custom_components.atm.mesa.async_setup_mesa", mesa), \
         patch("custom_components.atm.panel.async_register_atm_panel", AsyncMock()), \
         patch("custom_components.atm.panel.async_sync_mesa_inject", AsyncMock()), \
         patch("custom_components.atm.ws_dispatch.check_ws_dispatch_compat", return_value=None), \
         patch.object(atm_init, "async_track_time_interval", MagicMock(return_value=MagicMock())), \
         patch.object(hass, "async_create_background_task", _fake_bg), \
         patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()):
        result = await atm_init.async_setup_entry(hass, entry)

    return result, entry, hass.http.register_view.call_count


async def test_setup_registers_routes_when_kill_switch_off(hass: HomeAssistant):
    result, _entry, view_count = await _run_setup(hass, kill_switch=False)
    assert result is True
    data = hass.data[DOMAIN]
    assert data.routes_registered is True
    # Admin views plus the proxy/MCP/skill views were all registered.
    from custom_components.atm.admin_view import ALL_ADMIN_VIEWS
    assert view_count > len(ALL_ADMIN_VIEWS)


async def test_setup_skips_client_routes_when_kill_switch_on(hass: HomeAssistant):
    result, _entry, view_count = await _run_setup(hass, kill_switch=True)
    assert result is True
    data = hass.data[DOMAIN]
    # Client routes are NOT registered, but the helper is wired for later re-enable.
    assert data.routes_registered is False
    assert callable(data.async_register_routes)
    # Admin views are still registered (kill switch never hides the admin surface).
    from custom_components.atm.admin_view import ALL_ADMIN_VIEWS
    assert view_count == len(ALL_ADMIN_VIEWS)


async def test_setup_degrades_when_mesa_fails(hass: HomeAssistant):
    result, _entry, _ = await _run_setup(hass, mesa_fail=True)
    assert result is True
    # MESA setup raising must not block startup; the runtime degrades to off.
    assert hass.data[DOMAIN].mesa is None


async def test_unload_removes_data(hass: HomeAssistant):
    await _run_setup(hass, kill_switch=False)
    assert DOMAIN in hass.data
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    with patch("custom_components.atm.panel.remove_atm_panel"), \
         patch("custom_components.atm.panel.remove_mesa_inject"), \
         patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
        ok = await atm_init.async_unload_entry(hass, entry)

    assert ok is True
    assert DOMAIN not in hass.data
