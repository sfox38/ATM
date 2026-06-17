"""Panel registration for the ATM admin UI."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import (
    async_register_built_in_panel,
    async_remove_panel,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_FRONTEND_DIR = Path(__file__).parent / "frontend"
_JS_FILE = _FRONTEND_DIR / "atm-panel.js"
_PANEL_URL = "/local/atm"
_JS_URL = f"{_PANEL_URL}/atm-panel.js"
_PANEL_KEY = "atm"
_PANEL_REGISTERED_KEY = "atm_panel_registered"


def _js_url_with_cache_bust() -> str:
    """Append the bundle's mtime as a query param so each rebuild busts the cache.

    The panel JS is served without cache headers, so browsers otherwise keep a
    stale copy across frontend builds. Reading the mtime ties the URL to the
    actual file on disk; no version constant to keep in sync. Runs in the
    executor (see caller) since it touches the filesystem.
    """
    try:
        return f"{_JS_URL}?v={int(_JS_FILE.stat().st_mtime)}"
    except OSError:
        return _JS_URL


async def async_register_atm_panel(hass: HomeAssistant) -> None:
    """Register the static frontend bundle and the Lovelace panel.

    Safe to call on re-setup: removes any stale panel entry before registering.
    Static path registration is skipped silently if already registered.
    """
    try:
        await hass.http.async_register_static_paths([
            StaticPathConfig(
                url_path=_PANEL_URL,
                path=str(_FRONTEND_DIR),
                cache_headers=False,
            )
        ])
    except RuntimeError as exc:
        _LOGGER.warning("ATM: failed to register static path %s: %s", _PANEL_URL, exc)

    if hass.data.get(_PANEL_REGISTERED_KEY):
        async_remove_panel(hass, _PANEL_KEY)

    js_url = await hass.async_add_executor_job(_js_url_with_cache_bust)

    async_register_built_in_panel(
        hass=hass,
        component_name="custom",
        sidebar_title="ATM",
        sidebar_icon="mdi:key-variant",
        frontend_url_path=_PANEL_KEY,
        require_admin=True,
        config={
            "_panel_custom": {
                "name": "atm-panel",
                "js_url": js_url,
            }
        },
    )
    hass.data[_PANEL_REGISTERED_KEY] = True


def remove_atm_panel(hass: HomeAssistant) -> None:
    """Remove the panel if it was registered in this session.

    Silently skips if the panel was never registered (e.g. unload before setup
    completed, or HA restarted with the kill switch enabled).
    """
    if hass.data.pop(_PANEL_REGISTERED_KEY, False):
        async_remove_panel(hass, _PANEL_KEY)
