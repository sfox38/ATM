"""Tests for the optional in-context profile injector registration (panel.py).

Covers async_sync_mesa_inject: it adds the extra ES-module URL only when the
mesa_inject_enabled setting is on AND the HA version meets the soft baseline, and
removes it when the setting is toggled off. The actual DOM injection is frontend
code (covered by vitest); this is just the registration gate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.atm import panel as panel_mod
from custom_components.atm.const import DOMAIN
from custom_components.atm.token_store import GlobalSettings


def _make_hass(inject_enabled: bool, version: str = "2026.6.0") -> MagicMock:
    hass = MagicMock()
    store = MagicMock()
    store.get_settings.return_value = GlobalSettings(mesa_inject_enabled=inject_enabled)
    data = MagicMock()
    data.store = store
    hass.data = {DOMAIN: data}
    hass.config.version = version
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k))
    return hass


@pytest.mark.asyncio
async def test_sync_adds_module_when_enabled():
    hass = _make_hass(True)
    with patch.object(panel_mod, "add_extra_js_url") as add, \
         patch.object(panel_mod, "remove_extra_js_url") as rem:
        await panel_mod.async_sync_mesa_inject(hass)

    assert add.call_count == 1
    url = add.call_args.args[1]
    assert url.startswith("/local/atm/atm-inject.js")
    rem.assert_not_called()
    assert hass.data[panel_mod._INJECT_REGISTERED_URL_KEY] == url


@pytest.mark.asyncio
async def test_sync_does_nothing_when_disabled():
    hass = _make_hass(False)
    with patch.object(panel_mod, "add_extra_js_url") as add:
        await panel_mod.async_sync_mesa_inject(hass)
    add.assert_not_called()
    assert panel_mod._INJECT_REGISTERED_URL_KEY not in hass.data


@pytest.mark.asyncio
async def test_sync_skips_below_version_baseline():
    hass = _make_hass(True, version="2024.1.0")
    with patch.object(panel_mod, "add_extra_js_url") as add:
        await panel_mod.async_sync_mesa_inject(hass)
    add.assert_not_called()


@pytest.mark.asyncio
async def test_sync_removes_when_toggled_off():
    hass = _make_hass(True)
    with patch.object(panel_mod, "add_extra_js_url"), \
         patch.object(panel_mod, "remove_extra_js_url") as rem:
        await panel_mod.async_sync_mesa_inject(hass)  # add
        url = hass.data[panel_mod._INJECT_REGISTERED_URL_KEY]
        hass.data[DOMAIN].store.get_settings.return_value = GlobalSettings(
            mesa_inject_enabled=False
        )
        await panel_mod.async_sync_mesa_inject(hass)  # remove

    rem.assert_called_once_with(hass, url)
    assert panel_mod._INJECT_REGISTERED_URL_KEY not in hass.data


@pytest.mark.asyncio
async def test_remove_mesa_inject_clears_registration():
    hass = _make_hass(True)
    with patch.object(panel_mod, "add_extra_js_url"), \
         patch.object(panel_mod, "remove_extra_js_url") as rem:
        await panel_mod.async_sync_mesa_inject(hass)
        url = hass.data[panel_mod._INJECT_REGISTERED_URL_KEY]
        panel_mod.remove_mesa_inject(hass)

    rem.assert_called_once_with(hass, url)
    assert panel_mod._INJECT_REGISTERED_URL_KEY not in hass.data
