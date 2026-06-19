"""Panel registration for the ATM admin UI."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import (
    add_extra_js_url,
    async_register_built_in_panel,
    async_remove_panel,
    remove_extra_js_url,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN, MESA_INJECT_MIN_HA

_LOGGER = logging.getLogger(__name__)

_FRONTEND_DIR = Path(__file__).parent / "frontend"
_JS_FILE = _FRONTEND_DIR / "atm-panel.js"
_PANEL_URL = "/local/atm"
_JS_URL = f"{_PANEL_URL}/atm-panel.js"
_PANEL_KEY = "atm"
_PANEL_REGISTERED_KEY = "atm_panel_registered"

# Optional in-context profile injector (mesa_inject_enabled). Served from the same
# static dir as the panel, but loaded on EVERY HA page via the frontend extra-module
# mechanism (admin-gated and feature-detected in JS), not as a panel.
_INJECT_JS_FILE = _FRONTEND_DIR / "atm-inject.js"
_INJECT_JS_URL = f"{_PANEL_URL}/atm-inject.js"
_INJECT_REGISTERED_URL_KEY = "atm_inject_registered_url"


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


def _inject_url_with_cache_bust() -> str:
    """The injector module URL with the bundle mtime appended (cache-bust).

    Runs in the executor (touches the filesystem). Falls back to the bare URL if
    the file is missing.
    """
    try:
        return f"{_INJECT_JS_URL}?v={int(_INJECT_JS_FILE.stat().st_mtime)}"
    except OSError:
        return _INJECT_JS_URL


def _inject_version_ok(hass: HomeAssistant) -> bool:
    """Whether the running HA is at or above the injector feature baseline.

    Fail-open: if the version cannot be parsed, return True and rely on the
    in-page feature-detection to self-disable. The version gate only avoids
    loading the script on known-incompatible old HA; it is not a safety boundary.
    """
    try:
        from awesomeversion import AwesomeVersion

        return AwesomeVersion(hass.config.version) >= AwesomeVersion(MESA_INJECT_MIN_HA)
    except Exception:  # noqa: BLE001 - version parsing must never disable the feature outright
        return True


async def async_sync_mesa_inject(hass: HomeAssistant) -> None:
    """Add or remove the in-context profile injector module to match settings.

    Idempotent; safe to call at setup and after any settings change. Gated on the
    mesa_inject_enabled setting and a soft HA-version baseline, never on the kill
    switch (this is an admin convenience, like the panel). The module is served
    from the panel's existing static path, so no extra path registration is needed.
    """
    data = hass.data.get(DOMAIN)
    if data is None:
        return
    enabled = data.store.get_settings().mesa_inject_enabled and _inject_version_ok(hass)
    prior = hass.data.get(_INJECT_REGISTERED_URL_KEY)

    if not enabled:
        if prior:
            _safe_remove_inject_url(hass, prior)
            hass.data.pop(_INJECT_REGISTERED_URL_KEY, None)
        return

    url = await hass.async_add_executor_job(_inject_url_with_cache_bust)
    if prior == url:
        return  # already current
    if prior:
        _safe_remove_inject_url(hass, prior)
    add_extra_js_url(hass, url)  # es5=False -> loaded as an ES module
    hass.data[_INJECT_REGISTERED_URL_KEY] = url


def remove_mesa_inject(hass: HomeAssistant) -> None:
    """Remove the injector module URL on unload, if registered."""
    prior = hass.data.pop(_INJECT_REGISTERED_URL_KEY, None)
    if prior:
        _safe_remove_inject_url(hass, prior)


def _safe_remove_inject_url(hass: HomeAssistant, url: str) -> None:
    try:
        remove_extra_js_url(hass, url)
    except Exception:  # noqa: BLE001 - removal is best-effort; never block teardown
        _LOGGER.debug("ATM: failed to remove inject module URL %s", url, exc_info=True)
