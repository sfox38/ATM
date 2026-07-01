"""Tests for build_safe_config, the cap_config_read-safe view of hass.config.

get_config (REST + MCP) must not hand a cap_config_read token precise home
coordinates, internal/external URLs, or host filesystem paths; it returns an
allowlisted subset of agent-useful context with ATM's own components stripped.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.atm.helpers import build_safe_config


def _hass_with_config(d: dict) -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(as_dict=lambda: d))


_RAW = {
    "latitude": 37.1234,
    "longitude": -122.5678,
    "elevation": 5,
    "radius": 100,
    "internal_url": "http://10.0.0.5:8123",
    "external_url": "https://home.duckdns.org",
    "config_dir": "/config",
    "allowlist_external_dirs": ["/media"],
    "allowlist_external_urls": ["https://x"],
    "media_dirs": {"local": "/media"},
    "location_name": "Home",
    "time_zone": "America/Los_Angeles",
    "unit_system": {"temperature": "C"},
    "version": "2026.6.0",
    "components": ["light", "atm", "atm.sensor", "automation"],
    "currency": "USD",
    "country": "US",
    "language": "en",
    "config_source": "storage",
    "state": "RUNNING",
}


def test_drops_sensitive_fields():
    safe = build_safe_config(_hass_with_config(_RAW))
    for k in (
        "latitude", "longitude", "elevation", "radius",
        "internal_url", "external_url", "config_dir",
        "allowlist_external_dirs", "allowlist_external_urls", "media_dirs",
    ):
        assert k not in safe


def test_keeps_agent_useful_fields():
    safe = build_safe_config(_hass_with_config(_RAW))
    assert safe["location_name"] == "Home"
    assert safe["time_zone"] == "America/Los_Angeles"
    assert safe["version"] == "2026.6.0"
    assert safe["unit_system"] == {"temperature": "C"}
    assert safe["config_source"] == "storage"


def test_strips_atm_components():
    safe = build_safe_config(_hass_with_config(_RAW))
    assert "atm" not in safe["components"]
    assert "atm.sensor" not in safe["components"]
    assert "light" in safe["components"]
    assert "automation" in safe["components"]


def test_allowlist_excludes_unknown_keys():
    raw = {**_RAW, "some_future_secret_key": "leak-me"}
    safe = build_safe_config(_hass_with_config(raw))
    assert "some_future_secret_key" not in safe
