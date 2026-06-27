"""Tests for ATM sensor platform."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from custom_components.atm.const import DOMAIN, TOKEN_PREFIX
from custom_components.atm.data import ATMData
from custom_components.atm.audit import AuditLog
from custom_components.atm.rate_limiter import RateLimiter
from custom_components.atm.sensor import (
    ATMTokenSensor,
    _make_sensors,
    async_create_token_sensors,
    async_remove_token_sensors,
    async_setup_entry,
)
from custom_components.atm.token_store import token_name_slug as _token_slug
from custom_components.atm.token_store import TokenRecord, TokenStore


def _make_token(
    name: str = "my-token",
    revoked: bool = False,
    expires_at=None,
    last_used_at=None,
) -> TokenRecord:
    from homeassistant.util.dt import utcnow

    raw = TOKEN_PREFIX + secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return TokenRecord(
        id=str(uuid.uuid4()),
        name=name,
        token_hash=token_hash,
        created_at=utcnow(),
        created_by="user1",
        revoked=revoked,
        expires_at=expires_at,
        last_used_at=last_used_at,
    )


def _make_data(tokens: list[TokenRecord] | None = None) -> ATMData:
    store = MagicMock(spec=TokenStore)
    store.list_tokens = MagicMock(return_value=tokens or [])
    store.async_lock = asyncio.Lock()
    rate_limiter = MagicMock(spec=RateLimiter)
    audit = MagicMock(spec=AuditLog)
    return ATMData(
        store=store,
        rate_limiter=rate_limiter,
        audit=audit,
    )


def _make_hass(data: ATMData) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: data}
    return hass


def _make_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test-entry"
    return entry


# --- _token_slug ---

def test_token_slug_lowercases_and_replaces_hyphens():
    token = _make_token(name="My-Token")
    assert _token_slug(token.name) == "my_token"


def test_token_slug_no_hyphens_unchanged():
    token = _make_token(name="mytoken")
    assert _token_slug(token.name) == "mytoken"


# --- _make_sensors ---

def test_make_sensors_returns_six():
    token = _make_token(name="alpha")
    data = _make_data()
    sensors = _make_sensors(token, data)
    assert len(sensors) == 6


def test_make_sensors_unique_ids():
    token = _make_token(name="alpha")
    data = _make_data()
    sensors = _make_sensors(token, data)
    unique_ids = [s._attr_unique_id for s in sensors]
    expected = [
        "atm_alpha_status",
        "atm_alpha_request_count",
        "atm_alpha_denied_count",
        "atm_alpha_rate_limit_hits",
        "atm_alpha_last_access",
        "atm_alpha_expires_in",
    ]
    assert unique_ids == expected


def test_make_sensors_names_are_titled():
    token = _make_token(name="my-token")
    data = _make_data()
    sensors = _make_sensors(token, data)
    names = [s._attr_name for s in sensors]
    assert names[0] == "Status"
    assert names[1] == "Request Count"
    assert names[4] == "Last Access"


# --- status sensor ---

def test_status_active():
    token = _make_token()
    data = _make_data()
    sensor = ATMTokenSensor(token, "my_token", "status", data)
    assert sensor.native_value == "active"


def test_status_revoked():
    token = _make_token(revoked=True)
    data = _make_data()
    sensor = ATMTokenSensor(token, "my_token", "status", data)
    assert sensor.native_value == "revoked"


def test_status_expired():
    from homeassistant.util.dt import utcnow

    token = _make_token(expires_at=utcnow() - timedelta(hours=1))
    data = _make_data()
    sensor = ATMTokenSensor(token, "my_token", "status", data)
    assert sensor.native_value == "expired"


# --- counter sensors ---

def test_request_count_zero_when_no_counters():
    token = _make_token()
    data = _make_data()
    sensor = ATMTokenSensor(token, "my_token", "request_count", data)
    assert sensor.native_value == 0


def test_request_count_from_token_counters():
    token = _make_token()
    data = _make_data()
    data.token_counters[token.id] = {"request_count": 42, "denied_count": 3, "rate_limit_hits": 1}
    sensor = ATMTokenSensor(token, "my_token", "request_count", data)
    assert sensor.native_value == 42


def test_denied_count_from_token_counters():
    token = _make_token()
    data = _make_data()
    data.token_counters[token.id] = {"request_count": 10, "denied_count": 5, "rate_limit_hits": 0}
    sensor = ATMTokenSensor(token, "my_token", "denied_count", data)
    assert sensor.native_value == 5


def test_rate_limit_hits_from_token_counters():
    token = _make_token()
    data = _make_data()
    data.token_counters[token.id] = {"request_count": 10, "denied_count": 0, "rate_limit_hits": 7}
    sensor = ATMTokenSensor(token, "my_token", "rate_limit_hits", data)
    assert sensor.native_value == 7


def test_denied_count_zero_when_no_counters():
    token = _make_token()
    data = _make_data()
    sensor = ATMTokenSensor(token, "my_token", "denied_count", data)
    assert sensor.native_value == 0


# --- last_access sensor ---

def test_last_access_never_when_none():
    token = _make_token(last_used_at=None)
    data = _make_data()
    sensor = ATMTokenSensor(token, "my_token", "last_access", data)
    assert sensor.native_value is None


def test_last_access_returns_iso_string():
    from homeassistant.util.dt import utcnow

    ts = utcnow()
    token = _make_token(last_used_at=ts)
    data = _make_data()
    sensor = ATMTokenSensor(token, "my_token", "last_access", data)
    assert sensor.native_value == ts.isoformat()


# --- expires_in sensor ---

def test_expires_in_no_expiry_string():
    token = _make_token(expires_at=None)
    data = _make_data()
    sensor = ATMTokenSensor(token, "my_token", "expires_in", data)
    assert sensor.native_value == "No expiry"
    assert sensor.state_class is None
    assert sensor.native_unit_of_measurement is None


def test_expires_in_returns_days():
    from homeassistant.util.dt import utcnow

    token = _make_token(expires_at=utcnow() + timedelta(days=5))
    data = _make_data()
    sensor = ATMTokenSensor(token, "my_token", "expires_in", data)
    assert sensor.native_value == 5


def test_expires_in_partial_day_rounds_up():
    from homeassistant.util.dt import utcnow

    token = _make_token(expires_at=utcnow() + timedelta(hours=2))
    data = _make_data()
    sensor = ATMTokenSensor(token, "my_token", "expires_in", data)
    assert sensor.native_value == 1


# --- async_setup_entry ---

@pytest.mark.asyncio
async def test_setup_entry_registers_callback_and_adds_sensors():
    token = _make_token(name="tok-one")
    data = _make_data(tokens=[token])
    hass = _make_hass(data)
    entry = _make_entry()
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    assert data.async_add_entities_cb is add_entities
    assert "tok_one" in data.platform_entities
    assert len(data.platform_entities["tok_one"]) == 6
    add_entities.assert_called_once()
    added = add_entities.call_args.args[0]
    assert len(added) == 6


@pytest.mark.asyncio
async def test_setup_entry_no_tokens_does_not_call_add_entities():
    data = _make_data(tokens=[])
    hass = _make_hass(data)
    entry = _make_entry()
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    assert data.async_add_entities_cb is add_entities
    add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_setup_entry_multiple_tokens():
    tokens = [_make_token(name="alpha"), _make_token(name="beta")]
    data = _make_data(tokens=tokens)
    hass = _make_hass(data)
    entry = _make_entry()
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    assert "alpha" in data.platform_entities
    assert "beta" in data.platform_entities
    added = add_entities.call_args.args[0]
    assert len(added) == 12


# --- async_create_token_sensors ---

@pytest.mark.asyncio
async def test_create_token_sensors_adds_to_platform_entities():
    token = _make_token(name="new-tok")
    data = _make_data()
    data.async_add_entities_cb = MagicMock()
    hass = _make_hass(data)
    entry = _make_entry()

    await async_create_token_sensors(hass, entry, token)

    assert "new_tok" in data.platform_entities
    assert len(data.platform_entities["new_tok"]) == 6
    data.async_add_entities_cb.assert_called_once()


@pytest.mark.asyncio
async def test_create_token_sensors_noop_when_no_callback():
    token = _make_token(name="new-tok")
    data = _make_data()
    data.async_add_entities_cb = None
    hass = _make_hass(data)
    entry = _make_entry()

    await async_create_token_sensors(hass, entry, token)

    assert "new_tok" not in data.platform_entities


# --- async_remove_token_sensors ---

@pytest.mark.asyncio
async def test_remove_token_sensors_calls_async_remove_on_each():
    token = _make_token(name="gone-tok")
    data = _make_data()
    hass = _make_hass(data)

    sensor1 = MagicMock(spec=ATMTokenSensor)
    sensor1.async_remove = AsyncMock()
    sensor1.unique_id = None
    sensor2 = MagicMock(spec=ATMTokenSensor)
    sensor2.async_remove = AsyncMock()
    sensor2.unique_id = None
    data.platform_entities["gone_tok"] = [sensor1, sensor2]

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()), \
         patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()):
        await async_remove_token_sensors(hass, "gone_tok")

    sensor1.async_remove.assert_called_once()
    sensor2.async_remove.assert_called_once()
    assert "gone_tok" not in data.platform_entities


@pytest.mark.asyncio
async def test_remove_token_sensors_unknown_slug_is_noop():
    data = _make_data()
    hass = _make_hass(data)

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()), \
         patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()):
        await async_remove_token_sensors(hass, "nonexistent")


@pytest.mark.asyncio
async def test_remove_token_sensors_pops_from_platform_entities():
    token = _make_token(name="gone-tok")
    data = _make_data()
    hass = _make_hass(data)

    sensor = MagicMock(spec=ATMTokenSensor)
    sensor.async_remove = AsyncMock()
    sensor.unique_id = None
    data.platform_entities["gone_tok"] = [sensor]
    data.platform_entities["other_tok"] = [MagicMock()]

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()), \
         patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()):
        await async_remove_token_sensors(hass, "gone_tok")

    assert "gone_tok" not in data.platform_entities
    assert "other_tok" in data.platform_entities
