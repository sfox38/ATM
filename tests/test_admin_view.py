"""Tests for ATM admin views."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.atm.admin_view import (
    ATMAdminAuditView,
    ATMAdminEntityTreeView,
    ATMAdminPermissionDomainView,
    ATMAdminPermissionsView,
    ATMAdminSettingsView,
    ATMAdminTokenAuditView,
    ATMAdminTokenStatsView,
    ATMAdminTokensView,
    ATMAdminTokenView,
    ATMAdminArchivedTokensView,
    ALL_ADMIN_VIEWS,
)
from custom_components.atm.audit import AuditLog
from custom_components.atm.const import DOMAIN, TOKEN_PREFIX
from custom_components.atm.data import ATMData
from custom_components.atm.rate_limiter import RateLimiter
from custom_components.atm.token_store import ArchivedTokenRecord, GlobalSettings, TokenRecord, TokenStore


def _make_active_token(name: str = "test-token", pass_through: bool = False) -> TokenRecord:
    from homeassistant.util.dt import utcnow

    raw = TOKEN_PREFIX + secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return TokenRecord(
        id=str(uuid.uuid4()),
        name=name,
        token_hash=token_hash,
        created_at=utcnow(),
        created_by="user1",
        pass_through=pass_through,
    )


def _make_data(tokens: list[TokenRecord] | None = None) -> ATMData:
    store = MagicMock(spec=TokenStore)
    store.list_tokens.return_value = tokens or []
    store.list_archived.return_value = []
    store.get_settings.return_value = GlobalSettings()
    store.get_entity_hints.return_value = {}
    store.async_lock = asyncio.Lock()

    rate_limiter = MagicMock(spec=RateLimiter)
    audit = MagicMock(spec=AuditLog)
    audit.query.return_value = []

    return ATMData(
        store=store,
        rate_limiter=rate_limiter,
        audit=audit,
    )


def _make_admin_request(is_admin: bool = True, body: bytes = b"", authenticated: bool = True) -> MagicMock:
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER

    user = MagicMock()
    user.is_admin = is_admin
    user.id = "admin-user-id"

    def _get(k, default=None):
        if k == KEY_HASS_USER:
            return user
        if k == KEY_AUTHENTICATED:
            return authenticated
        return default

    request = MagicMock()
    request.query = {}
    request.read = AsyncMock(return_value=body)
    request.content_length = len(body)
    request.content = MagicMock()
    request.content.read = AsyncMock(return_value=body)
    request.__getitem__ = MagicMock(side_effect=lambda k: user if k == KEY_HASS_USER else None)
    request.get = MagicMock(side_effect=_get)
    return request


def _make_hass(data: ATMData) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: data}
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))
    return hass


@pytest.mark.asyncio
async def test_unauthenticated_rejected_from_tokens_list():
    token = _make_active_token()
    data = _make_data([token])
    hass = _make_hass(data)

    view = ATMAdminTokensView()
    view.hass = hass

    request = _make_admin_request(authenticated=False)
    resp = await view.get(request)

    assert resp.status == 401
    body = json.loads(resp.text)
    assert body["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_non_admin_rejected_from_tokens_list():
    token = _make_active_token()
    data = _make_data([token])
    hass = _make_hass(data)

    view = ATMAdminTokensView()
    view.hass = hass

    request = _make_admin_request(is_admin=False)
    resp = await view.get(request)

    assert resp.status == 403
    body = json.loads(resp.text)
    assert body["error"] == "forbidden"


@pytest.mark.asyncio
async def test_admin_can_list_tokens():
    token = _make_active_token()
    data = _make_data([token])
    hass = _make_hass(data)

    view = ATMAdminTokensView()
    view.hass = hass

    request = _make_admin_request()
    resp = await view.get(request)

    assert resp.status == 200
    body = json.loads(resp.text)
    assert len(body) == 1
    assert body[0]["id"] == token.id
    assert "token_hash" not in body[0]
    assert "token" not in body[0]


@pytest.mark.asyncio
async def test_create_token_returns_raw_token_once():
    data = _make_data()
    hass = _make_hass(data)

    token = _make_active_token()
    data.store.name_slug_exists.return_value = False
    data.store.async_create_token = AsyncMock(return_value=(token, "atm_rawtoken123"))

    view = ATMAdminTokensView()
    view.hass = hass

    body = json.dumps({"name": "my-token"}).encode()
    request = _make_admin_request(body=body)
    resp = await view.post(request)

    assert resp.status == 201
    result = json.loads(resp.text)
    assert result["token"] == "atm_rawtoken123"
    assert result["id"] == token.id


@pytest.mark.asyncio
async def test_create_token_invalid_name_rejected():
    data = _make_data()
    hass = _make_hass(data)

    view = ATMAdminTokensView()
    view.hass = hass

    body = json.dumps({"name": "bad name!"}).encode()
    request = _make_admin_request(body=body)
    resp = await view.post(request)

    assert resp.status == 400
    body_parsed = json.loads(resp.text)
    assert body_parsed["error"] == "invalid_request"


@pytest.mark.asyncio
async def test_create_token_slug_collision_rejected():
    data = _make_data()
    hass = _make_hass(data)
    data.store.name_slug_exists.return_value = True

    view = ATMAdminTokensView()
    view.hass = hass

    body = json.dumps({"name": "my-token"}).encode()
    request = _make_admin_request(body=body)
    resp = await view.post(request)

    assert resp.status == 409


@pytest.mark.asyncio
async def test_create_pass_through_requires_confirmation():
    data = _make_data()
    hass = _make_hass(data)
    data.store.name_slug_exists.return_value = False

    view = ATMAdminTokensView()
    view.hass = hass

    body = json.dumps({"name": "pt-token", "pass_through": True}).encode()
    request = _make_admin_request(body=body)
    resp = await view.post(request)

    assert resp.status == 400
    body_parsed = json.loads(resp.text)
    assert "confirm_pass_through" in body_parsed["message"]


@pytest.mark.asyncio
async def test_create_pass_through_with_confirmation_succeeds():
    data = _make_data()
    hass = _make_hass(data)

    token = _make_active_token(pass_through=True)
    data.store.name_slug_exists.return_value = False
    data.store.async_create_token = AsyncMock(return_value=(token, "atm_rawtoken"))

    view = ATMAdminTokensView()
    view.hass = hass

    body = json.dumps({"name": "pt-token", "pass_through": True, "confirm_pass_through": True}).encode()
    request = _make_admin_request(body=body)
    resp = await view.post(request)

    assert resp.status == 201


@pytest.mark.asyncio
async def test_patch_token_rename_succeeds():
    token = _make_active_token(name="old-name")
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.name_slug_exists.return_value = False
    renamed = _make_active_token(name="new-name")
    renamed.id = token.id
    data.store.async_patch_token = AsyncMock(return_value=renamed)
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    body = json.dumps({"name": "new-name"}).encode()
    request = _make_admin_request(body=body)
    resp = await view.patch(request, token_id=token.id)

    assert resp.status == 200
    data.store.async_patch_token.assert_awaited_once()
    assert data.store.async_patch_token.call_args.kwargs.get("name") == "new-name"


@pytest.mark.asyncio
async def test_patch_token_rename_clash_rejected():
    token = _make_active_token(name="old-name")
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.name_slug_exists.return_value = True  # another token already uses it
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    body = json.dumps({"name": "taken-name"}).encode()
    request = _make_admin_request(body=body)
    resp = await view.patch(request, token_id=token.id)

    assert resp.status == 400


@pytest.mark.asyncio
async def test_patch_token_rename_invalid_name_rejected():
    token = _make_active_token(name="old-name")
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.name_slug_exists.return_value = False
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    body = json.dumps({"name": "no"}).encode()  # too short for TOKEN_NAME_REGEX
    request = _make_admin_request(body=body)
    resp = await view.patch(request, token_id=token.id)

    assert resp.status == 400


@pytest.mark.asyncio
async def test_patch_token_rejects_expires_at_field():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    body = json.dumps({"expires_at": "2030-01-01T00:00:00Z"}).encode()
    request = _make_admin_request(body=body)
    resp = await view.patch(request, token_id=token.id)

    assert resp.status == 400


@pytest.mark.asyncio
async def test_patch_token_pass_through_requires_confirm():
    token = _make_active_token(pass_through=False)
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    body = json.dumps({"pass_through": True}).encode()
    request = _make_admin_request(body=body)
    resp = await view.patch(request, token_id=token.id)

    assert resp.status == 400
    body_parsed = json.loads(resp.text)
    assert "confirm_pass_through" in body_parsed["message"]


@pytest.mark.asyncio
async def test_patch_token_pass_through_already_enabled_no_confirm_needed():
    token = _make_active_token(pass_through=True)
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.async_patch_token = AsyncMock(return_value=token)
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    body = json.dumps({"pass_through": True}).encode()
    request = _make_admin_request(body=body)
    resp = await view.patch(request, token_id=token.id)

    assert resp.status == 200


@pytest.mark.asyncio
async def test_patch_token_announce_all_tools_accepted():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.async_patch_token = AsyncMock(return_value=token)
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    body = json.dumps({"announce_all_tools": True}).encode()
    resp = await view.patch(_make_admin_request(body=body), token_id=token.id)

    assert resp.status == 200
    assert data.store.async_patch_token.call_args.kwargs["announce_all_tools"] is True


@pytest.mark.asyncio
async def test_patch_token_announce_all_tools_rejects_non_bool():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.async_patch_token = AsyncMock(return_value=token)
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    body = json.dumps({"announce_all_tools": "yes"}).encode()
    resp = await view.patch(_make_admin_request(body=body), token_id=token.id)

    assert resp.status == 400


@pytest.mark.asyncio
async def test_patch_uses_async_lock():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.async_patch_token = AsyncMock(return_value=token)
    hass = _make_hass(data)

    lock_acquired = []
    original_acquire = data.store.async_lock.acquire

    async def tracking_acquire():
        lock_acquired.append(True)
        return await original_acquire()

    data.store.async_lock.acquire = tracking_acquire

    view = ATMAdminTokenView()
    view.hass = hass

    body = json.dumps({"cap_restart": "allow"}).encode()
    request = _make_admin_request(body=body)
    await view.patch(request, token_id=token.id)

    assert len(lock_acquired) == 1


@pytest.mark.asyncio
async def test_delete_token_fires_revoked_event():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.async_archive_token = AsyncMock(return_value=MagicMock())
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    request = _make_admin_request()
    resp = await view.delete(request, token_id=token.id)

    assert resp.status == 204
    hass.bus.async_fire.assert_called_once()
    event_type, payload = hass.bus.async_fire.call_args[0]
    assert event_type == "atm_token_revoked"
    assert payload["token_id"] == token.id
    assert payload["revoked_by"] == "admin-user-id"


@pytest.mark.asyncio
async def test_delete_token_destroys_rate_limiter_state():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.async_archive_token = AsyncMock(return_value=MagicMock())
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    request = _make_admin_request()
    await view.delete(request, token_id=token.id)

    data.rate_limiter.destroy.assert_called_once_with(token.id)


@pytest.mark.asyncio
async def test_delete_token_cleans_token_counters():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.async_archive_token = AsyncMock(return_value=MagicMock())
    data.token_counters[token.id] = {"request_count": 10, "denied_count": 1, "rate_limit_hits": 0}
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    request = _make_admin_request()
    await view.delete(request, token_id=token.id)

    assert token.id not in data.token_counters


@pytest.mark.asyncio
async def test_delete_nonexistent_token_returns_404():
    data = _make_data()
    data.store.get_token_by_id = MagicMock(return_value=None)
    hass = _make_hass(data)

    view = ATMAdminTokenView()
    view.hass = hass

    request = _make_admin_request()
    resp = await view.delete(request, token_id="nonexistent-id")

    assert resp.status == 404


@pytest.mark.asyncio
async def test_permission_domain_patch_valid_state():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.async_patch_permission_node = AsyncMock(return_value=token)
    hass = _make_hass(data)

    view = ATMAdminPermissionDomainView()
    view.hass = hass

    body = json.dumps({"state": "GREEN"}).encode()
    request = _make_admin_request(body=body)
    resp = await view.patch(request, token_id=token.id, node_id="light")

    assert resp.status == 200
    data.store.async_patch_permission_node.assert_called_once_with(
        token.id, "domains", "light", "GREEN", None
    )


@pytest.mark.asyncio
async def test_permission_patch_invalid_state_rejected():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    hass = _make_hass(data)

    view = ATMAdminPermissionDomainView()
    view.hass = hass

    body = json.dumps({"state": "PURPLE"}).encode()
    request = _make_admin_request(body=body)
    resp = await view.patch(request, token_id=token.id, node_id="light")

    assert resp.status == 400


@pytest.mark.asyncio
async def test_permission_patch_grey_removes_node():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.async_patch_permission_node = AsyncMock(return_value=token)
    hass = _make_hass(data)

    view = ATMAdminPermissionDomainView()
    view.hass = hass

    body = json.dumps({"state": "GREY"}).encode()
    request = _make_admin_request(body=body)
    resp = await view.patch(request, token_id=token.id, node_id="light")

    assert resp.status == 200
    data.store.async_patch_permission_node.assert_called_once_with(
        token.id, "domains", "light", "GREY", None
    )


@pytest.mark.asyncio
async def test_permission_patch_with_hint():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.store.async_patch_permission_node = AsyncMock(return_value=token)
    hass = _make_hass(data)

    view = ATMAdminPermissionDomainView()
    view.hass = hass

    body = json.dumps({"state": "YELLOW", "hint": "Living room lights only"}).encode()
    request = _make_admin_request(body=body)
    await view.patch(request, token_id=token.id, node_id="light")

    data.store.async_patch_permission_node.assert_called_once_with(
        token.id, "domains", "light", "YELLOW", "Living room lights only"
    )


@pytest.mark.asyncio
async def test_settings_patch_uses_async_lock():
    data = _make_data()
    data.store.async_patch_settings = AsyncMock(return_value=GlobalSettings())
    hass = _make_hass(data)

    lock_acquired = []
    original_acquire = data.store.async_lock.acquire

    async def tracking_acquire():
        lock_acquired.append(True)
        return await original_acquire()

    data.store.async_lock.acquire = tracking_acquire

    view = ATMAdminSettingsView()
    view.hass = hass

    body = json.dumps({"notify_on_rate_limit": True}).encode()
    request = _make_admin_request(body=body)
    await view.patch(request)

    assert len(lock_acquired) == 1


@pytest.mark.asyncio
async def test_settings_patch_rejects_unknown_fields_silently():
    data = _make_data()
    settings = GlobalSettings()
    data.store.async_patch_settings = AsyncMock(return_value=settings)
    hass = _make_hass(data)

    view = ATMAdminSettingsView()
    view.hass = hass

    body = json.dumps({"notify_on_rate_limit": True, "unknown_field": "ignored"}).encode()
    request = _make_admin_request(body=body)
    resp = await view.patch(request)

    assert resp.status == 200
    call_kwargs = data.store.async_patch_settings.call_args.kwargs
    assert "unknown_field" not in call_kwargs
    assert "notify_on_rate_limit" in call_kwargs


@pytest.mark.asyncio
async def test_settings_patch_mesa_mode_valid_updates_enforcer():
    data = _make_data()
    data.store.async_patch_settings = AsyncMock(return_value=GlobalSettings(mesa_mode="enforced"))
    data.mesa = MagicMock()
    hass = _make_hass(data)

    view = ATMAdminSettingsView()
    view.hass = hass

    body = json.dumps({"mesa_mode": "enforced"}).encode()
    resp = await view.patch(_make_admin_request(body=body))

    assert resp.status == 200
    assert "mesa_mode" in data.store.async_patch_settings.call_args.kwargs
    data.mesa.set_mode.assert_called_once_with("enforced")


@pytest.mark.asyncio
async def test_settings_patch_mesa_mode_invalid_rejected():
    data = _make_data()
    data.store.async_patch_settings = AsyncMock(return_value=GlobalSettings())
    hass = _make_hass(data)

    view = ATMAdminSettingsView()
    view.hass = hass

    body = json.dumps({"mesa_mode": "yolo"}).encode()
    resp = await view.patch(_make_admin_request(body=body))

    assert resp.status == 400
    data.store.async_patch_settings.assert_not_called()


@pytest.mark.asyncio
async def test_settings_patch_mesa_inject_enables_and_syncs():
    data = _make_data()
    data.store.async_patch_settings = AsyncMock(
        return_value=GlobalSettings(mesa_inject_enabled=True)
    )
    hass = _make_hass(data)

    view = ATMAdminSettingsView()
    view.hass = hass

    body = json.dumps({"mesa_inject_enabled": True}).encode()
    with patch("custom_components.atm.panel.async_sync_mesa_inject", new=AsyncMock()) as sync:
        resp = await view.patch(_make_admin_request(body=body))

    assert resp.status == 200
    assert data.store.async_patch_settings.call_args.kwargs["mesa_inject_enabled"] is True
    sync.assert_awaited_once_with(hass)


@pytest.mark.asyncio
async def test_settings_patch_mesa_inject_non_bool_rejected():
    data = _make_data()
    data.store.async_patch_settings = AsyncMock(return_value=GlobalSettings())
    hass = _make_hass(data)

    view = ATMAdminSettingsView()
    view.hass = hass

    body = json.dumps({"mesa_inject_enabled": "yes"}).encode()
    resp = await view.patch(_make_admin_request(body=body))

    assert resp.status == 400
    data.store.async_patch_settings.assert_not_called()


@pytest.mark.asyncio
async def test_token_stats_returns_zero_counters_for_new_token():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    hass = _make_hass(data)

    view = ATMAdminTokenStatsView()
    from custom_components.atm.admin_view import ATMAdminTokenStatsView as V
    view = V()
    view.hass = hass

    request = _make_admin_request()
    resp = await view.get(request, token_id=token.id)

    assert resp.status == 200
    body = json.loads(resp.text)
    assert body["request_count"] == 0
    assert body["denied_count"] == 0
    assert body["rate_limit_hits"] == 0


@pytest.mark.asyncio
async def test_token_stats_reflects_live_counters():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.token_counters[token.id] = {"request_count": 42, "denied_count": 5, "rate_limit_hits": 2}
    hass = _make_hass(data)

    from custom_components.atm.admin_view import ATMAdminTokenStatsView
    view = ATMAdminTokenStatsView()
    view.hass = hass

    request = _make_admin_request()
    resp = await view.get(request, token_id=token.id)

    body = json.loads(resp.text)
    assert body["request_count"] == 42
    assert body["denied_count"] == 5
    assert body["rate_limit_hits"] == 2


def _connection_view():
    from custom_components.atm.admin_view import ATMAdminTokenConnectionView
    return ATMAdminTokenConnectionView()


@pytest.mark.asyncio
async def test_token_connection_no_session_zero_counters():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    hass = _make_hass(data)
    view = _connection_view()
    view.hass = hass

    resp = await view.get(_make_admin_request(), token_id=token.id)
    assert resp.status == 200
    body = json.loads(resp.text)
    assert body == {"last_used_at": None, "request_count": 0}


@pytest.mark.asyncio
async def test_token_connection_reports_request_count():
    # Streamable HTTP clients are stateless; request_count is the "connected" signal.
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    data.token_counters[token.id] = {"request_count": 3, "denied_count": 0, "rate_limit_hits": 0}
    hass = _make_hass(data)
    view = _connection_view()
    view.hass = hass

    resp = await view.get(_make_admin_request(), token_id=token.id)
    body = json.loads(resp.text)
    assert body["request_count"] == 3


@pytest.mark.asyncio
async def test_token_connection_reports_last_used_iso():
    from homeassistant.util.dt import utcnow
    token = _make_active_token()
    token.last_used_at = utcnow()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    hass = _make_hass(data)
    view = _connection_view()
    view.hass = hass

    resp = await view.get(_make_admin_request(), token_id=token.id)
    body = json.loads(resp.text)
    assert body["last_used_at"] == token.last_used_at.isoformat()


@pytest.mark.asyncio
async def test_token_connection_404_for_unknown_token():
    data = _make_data([])
    data.store.get_token_by_id = MagicMock(return_value=None)
    hass = _make_hass(data)
    view = _connection_view()
    view.hass = hass

    resp = await view.get(_make_admin_request(), token_id="nope")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_token_connection_requires_admin():
    token = _make_active_token()
    data = _make_data([token])
    data.store.get_token_by_id = MagicMock(return_value=token)
    hass = _make_hass(data)
    view = _connection_view()
    view.hass = hass

    resp = await view.get(_make_admin_request(is_admin=False), token_id=token.id)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_audit_log_query_paginates():
    data = _make_data()
    token = _make_active_token()
    data.store.get_token_by_id = MagicMock(return_value=token)
    hass = _make_hass(data)

    view = ATMAdminTokenAuditView()
    view.hass = hass

    request = _make_admin_request()
    request.query = {"limit": "50", "offset": "10"}
    await view.get(request, token_id=token.id)

    data.audit.query.assert_called_once_with(
        token_id=token.id,
        outcome=None,
        client_ip=None,
        limit=50,
        offset=10,
    )


@pytest.mark.asyncio
async def test_global_audit_query_all_tokens():
    data = _make_data()
    hass = _make_hass(data)

    view = ATMAdminAuditView()
    view.hass = hass

    request = _make_admin_request()
    request.query = {}
    await view.get(request)

    data.audit.query.assert_called_once_with(
        token_id=None,
        outcome=None,
        client_ip=None,
        limit=100,
        offset=0,
    )


@pytest.mark.asyncio
async def test_entity_tree_uses_cache():
    data = _make_data()
    cached_tree = {"light": {"devices": {}, "deviceless_entities": [], "entity_details": {}}}
    data.entity_tree_cache = cached_tree
    data.entity_tree_cache_valid = True
    hass = _make_hass(data)
    hass.states = MagicMock()

    view = ATMAdminEntityTreeView()
    view.hass = hass

    request = _make_admin_request()
    request.query = {}

    with patch("custom_components.atm.admin_view._build_entity_tree") as mock_build:
        resp = await view.get(request)

    mock_build.assert_not_called()
    assert resp.status == 200
    body = json.loads(resp.text)
    assert "light" in body


@pytest.mark.asyncio
async def test_entity_tree_rebuilds_when_invalid():
    data = _make_data()
    data.entity_tree_cache = None
    data.entity_tree_cache_valid = False
    fresh_tree = {"switch": {}}
    hass = _make_hass(data)

    view = ATMAdminEntityTreeView()
    view.hass = hass

    request = _make_admin_request()
    request.query = {}

    with patch("custom_components.atm.admin_view._build_entity_tree", new=MagicMock(return_value=fresh_tree)):
        resp = await view.get(request)

    assert resp.status == 200
    assert data.entity_tree_cache_valid is True


@pytest.mark.asyncio
async def test_entity_tree_force_reload_bypasses_cache():
    data = _make_data()
    data.entity_tree_cache = {"light": {}}
    data.entity_tree_cache_valid = True
    fresh = {"sensor": {}}
    hass = _make_hass(data)

    view = ATMAdminEntityTreeView()
    view.hass = hass

    request = _make_admin_request()
    request.query = {"force_reload": "1"}

    with patch("custom_components.atm.admin_view._build_entity_tree", new=MagicMock(return_value=fresh)):
        resp = await view.get(request)

    assert resp.status == 200
    body = json.loads(resp.text)
    assert "sensor" in body


def test_all_admin_views_exported():
    assert len(ALL_ADMIN_VIEWS) == 41


def test_archived_views_before_token_view():
    from custom_components.atm.admin_view import (
        ATMAdminArchivedTokensView,
        ATMAdminArchivedTokenView,
        ATMAdminTokenView,
    )
    archived_idx = ALL_ADMIN_VIEWS.index(ATMAdminArchivedTokensView)
    archived_single_idx = ALL_ADMIN_VIEWS.index(ATMAdminArchivedTokenView)
    token_idx = ALL_ADMIN_VIEWS.index(ATMAdminTokenView)
    assert archived_idx < token_idx
    assert archived_single_idx < token_idx


# --- global entity hints ---

def _entity_hints_view():
    from custom_components.atm.admin_view import ATMAdminEntityHintsView
    return ATMAdminEntityHintsView()


def _entity_hint_view():
    from custom_components.atm.admin_view import ATMAdminEntityHintView
    return ATMAdminEntityHintView()


@pytest.mark.asyncio
async def test_set_global_entity_hint():
    data = _make_data()
    data.store.async_set_entity_hint = AsyncMock()
    data.store.get_entity_hints.return_value = {"light.x": "note"}
    view = _entity_hint_view()
    view.hass = _make_hass(data)
    body = json.dumps({"hint": "note"}).encode()
    resp = await view.put(_make_admin_request(body=body), entity_id="light.x")
    assert resp.status == 200
    data.store.async_set_entity_hint.assert_awaited_once_with("light.x", "note")
    assert json.loads(resp.text)["entity_hints"] == {"light.x": "note"}


@pytest.mark.asyncio
async def test_clear_global_entity_hint():
    data = _make_data()
    data.store.async_set_entity_hint = AsyncMock()
    view = _entity_hint_view()
    view.hass = _make_hass(data)
    body = json.dumps({"hint": "   "}).encode()
    resp = await view.put(_make_admin_request(body=body), entity_id="light.x")
    assert resp.status == 200
    data.store.async_set_entity_hint.assert_awaited_once_with("light.x", None)


@pytest.mark.asyncio
async def test_set_global_entity_hint_invalid_entity_id():
    data = _make_data()
    view = _entity_hint_view()
    view.hass = _make_hass(data)
    body = json.dumps({"hint": "x"}).encode()
    resp = await view.put(_make_admin_request(body=body), entity_id="not-an-entity")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_set_global_entity_hint_too_long():
    data = _make_data()
    view = _entity_hint_view()
    view.hass = _make_hass(data)
    body = json.dumps({"hint": "z" * 201}).encode()
    resp = await view.put(_make_admin_request(body=body), entity_id="light.x")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_get_global_entity_hints():
    data = _make_data()
    data.store.get_entity_hints.return_value = {"light.x": "note"}
    view = _entity_hints_view()
    view.hass = _make_hass(data)
    resp = await view.get(_make_admin_request())
    assert resp.status == 200
    assert json.loads(resp.text)["entity_hints"] == {"light.x": "note"}
