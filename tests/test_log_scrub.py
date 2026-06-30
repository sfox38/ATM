"""Tests for log-text redaction used by the cap_log_read tools/endpoint.

collect_log_entries routes every message and exception through _scrub_log_text,
which removes ATM tokens, JWTs/LLATs, and URL-embedded credentials so a token
holding cap_log_read never receives another integration's secret verbatim.
"""

from __future__ import annotations

from custom_components.atm.helpers import (
    _scrub_log_text,
    redact_diagnostics,
    redact_secrets_in_text,
)


def test_scrubs_atm_token():
    raw = "auth failed for atm_" + "a" * 64
    assert "atm_" + "a" * 64 not in _scrub_log_text(raw)
    assert "<atm-token>" in _scrub_log_text(raw)


def test_scrubs_jwt_llat():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJoYSJ9.abc123signaturepart"
    out = _scrub_log_text(f"rejected token {jwt} from client")
    assert jwt not in out
    assert "<token>" in out


def test_scrubs_url_query_credentials():
    out = _scrub_log_text("GET https://api.example.com/v1?access_token=SEKRET&page=2")
    assert "SEKRET" not in out
    assert "access_token=<redacted>" in out
    assert "page=2" in out


def test_scrubs_userinfo_credentials():
    out = _scrub_log_text("connecting to https://admin:hunter2@db.local/x")
    assert "hunter2" not in out
    assert "://<redacted>@" in out


def test_preserves_benign_text():
    raw = "Setup of sensor.kitchen took 1.2 seconds"
    assert _scrub_log_text(raw) == raw


def test_redact_secrets_in_text_yaml_keys():
    diff = "name: My HA\npassword: hunter2\napi_key: abcd1234\nlatitude: 51.5"
    out = redact_secrets_in_text(diff)
    assert "hunter2" not in out
    assert "abcd1234" not in out
    assert "password: <redacted>" in out
    assert "api_key: <redacted>" in out
    assert "latitude: 51.5" in out  # benign key preserved


def test_redact_secrets_in_text_none_passthrough():
    assert redact_secrets_in_text(None) is None
    assert redact_secrets_in_text("") == ""


def test_redact_diagnostics_scrubs_topology_conservatively():
    obj = {
        "a": "10.0.0.5",                 # private IPv4
        "b": "192.168.1.1",              # private IPv4
        "c": "172.20.3.4",               # private IPv4 (172.16/12)
        "d": "fe80::1ff:fe23:4567:890a", # link-local IPv6
        "e": "http://nas.local:8080/x",  # bare URL host
        "f": "/config/.storage/db",      # absolute unix path
        "g": r"C:\HA\config\secrets",    # windows path
        "ok": "reachable",               # benign
        "ver": "4.8.0.1",                # public-IP-shaped version: NOT private, keep
        "pub": "8.8.8.8",                # public IP: not a private range, keep
        "frac": "ratio 1/2 done",        # lone slash: not a path, keep
    }
    out = redact_diagnostics(obj)
    assert out["a"] == "<redacted-ip>"
    assert out["b"] == "<redacted-ip>"
    assert out["c"] == "<redacted-ip>"
    assert out["d"] == "<redacted-ip>"
    assert out["e"] == "<redacted-url>"
    assert out["f"] == "<redacted-path>"
    assert out["g"] == "<redacted-path>"
    assert out["ok"] == "reachable"
    assert out["ver"] == "4.8.0.1"
    assert out["pub"] == "8.8.8.8"
    assert out["frac"] == "ratio 1/2 done"
