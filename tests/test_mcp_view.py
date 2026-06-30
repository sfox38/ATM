"""Tests for ATM MCP endpoint."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.atm.audit import AuditLog
from custom_components.atm.const import (
    DOMAIN,
    MAX_REQUEST_BODY_BYTES,
    TOKEN_LENGTH,
    TOKEN_PREFIX,
)
from custom_components.atm.data import ATMData
from custom_components.atm.mcp_view import (
    ATMMcpContextView,
    ATMMcpView,
    _build_context_json,
    _build_context_plain,
    _dispatch_mcp,
)
from custom_components.atm.rate_limiter import RateLimiter, RateLimitResult
from custom_components.atm.token_store import PermissionNode, PermissionTree, TokenRecord, TokenStore


def _raw_token() -> str:
    return TOKEN_PREFIX + secrets.token_hex(32)


def _make_token(
    pass_through: bool = False,
    cap_restart: str = "deny",
    cap_config_read: str = "deny",
    cap_template_render: str = "deny",
    cap_automation_write: str = "deny",
    cap_script_write: str = "deny",
    cap_log_read: str = "deny",
    rate_limit_requests: int = 60,
    rate_limit_burst: int = 10,
    revoked: bool = False,
    permissions: PermissionTree | None = None,
) -> tuple[TokenRecord, str]:
    from homeassistant.util.dt import utcnow

    raw = _raw_token()
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    record = TokenRecord(
        id=str(uuid.uuid4()),
        name="test-token",
        token_hash=token_hash,
        created_at=utcnow(),
        created_by="user1",
        pass_through=pass_through,
        cap_restart=cap_restart,
        cap_config_read=cap_config_read,
        cap_template_render=cap_template_render,
        cap_automation_write=cap_automation_write,
        cap_script_write=cap_script_write,
        cap_log_read=cap_log_read,
        rate_limit_requests=rate_limit_requests,
        rate_limit_burst=rate_limit_burst,
        revoked=revoked,
        permissions=permissions or PermissionTree(),
    )
    return record, raw


def _make_data(token: TokenRecord | None = None) -> ATMData:
    store = MagicMock(spec=TokenStore)
    if token is not None:
        store.get_token_by_hash.return_value = token
    else:
        store.get_token_by_hash.return_value = None
    store.get_settings.return_value = MagicMock(
        kill_switch=False,
        disable_all_logging=False,
        log_allowed=True,
        log_denied=True,
        log_rate_limited=True,
        log_entity_names=True,
        log_client_ip=True,
        notify_on_rate_limit=False,
    )
    store.update_last_used = MagicMock()
    store.get_entity_hints.return_value = {}

    rate_limiter = MagicMock(spec=RateLimiter)
    rate_limiter.check.return_value = RateLimitResult(
        allowed=True,
        rate_limiting_enabled=True,
        limit=60,
        remaining=59,
        reset=9999999999,
        retry_after=0,
    )

    audit = MagicMock(spec=AuditLog)
    audit.record = MagicMock()

    return ATMData(
        store=store,
        rate_limiter=rate_limiter,
        audit=audit,
        rate_limit_notified={},
    )


def _make_request(
    headers: dict | None = None,
    query: dict | None = None,
    method: str = "GET",
    remote: str = "127.0.0.1",
    body: bytes = b"",
) -> MagicMock:
    req = MagicMock()
    req.method = method
    req.remote = remote
    req.headers = MagicMock()
    req.headers.get = MagicMock(side_effect=lambda k, default="": (headers or {}).get(k, default))
    req.query = query or {}
    req.content_length = None
    req.read = AsyncMock(return_value=body)
    req.content = MagicMock()
    req.content.read = AsyncMock(return_value=body)
    return req


def _make_hass(data: ATMData) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: data}
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.components = MagicMock()
    hass.states = MagicMock()
    hass.states.async_all.return_value = []
    return hass


def _make_mcp_view(data: ATMData, hass: MagicMock | None = None) -> ATMMcpView:
    view = ATMMcpView()
    view.hass = hass if hass is not None else _make_hass(data)
    return view


def _make_context_view(data: ATMData, hass: MagicMock | None = None) -> ATMMcpContextView:
    view = ATMMcpContextView()
    view.hass = hass if hass is not None else _make_hass(data)
    return view


@pytest.mark.asyncio
async def test_streamable_batch_dispatches_sequentially_and_isolates_failures():
    from custom_components.atm.mcp_view import _handle_streamable_batch

    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)
    rl = RateLimitResult(allowed=True, rate_limiting_enabled=True, limit=60, remaining=59, reset=9999999999)

    call_order: list = []

    async def fake_dispatch(method, msg_id, params, *a, **k):
        call_order.append(msg_id)
        if method == "boom":
            raise RuntimeError("explode")
        return ({"jsonrpc": "2.0", "id": msg_id, "result": {"m": method}}, None, None, None)

    items = [
        {"jsonrpc": "2.0", "id": 1, "method": "a"},
        {"jsonrpc": "2.0", "id": 2, "method": "boom"},
        {"jsonrpc": "2.0", "id": 3, "method": "c"},
    ]
    with patch("custom_components.atm.mcp_view._dispatch_mcp", side_effect=fake_dispatch):
        resp = await _handle_streamable_batch(items, token, rl, hass, data, "rid", "127.0.0.1", "http://h")

    assert resp.status == 200
    body = json.loads(resp.text)
    # Sequential, in-order dispatch (no asyncio.gather concurrency).
    assert call_order == [1, 2, 3]
    assert [r["id"] for r in body] == [1, 2, 3]
    # One item's failure is isolated as a per-item internal error, not a batch failure.
    assert body[1]["error"]["code"] == -32603
    assert body[0]["result"]["m"] == "a"
    assert body[2]["result"]["m"] == "c"


# --- token validation on POST /api/atm/mcp (Streamable HTTP) ---

@pytest.mark.asyncio
async def test_mcp_401_missing_auth_header():
    token, _ = _make_token()
    data = _make_data(token)
    view = _make_mcp_view(data)
    request = _make_request(method="POST", headers={})

    result = await view.post(request)

    from aiohttp import web
    assert isinstance(result, web.Response)
    assert result.status == 401
    data.store.get_token_by_hash.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_401_token_missing_atm_prefix():
    token, _ = _make_token()
    data = _make_data(token)
    view = _make_mcp_view(data)
    bad = "xxxx_" + secrets.token_hex(32)
    request = _make_request(method="POST", headers={"Authorization": f"Bearer {bad}"})

    result = await view.post(request)

    assert result.status == 401
    data.store.get_token_by_hash.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_401_token_wrong_length_short():
    token, _ = _make_token()
    data = _make_data(token)
    view = _make_mcp_view(data)
    short = TOKEN_PREFIX + secrets.token_hex(32)[:-1]  # 67 chars total
    assert len(short) == TOKEN_LENGTH - 1
    request = _make_request(method="POST", headers={"Authorization": f"Bearer {short}"})

    result = await view.post(request)

    assert result.status == 401
    data.store.get_token_by_hash.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_401_token_wrong_length_long():
    token, _ = _make_token()
    data = _make_data(token)
    view = _make_mcp_view(data)
    long_tok = TOKEN_PREFIX + secrets.token_hex(32) + "a"  # 69 chars total
    assert len(long_tok) == TOKEN_LENGTH + 1
    request = _make_request(method="POST", headers={"Authorization": f"Bearer {long_tok}"})

    result = await view.post(request)

    assert result.status == 401
    data.store.get_token_by_hash.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_401_llat_jwt_format_rejected():
    token, _ = _make_token()
    data = _make_data(token)
    view = _make_mcp_view(data)
    llat = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
    request = _make_request(method="POST", headers={"Authorization": f"Bearer {llat}"})

    result = await view.post(request)

    assert result.status == 401
    data.store.get_token_by_hash.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_401_token_in_query_param():
    token, raw = _make_token()
    data = _make_data(token)
    view = _make_mcp_view(data)
    request = _make_request(
        method="POST",
        headers={"Authorization": f"Bearer {raw}"},
        query={"token": raw},
    )

    result = await view.post(request)

    assert result.status == 401


@pytest.mark.asyncio
async def test_mcp_401_access_token_in_query_param():
    token, raw = _make_token()
    data = _make_data(token)
    view = _make_mcp_view(data)
    request = _make_request(
        method="POST",
        headers={"Authorization": f"Bearer {raw}"},
        query={"access_token": raw},
    )

    result = await view.post(request)

    assert result.status == 401


# --- POST /api/atm/mcp (Streamable HTTP transport) ---

@pytest.mark.asyncio
async def test_mcp_post_413_body_too_large():
    token, raw = _make_token()
    data = _make_data(token)
    view = _make_mcp_view(data)
    request = _make_request(
        method="POST",
        headers={"Authorization": f"Bearer {raw}"},
    )
    request.content_length = MAX_REQUEST_BODY_BYTES + 1

    result = await view.post(request)

    assert result.status == 413


@pytest.mark.asyncio
async def test_mcp_post_413_body_too_large_streaming():
    token, raw = _make_token()
    data = _make_data(token)
    view = _make_mcp_view(data)
    big_body = b"x" * (MAX_REQUEST_BODY_BYTES + 1)
    request = _make_request(
        method="POST",
        headers={"Authorization": f"Bearer {raw}"},
        body=big_body,
    )

    result = await view.post(request)

    assert result.status == 413


@pytest.mark.asyncio
async def test_mcp_post_initialize_returns_result_inline():
    token, raw = _make_token()
    data = _make_data(token)
    view = _make_mcp_view(data)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode()
    request = _make_request(
        method="POST",
        headers={"Authorization": f"Bearer {raw}"},
        body=body,
    )

    result = await view.post(request)

    assert result.status == 200
    payload = json.loads(result.text)
    assert payload["id"] == 1
    assert payload["result"]["protocolVersion"] == "2025-03-26"
    assert payload["result"]["serverInfo"]["name"] == "ATM"
    assert "tools" in payload["result"]["capabilities"]


@pytest.mark.asyncio
async def test_mcp_post_notification_returns_202():
    token, raw = _make_token()
    data = _make_data(token)
    view = _make_mcp_view(data)
    body = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
    request = _make_request(
        method="POST",
        headers={"Authorization": f"Bearer {raw}"},
        body=body,
    )

    result = await view.post(request)

    assert result.status == 202


# --- tools/list filtering ---

@pytest.mark.asyncio
async def test_tools_list_includes_read_tools_always():
    token, _ = _make_token()  # no write scope
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, _o = await _dispatch_mcp(
        "tools/list", 1, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    tool_names = [t["name"] for t in result["result"]["tools"]]
    for name in ("get_state", "get_states", "get_history", "get_statistics", "GetLiveContext", "GetDateTime", "get_approval_status"):
        assert name in tool_names


@pytest.mark.asyncio
async def test_initialize_includes_token_aware_instructions():
    # Channel A: the MCP initialize result carries a token-aware primer that
    # names the token's confirm-gated caps and links to the skill endpoint.
    token, _ = _make_token(cap_automation_write="confirm")
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, _o = await _dispatch_mcp(
        "initialize", 1, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    instr = result["result"]["instructions"]
    assert "http://homeassistant.local/api/atm/skill" in instr
    assert "get_capability_summary" in instr
    assert "pending_approval" in instr
    assert "cap_automation_write" in instr  # confirm-gated cap is surfaced


@pytest.mark.asyncio
async def test_skill_view_serves_markdown_unauthenticated():
    from custom_components.atm.skill_view import ATMSkillView

    view = ATMSkillView()
    assert view.requires_auth is False
    hass = MagicMock()
    data = MagicMock()
    data.shutting_down = False
    data.store.get_settings.return_value = MagicMock(kill_switch=False)
    hass.data = {DOMAIN: data}
    view.hass = hass
    resp = await view.get(MagicMock())
    assert resp.status == 200
    assert resp.content_type == "text/markdown"
    assert "ATM" in resp.text
    assert "get_approval_status" in resp.text


@pytest.mark.asyncio
async def test_tools_list_hides_control_tools_without_write_scope():
    token, _ = _make_token()  # no GREEN grants, not pass_through
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, _o = await _dispatch_mcp(
        "tools/list", 1, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    tool_names = [t["name"] for t in result["result"]["tools"]]
    for name in ("call_service", "HassTurnOn", "HassTurnOff", "HassLightSet", "HassStopMoving"):
        assert name not in tool_names


@pytest.mark.asyncio
async def test_tools_list_shows_control_tools_with_write_scope():
    token, _ = _make_token(permissions=PermissionTree(domains={"light": PermissionNode(state="GREEN")}))
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, _o = await _dispatch_mcp(
        "tools/list", 1, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    tool_names = [t["name"] for t in result["result"]["tools"]]
    for name in ("call_service", "HassTurnOn", "HassTurnOff"):
        assert name in tool_names


@pytest.mark.asyncio
async def test_tools_list_pass_through_has_write_scope():
    token, _ = _make_token(pass_through=True)
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, _o = await _dispatch_mcp(
        "tools/list", 1, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    tool_names = [t["name"] for t in result["result"]["tools"]]
    assert "call_service" in tool_names
    assert "HassTurnOn" in tool_names


@pytest.mark.asyncio
async def test_tools_list_announce_all_overrides_gating():
    # A read-only token with announce_all_tools sees the full surface.
    token, _ = _make_token()
    token.announce_all_tools = True
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, _o = await _dispatch_mcp(
        "tools/list", 1, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    tool_names = [t["name"] for t in result["result"]["tools"]]
    for name in ("call_service", "HassTurnOn", "get_config", "create_automation", "restart_ha"):
        assert name in tool_names


@pytest.mark.asyncio
async def test_tools_list_excludes_system_tools_when_flags_false():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, _o = await _dispatch_mcp(
        "tools/list", 1, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    tool_names = [t["name"] for t in result["result"]["tools"]]
    for name in ("get_config", "render_template", "create_automation", "restart_ha"):
        assert name not in tool_names


@pytest.mark.asyncio
async def test_tools_list_includes_system_tools_when_flags_true():
    token, _ = _make_token(
        cap_config_read="allow",
        cap_template_render="allow",
        cap_restart="allow",
        cap_automation_write="allow",
    )
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, _o = await _dispatch_mcp(
        "tools/list", 1, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    tool_names = [t["name"] for t in result["result"]["tools"]]
    for name in ("get_config", "render_template", "create_automation", "restart_ha"):
        assert name in tool_names


@pytest.mark.asyncio
async def test_tools_list_pass_through_non_exempt_flags_auto_granted():
    """Pass-through grants non-exempt flag tools automatically, but not exempt-flag tools."""
    token, _ = _make_token(pass_through=True)
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, _o = await _dispatch_mcp(
        "tools/list", 1, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    tool_names = [t["name"] for t in result["result"]["tools"]]
    # Non-exempt flags: pass-through grants these automatically
    for name in ("get_config", "render_template"):
        assert name in tool_names
    # Exempt flags: require explicit grant even for pass-through tokens
    for name in ("create_automation", "restart_ha"):
        assert name not in tool_names


@pytest.mark.asyncio
async def test_tools_list_pass_through_with_exempt_flags():
    """Pass-through + explicit exempt flags yields all system tools."""
    token, _ = _make_token(
        pass_through=True,
        cap_restart="allow",
        cap_automation_write="allow",
        cap_script_write="allow",
    )
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, _o = await _dispatch_mcp(
        "tools/list", 1, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    tool_names = [t["name"] for t in result["result"]["tools"]]
    for name in ("get_config", "render_template", "create_automation", "restart_ha"):
        assert name in tool_names


# --- tools/call: get_state ---

@pytest.mark.asyncio
async def test_tools_call_get_state_denied_entity_not_accessible():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)

    with patch("custom_components.atm.mcp_view.resolve") as mock_resolve:
        from custom_components.atm.policy_engine import Permission
        mock_resolve.return_value = Permission.NO_ACCESS

        result, _m, _r, outcome = await _dispatch_mcp(
            "tools/call",
            3,
            {"name": "get_state", "arguments": {"entity_id": "light.kitchen"}},
            token,
            hass,
            data,
            "127.0.0.1",
            base_url="http://homeassistant.local"
        )

    assert outcome == "denied"
    content = result["result"]["content"][0]["text"]
    assert "not found" in content.lower()
    assert result["result"].get("isError") is True


@pytest.mark.asyncio
async def test_tools_call_get_state_success():
    token, _ = _make_token()
    data = _make_data(token)

    state_mock = MagicMock()
    state_mock.entity_id = "light.kitchen"
    state_mock.state = "on"
    state_mock.attributes = {"brightness": 255}

    hass = _make_hass(data)
    hass.states.get.return_value = state_mock

    with patch("custom_components.atm.mcp_view.resolve") as mock_resolve:
        from custom_components.atm.policy_engine import Permission
        mock_resolve.return_value = Permission.WRITE

        with patch("custom_components.atm.mcp_view.scrub_sensitive_attributes") as mock_scrub:
            mock_scrub.return_value = {"entity_id": "light.kitchen", "state": "on", "attributes": {}}

            result, _m, _r, outcome = await _dispatch_mcp(
                "tools/call",
                3,
                {"name": "get_state", "arguments": {"entity_id": "light.kitchen"}},
                token,
                hass,
                data,
                "127.0.0.1",
                base_url="http://homeassistant.local"
            )

    assert outcome == "allowed"
    content = result["result"]["content"][0]["text"]
    payload = json.loads(content)
    assert payload["entity_id"] == "light.kitchen"


@pytest.mark.asyncio
async def test_tools_call_get_state_not_found_entity_same_response_as_denied():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)

    with patch("custom_components.atm.mcp_view.resolve") as mock_resolve:
        from custom_components.atm.policy_engine import Permission
        mock_resolve.return_value = Permission.NOT_FOUND

        result_nf, _m, _r, outcome_nf = await _dispatch_mcp(
            "tools/call",
            3,
            {"name": "get_state", "arguments": {"entity_id": "light.ghost"}},
            token,
            hass,
            data,
            "127.0.0.1",
            base_url="http://homeassistant.local"
        )

    with patch("custom_components.atm.mcp_view.resolve") as mock_resolve:
        mock_resolve.return_value = Permission.DENY

        result_denied, _m, _r, outcome_denied = await _dispatch_mcp(
            "tools/call",
            4,
            {"name": "get_state", "arguments": {"entity_id": "light.ghost"}},
            token,
            hass,
            data,
            "127.0.0.1",
            base_url="http://homeassistant.local"
        )

    nf_text = result_nf["result"]["content"][0]["text"]
    denied_text = result_denied["result"]["content"][0]["text"]
    assert nf_text == denied_text


# --- get_state / get_states field projection (v2.1) ---

from custom_components.atm.mcp_view import (  # noqa: E402
    _normalize_fields,
    _select_state_fields,
    _lean_state,
    _project_state,
)


def _full_light_dict():
    return {
        "entity_id": "light.kitchen",
        "state": "on",
        "attributes": {
            "friendly_name": "Kitchen",
            "brightness": 200,
            "color_temp_kelvin": 3000,
            "supported_features": 44,   # not domain-important -> dropped in lean
            "icon": "mdi:lamp",         # not domain-important -> dropped in lean
        },
        "last_changed": "2026-06-29T00:00:00+00:00",
        "last_updated": "2026-06-29T00:00:00+00:00",
        "context": {"id": "abc", "user_id": None, "parent_id": None},
    }


def test_normalize_fields_accepts_list_csv_and_rejects_garbage():
    assert _normalize_fields(["state", " attr.brightness "]) == ["state", "attr.brightness"]
    assert _normalize_fields("state, attr.brightness") == ["state", "attr.brightness"]
    assert _normalize_fields(None) == []
    assert _normalize_fields(123) == []
    assert _normalize_fields([]) == []


def test_lean_state_keeps_base_plus_domain_attrs_only():
    lean = _lean_state(_full_light_dict())
    assert lean["entity_id"] == "light.kitchen"
    assert lean["state"] == "on"
    assert lean["attributes"]["friendly_name"] == "Kitchen"
    assert lean["attributes"]["brightness"] == 200
    assert lean["attributes"]["color_temp_kelvin"] == 3000
    assert "supported_features" not in lean["attributes"]
    assert "icon" not in lean["attributes"]
    # heavy top-level fields dropped in lean
    assert "context" not in lean
    assert "last_updated" not in lean


def test_lean_state_unknown_domain_base_only():
    d = {"entity_id": "weird.thing", "state": "x", "attributes": {"friendly_name": "W", "foo": 1}}
    lean = _lean_state(d)
    assert lean["attributes"] == {"friendly_name": "W"}
    assert "foo" not in lean["attributes"]


def test_select_state_fields_topmost_attr_and_all():
    d = _full_light_dict()
    sel = _select_state_fields(d, ["state", "attr.brightness", "last_changed"])
    assert sel == {
        "entity_id": "light.kitchen",
        "state": "on",
        "last_changed": "2026-06-29T00:00:00+00:00",
        "attributes": {"brightness": 200},
    }
    sel_all = _select_state_fields(d, ["attributes"])
    assert sel_all["attributes"] == d["attributes"]
    sel_unknown = _select_state_fields(d, ["nope", "attr.nope"])
    assert sel_unknown == {"entity_id": "light.kitchen"}


def test_select_state_fields_cannot_resurrect_scrubbed_attr():
    # access_token is already scrubbed out before projection; requesting it returns nothing.
    d = {"entity_id": "camera.front", "state": "idle", "attributes": {"friendly_name": "Front"}}
    sel = _select_state_fields(d, ["attr.access_token"])
    assert sel == {"entity_id": "camera.front"}
    assert "attributes" not in sel


def test_project_state_modes():
    d = _full_light_dict()
    assert _project_state(d, None, True) is d  # detailed -> full as-is
    assert _project_state(d, ["state"], False) == {"entity_id": "light.kitchen", "state": "on"}
    lean = _project_state(d, None, False)
    assert "supported_features" not in lean["attributes"]


@pytest.mark.asyncio
async def test_tools_call_get_state_lean_default_and_detailed():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)
    hass.states.get.return_value = MagicMock()
    full = {
        "entity_id": "light.kitchen",
        "state": "on",
        "attributes": {"friendly_name": "K", "brightness": 5, "icon": "mdi:x"},
        "last_updated": "t",
        "context": {"id": "c"},
    }
    with patch("custom_components.atm.mcp_view.resolve") as mock_resolve:
        from custom_components.atm.policy_engine import Permission
        mock_resolve.return_value = Permission.WRITE
        with patch("custom_components.atm.mcp_view.scrub_sensitive_attributes", return_value=full):
            res_lean, _m, _r, out_lean = await _dispatch_mcp(
                "tools/call", 3,
                {"name": "get_state", "arguments": {"entity_id": "light.kitchen"}},
                token, hass, data, "127.0.0.1", base_url="http://h",
            )
            res_full, _m2, _r2, out_full = await _dispatch_mcp(
                "tools/call", 4,
                {"name": "get_state", "arguments": {"entity_id": "light.kitchen", "detailed": True}},
                token, hass, data, "127.0.0.1", base_url="http://h",
            )
    assert out_lean == "allowed" and out_full == "allowed"
    lean = json.loads(res_lean["result"]["content"][0]["text"])
    assert lean["attributes"]["brightness"] == 5
    assert "icon" not in lean["attributes"]
    assert "context" not in lean
    full_out = json.loads(res_full["result"]["content"][0]["text"])
    assert full_out["attributes"]["icon"] == "mdi:x"
    assert "context" in full_out


# --- get_logbook / get_calendar_events (v2.1) ---

@pytest.mark.asyncio
async def test_get_logbook_forbidden_without_cap():
    token, _ = _make_token(cap_log_read="deny")
    data = _make_data(token)
    hass = _make_hass(data)
    res, _m, _r, outcome = await _dispatch_mcp(
        "tools/call", 3, {"name": "get_logbook", "arguments": {}},
        token, hass, data, "127.0.0.1", base_url="http://h",
    )
    assert outcome == "denied"
    assert res["result"].get("isError") is True


@pytest.mark.asyncio
async def test_get_logbook_scopes_to_accessible_entities():
    token, _ = _make_token(cap_log_read="allow")
    data = _make_data(token)
    hass = _make_hass(data)
    from custom_components.atm.policy_engine import Permission

    def _res(eid, tok, h):
        return Permission.READ if eid == "light.ok" else Permission.NO_ACCESS

    entries = [
        {"entity_id": "light.ok", "message": "turned on"},
        {"entity_id": "light.secret", "message": "turned off"},
        {"name": "Some event", "message": "no entity id"},
    ]
    # _logbook_entry_visible (mcp_view.resolve) does the scoping under test; the
    # extra filter_service_response redaction pass has its own coverage, so stub it
    # to identity here (it would otherwise hit a real entity registry on the mock).
    with patch("custom_components.atm.mcp_view.resolve", side_effect=_res), \
         patch("custom_components.atm.mcp_view.filter_service_response", side_effect=lambda d, t, h: d), \
         patch("custom_components.atm.mcp_view.async_ws_command", new=AsyncMock(return_value=entries)):
        res, _m, _r, outcome = await _dispatch_mcp(
            "tools/call", 3, {"name": "get_logbook", "arguments": {}},
            token, hass, data, "127.0.0.1", base_url="http://h",
        )
    assert outcome == "allowed"
    payload = json.loads(res["result"]["content"][0]["text"])
    assert payload["count"] == 1
    assert payload["entries"][0]["entity_id"] == "light.ok"


@pytest.mark.asyncio
async def test_get_calendar_events_returns_events_for_accessible_calendar():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)
    hass.services.async_call = AsyncMock(return_value={"calendar.fam": {"events": [{"summary": "Dentist"}]}})
    from custom_components.atm.policy_engine import Permission
    with patch("custom_components.atm.mcp_view.resolve", return_value=Permission.READ):
        res, _m, _r, outcome = await _dispatch_mcp(
            "tools/call", 3,
            {"name": "get_calendar_events", "arguments": {"calendar_id": "calendar.fam"}},
            token, hass, data, "127.0.0.1", base_url="http://h",
        )
    assert outcome == "allowed"
    payload = json.loads(res["result"]["content"][0]["text"])
    assert payload["calendar_id"] == "calendar.fam"
    assert payload["events"][0]["summary"] == "Dentist"


@pytest.mark.asyncio
async def test_get_calendar_events_rejects_non_calendar_entity():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)
    from custom_components.atm.policy_engine import Permission
    with patch("custom_components.atm.mcp_view.resolve", return_value=Permission.READ):
        res, _m, _r, outcome = await _dispatch_mcp(
            "tools/call", 3,
            {"name": "get_calendar_events", "arguments": {"calendar_id": "light.kitchen"}},
            token, hass, data, "127.0.0.1", base_url="http://h",
        )
    assert outcome == "invalid_request"


@pytest.mark.asyncio
async def test_get_calendar_events_not_found_when_inaccessible():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)
    from custom_components.atm.policy_engine import Permission
    with patch("custom_components.atm.mcp_view.resolve", return_value=Permission.NO_ACCESS):
        res, _m, _r, outcome = await _dispatch_mcp(
            "tools/call", 3,
            {"name": "get_calendar_events", "arguments": {"calendar_id": "calendar.fam"}},
            token, hass, data, "127.0.0.1", base_url="http://h",
        )
    assert outcome == "denied"


# --- list_blueprints (v2.1) ---

@pytest.mark.asyncio
async def test_list_blueprints_forbidden_without_cap():
    token, _ = _make_token(cap_config_read="deny")
    data = _make_data(token)
    hass = _make_hass(data)
    res, _m, _r, outcome = await _dispatch_mcp(
        "tools/call", 3, {"name": "list_blueprints", "arguments": {}},
        token, hass, data, "127.0.0.1", base_url="http://h",
    )
    assert outcome == "denied"
    assert res["result"].get("isError") is True


@pytest.mark.asyncio
async def test_list_blueprints_lists_with_inputs():
    token, _ = _make_token(cap_config_read="allow")
    data = _make_data(token)
    hass = _make_hass(data)
    bp = MagicMock()
    bp.metadata = {"name": "Motion Light", "description": "d", "input": {"motion": {}}, "source_url": "u"}
    dom_bp = MagicMock()
    dom_bp.async_get_blueprints = AsyncMock(return_value={"author/motion.yaml": bp})
    with patch("homeassistant.components.automation.async_get_blueprints", return_value=dom_bp), \
         patch("homeassistant.components.script.async_get_blueprints", return_value=dom_bp):
        res, _m, _r, outcome = await _dispatch_mcp(
            "tools/call", 3, {"name": "list_blueprints", "arguments": {"domain": "automation"}},
            token, hass, data, "127.0.0.1", base_url="http://h",
        )
    assert outcome == "allowed"
    payload = json.loads(res["result"]["content"][0]["text"])
    assert payload["count"] == 1
    row = payload["blueprints"][0]
    assert row["name"] == "Motion Light"
    assert row["domain"] == "automation"
    assert row["path"] == "author/motion.yaml"
    assert row["input"] == {"motion": {}}


# --- tools/call: restart_ha dual-gate ---

@pytest.mark.asyncio
async def test_tools_call_restart_ha_denied_without_cap_restart():
    token, _ = _make_token(cap_restart="deny", pass_through=False)
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, outcome = await _dispatch_mcp(
        "tools/call",
        5,
        {"name": "restart_ha", "arguments": {}},
        token,
        hass,
        data,
        "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    assert outcome == "denied"
    assert result["result"].get("isError") is True


@pytest.mark.asyncio
async def test_tools_call_restart_ha_denied_for_pass_through_without_cap_restart():
    token, _ = _make_token(cap_restart="deny", pass_through=True)
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, outcome = await _dispatch_mcp(
        "tools/call",
        6,
        {"name": "restart_ha", "arguments": {}},
        token,
        hass,
        data,
        "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    assert outcome == "denied"
    assert result["result"].get("isError") is True


# --- tools/call: automation stubs ---

@pytest.mark.asyncio
async def test_tools_call_create_automation_invalid_config_rejected():
    """Empty config dict fails HA validation and returns invalid_request."""
    token, _ = _make_token(cap_automation_write="allow")
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, outcome = await _dispatch_mcp(
        "tools/call",
        7,
        {"name": "create_automation", "arguments": {"config": {}}},
        token,
        hass,
        data,
        "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    assert outcome == "invalid_request"
    assert result["result"].get("isError") is True


@pytest.mark.asyncio
async def test_tools_call_automation_denied_without_flag():
    token, _ = _make_token(cap_automation_write="deny")
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, outcome = await _dispatch_mcp(
        "tools/call",
        8,
        {"name": "delete_automation", "arguments": {"automation_id": "abc"}},
        token,
        hass,
        data,
        "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    assert outcome == "denied"
    assert result["result"].get("isError") is True


# --- resources ---

@pytest.mark.asyncio
async def test_resources_list_returns_server_info():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, outcome = await _dispatch_mcp(
        "resources/list", 9, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    assert outcome == "allowed"
    resources = result["result"]["resources"]
    assert any(r["uri"] == "atm://server-info" for r in resources)


@pytest.mark.asyncio
async def test_resources_read_server_info():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)
    hass.states.async_all.return_value = []

    result, _m, _r, outcome = await _dispatch_mcp(
        "resources/read", 10, {"uri": "atm://server-info"}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    assert outcome == "allowed"
    contents = result["result"]["contents"]
    assert len(contents) == 1
    payload = json.loads(contents[0]["text"])
    assert payload["token_name"] == token.name
    assert "capability_flags" in payload


@pytest.mark.asyncio
async def test_resources_read_unknown_uri_returns_error():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, outcome = await _dispatch_mcp(
        "resources/read", 11, {"uri": "atm://nonexistent"}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    assert outcome == "denied"
    assert "error" in result


# --- unknown method ---

@pytest.mark.asyncio
async def test_unknown_method_with_id_returns_jsonrpc_error():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, outcome = await _dispatch_mcp(
        "nonexistent/method", 99, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    assert outcome == "not_implemented"
    assert result is not None
    assert "error" in result
    assert result["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_notification_no_id_returns_no_response():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)

    result, _m, _r, _o = await _dispatch_mcp(
        "notifications/initialized", None, {}, token, hass, data, "127.0.0.1",
        base_url="http://homeassistant.local"
    )

    assert result is None


# --- context endpoint ---

def _make_token_with_permissions(entities: dict[str, PermissionNode]) -> tuple[TokenRecord, str]:
    tree = PermissionTree(entities=entities)
    return _make_token(permissions=tree)


def _make_hass_with_states(data: ATMData, entity_ids: list[str]) -> MagicMock:
    hass = _make_hass(data)
    states = []
    for eid in entity_ids:
        s = MagicMock()
        s.entity_id = eid
        states.append(s)
    hass.states.async_all.return_value = states
    hass.states.get = MagicMock(side_effect=lambda eid: next((s for s in states if s.entity_id == eid), None))
    return hass


@pytest.mark.asyncio
async def test_context_plain_entity_with_hint_appears():
    token, raw = _make_token_with_permissions({
        "light.kitchen": PermissionNode(state="GREEN", hint="The main kitchen light")
    })
    data = _make_data(token)

    with patch("custom_components.atm.mcp_view.resolve") as mock_resolve, \
         patch("custom_components.atm.mcp_view.get_effective_hint", return_value="The main kitchen light"):
        from custom_components.atm.policy_engine import Permission

        def resolve_side_effect(eid, tok, hass):
            if eid == "light.kitchen":
                return Permission.WRITE
            return Permission.NO_ACCESS

        mock_resolve.side_effect = resolve_side_effect
        hass = _make_hass_with_states(data, ["light.kitchen"])
        text = _build_context_plain(token, hass)

    assert "light.kitchen" in text
    assert "The main kitchen light" in text
    assert "READ/WRITE" in text


@pytest.mark.asyncio
async def test_context_plain_entity_without_hint_renders_normally():
    token, raw = _make_token_with_permissions({
        "light.kitchen": PermissionNode(state="GREEN", hint=None)
    })
    data = _make_data(token)

    with patch("custom_components.atm.mcp_view.resolve") as mock_resolve, \
         patch("custom_components.atm.mcp_view.get_effective_hint", return_value=None):
        from custom_components.atm.policy_engine import Permission

        def resolve_side_effect(eid, tok, hass):
            if eid == "light.kitchen":
                return Permission.WRITE
            return Permission.NO_ACCESS

        mock_resolve.side_effect = resolve_side_effect
        hass = _make_hass_with_states(data, ["light.kitchen"])
        text = _build_context_plain(token, hass)

    assert "light.kitchen" in text
    assert '"' not in text.split("light.kitchen")[1].split("\n")[0]


@pytest.mark.asyncio
async def test_context_plain_read_permission_shows_read_only():
    token, _ = _make_token_with_permissions({
        "sensor.temp": PermissionNode(state="YELLOW")
    })
    data = _make_data(token)

    with patch("custom_components.atm.mcp_view.resolve") as mock_resolve, \
         patch("custom_components.atm.mcp_view.get_effective_hint", return_value=None):
        from custom_components.atm.policy_engine import Permission
        mock_resolve.return_value = Permission.READ
        hass = _make_hass_with_states(data, ["sensor.temp"])
        text = _build_context_plain(token, hass)

    assert "sensor.temp (READ)" in text
    assert "READ/WRITE" not in text


@pytest.mark.asyncio
async def test_context_json_entity_with_hint():
    token, _ = _make_token_with_permissions({
        "switch.relay_1": PermissionNode(state="GREEN", hint="Holiday lights power switch")
    })
    data = _make_data(token)

    with patch("custom_components.atm.mcp_view.resolve") as mock_resolve, \
         patch("custom_components.atm.mcp_view.get_effective_hint", return_value="Holiday lights power switch"):
        from custom_components.atm.policy_engine import Permission
        mock_resolve.return_value = Permission.WRITE

        with patch("custom_components.atm.mcp_view.er") as mock_er_mod:
            with patch("custom_components.atm.mcp_view.dr") as mock_dr_mod:
                mock_registry = MagicMock()
                mock_registry.async_get.return_value = None
                mock_er_mod.async_get.return_value = mock_registry

                mock_dev_reg = MagicMock()
                mock_dr_mod.async_get.return_value = mock_dev_reg

                hass = _make_hass_with_states(data, ["switch.relay_1"])
                payload = _build_context_json(token, hass)

    entity_entry = next(e for e in payload["entities"] if e["entity_id"] == "switch.relay_1")
    assert entity_entry["hint"] == "Holiday lights power switch"
    assert entity_entry["permission"] == "READ/WRITE"


@pytest.mark.asyncio
async def test_context_json_entity_without_hint_has_no_hint_key():
    token, _ = _make_token_with_permissions({
        "light.hall": PermissionNode(state="GREEN", hint=None)
    })
    data = _make_data(token)

    with patch("custom_components.atm.mcp_view.resolve") as mock_resolve, \
         patch("custom_components.atm.mcp_view.get_effective_hint", return_value=None):
        from custom_components.atm.policy_engine import Permission
        mock_resolve.return_value = Permission.WRITE

        with patch("custom_components.atm.mcp_view.er") as mock_er_mod:
            with patch("custom_components.atm.mcp_view.dr") as mock_dr_mod:
                mock_registry = MagicMock()
                mock_registry.async_get.return_value = None
                mock_er_mod.async_get.return_value = mock_registry

                mock_dev_reg = MagicMock()
                mock_dr_mod.async_get.return_value = mock_dev_reg

                hass = _make_hass_with_states(data, ["light.hall"])
                payload = _build_context_json(token, hass)

    entity_entry = next(e for e in payload["entities"] if e["entity_id"] == "light.hall")
    assert "hint" not in entity_entry


@pytest.mark.asyncio
async def test_context_json_includes_capability_flags():
    token, _ = _make_token(cap_config_read="allow", cap_restart="allow")
    data = _make_data(token)

    with patch("custom_components.atm.mcp_view.resolve") as mock_resolve:
        from custom_components.atm.policy_engine import Permission
        mock_resolve.return_value = Permission.NO_ACCESS

        with patch("custom_components.atm.mcp_view.er") as mock_er_mod:
            with patch("custom_components.atm.mcp_view.dr") as mock_dr_mod:
                mock_er_mod.async_get.return_value = MagicMock()
                mock_dr_mod.async_get.return_value = MagicMock()

                hass = _make_hass_with_states(data, [])
                payload = _build_context_json(token, hass)

    assert payload["capability_flags"]["cap_config_read"] == "allow"
    assert payload["capability_flags"]["cap_restart"] == "allow"
    assert payload["capability_flags"]["cap_template_render"] == "deny"


@pytest.mark.asyncio
async def test_context_endpoint_returns_plain_text_by_default():
    token, raw = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)

    view = _make_context_view(data, hass)
    request = _make_request(headers={"Authorization": f"Bearer {raw}"})

    result = await view.get(request)

    assert result.status == 200
    assert "text/plain" in result.content_type


@pytest.mark.asyncio
async def test_context_endpoint_returns_json_with_format_param():
    token, raw = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)

    view = _make_context_view(data, hass)
    request = _make_request(
        headers={"Authorization": f"Bearer {raw}"},
        query={"format": "json"},
    )

    with patch("custom_components.atm.mcp_view.er") as mock_er_mod:
        with patch("custom_components.atm.mcp_view.dr") as mock_dr_mod:
            mock_er_mod.async_get.return_value = MagicMock()
            mock_dr_mod.async_get.return_value = MagicMock()
            result = await view.get(request)

    assert result.status == 200
    assert "application/json" in result.content_type
    payload = json.loads(result.text)
    assert "entities" in payload
    assert "capability_flags" in payload


@pytest.mark.asyncio
async def test_context_endpoint_401_without_auth():
    token, _ = _make_token()
    data = _make_data(token)
    hass = _make_hass(data)

    view = _make_context_view(data, hass)
    request = _make_request(headers={})

    result = await view.get(request)

    assert result.status == 401


# ---- HassTurnOn/Off physical-control gating ----------------------------------


def _make_physical_token(cap_physical_control: str) -> TokenRecord:
    from homeassistant.util.dt import utcnow

    raw = _raw_token()
    return TokenRecord(
        id=str(uuid.uuid4()),
        name="phys-token",
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        created_at=utcnow(),
        created_by="user1",
        cap_physical_control=cap_physical_control,
        permissions=PermissionTree(),
    )


def _fake_action_done(calls):
    async def _fake_intent_action(tool, domain, service, sd, entities, h, token=None, args=None):
        calls.append((domain, service, tuple(entities)))
        body = json.dumps({
            "speech": {}, "response_type": "action_done",
            "data": {"success": [{"id": e, "type": "entity"} for e in entities], "failed": []},
        })
        return ({"content": [{"type": "text", "text": body}]}, "allowed", tool)
    return _fake_intent_action


@pytest.mark.asyncio
async def test_turn_on_allow_includes_physical_entities():
    from custom_components.atm.mcp_view import _tool_hass_turn_on

    token = _make_physical_token("allow")
    data = _make_data(token)
    hass = _make_hass(data)
    calls: list = []

    with patch("custom_components.atm.mcp_view.resolve_intent_entities",
               return_value=["light.kitchen", "lock.front_door"]), \
         patch("custom_components.atm.mcp_view._tool_intent_action", side_effect=_fake_action_done(calls)), \
         patch("custom_components.atm.mcp_view._gate", new=AsyncMock(return_value=None)) as gate:
        result = await _tool_hass_turn_on({"area": "Kitchen"}, token, hass, data, "rid", None)

    # The light goes via homeassistant.turn_on; the lock must go via lock.lock,
    # because homeassistant.turn_on cannot operate a lock on current HA.
    assert ("homeassistant", "turn_on", ("light.kitchen",)) in calls
    assert ("lock", "lock", ("lock.front_door",)) in calls
    merged = json.loads(result[0]["content"][0]["text"])
    assert {e["id"] for e in merged["data"]["success"]} == {"light.kitchen", "lock.front_door"}
    gate.assert_not_called()


@pytest.mark.asyncio
async def test_turn_on_deny_strips_physical_entities():
    from custom_components.atm.mcp_view import _tool_hass_turn_on

    token = _make_physical_token("deny")
    data = _make_data(token)
    hass = _make_hass(data)
    captured = {}

    async def _fake_intent_action(tool, domain, service, sd, entities, h, token=None, args=None):
        captured["entities"] = entities
        return ({"content": []}, "allowed", tool)

    with patch("custom_components.atm.mcp_view.resolve_intent_entities",
               return_value=["light.kitchen", "lock.front_door"]), \
         patch("custom_components.atm.mcp_view._tool_intent_action", side_effect=_fake_intent_action), \
         patch("custom_components.atm.mcp_view._gate", new=AsyncMock(return_value=None)) as gate:
        await _tool_hass_turn_on({"area": "Kitchen"}, token, hass, data, "rid", None)

    assert captured["entities"] == ["light.kitchen"]
    gate.assert_not_called()


@pytest.mark.asyncio
async def test_turn_on_confirm_with_physical_creates_pending():
    from custom_components.atm.mcp_view import _tool_hass_turn_on

    token = _make_physical_token("confirm")
    data = _make_data(token)
    hass = _make_hass(data)
    pending = ({"content": [], "_pending": True}, "pending_approval", "approval:HassTurnOn:x")
    intent = AsyncMock()

    with patch("custom_components.atm.mcp_view.resolve_intent_entities",
               return_value=["light.kitchen", "lock.front_door"]), \
         patch("custom_components.atm.mcp_view._tool_intent_action", new=intent), \
         patch("custom_components.atm.mcp_view._gate", new=AsyncMock(return_value=pending)) as gate:
        result = await _tool_hass_turn_on({"area": "Kitchen"}, token, hass, data, "rid", None)

    assert result == pending
    gate.assert_awaited_once()
    # The action must NOT fire while the request is pending approval.
    intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_turn_on_confirm_without_physical_fires_immediately():
    from custom_components.atm.mcp_view import _tool_hass_turn_on

    token = _make_physical_token("confirm")
    data = _make_data(token)
    hass = _make_hass(data)
    captured = {}

    async def _fake_intent_action(tool, domain, service, sd, entities, h, token=None, args=None):
        captured["entities"] = entities
        return ({"content": []}, "allowed", tool)

    with patch("custom_components.atm.mcp_view.resolve_intent_entities",
               return_value=["light.kitchen", "switch.fan"]), \
         patch("custom_components.atm.mcp_view._tool_intent_action", side_effect=_fake_intent_action), \
         patch("custom_components.atm.mcp_view._gate", new=AsyncMock(return_value=None)) as gate:
        await _tool_hass_turn_on({"area": "Kitchen"}, token, hass, data, "rid", None)

    assert captured["entities"] == ["light.kitchen", "switch.fan"]
    gate.assert_not_called()


@pytest.mark.asyncio
async def test_execute_turn_on_includes_physical_under_confirm():
    """Approved executors must actuate physical locks through lock.lock."""
    from custom_components.atm.mcp_view import _execute_hass_turn_on

    token = _make_physical_token("confirm")
    data = _make_data(token)
    hass = _make_hass(data)
    calls: list = []

    with patch("custom_components.atm.mcp_view.resolve_intent_entities",
               return_value=["light.kitchen", "lock.front_door"]), \
         patch("custom_components.atm.mcp_view._tool_intent_action", side_effect=_fake_action_done(calls)):
        await _execute_hass_turn_on({"area": "Kitchen"}, token, hass, data)

    assert ("lock", "lock", ("lock.front_door",)) in calls


def test_turn_service_groups_routes_lock_and_cover():
    """Lock and cover turn actions route through domain services."""
    from custom_components.atm.mcp_view import _turn_service_groups

    on = {(d, s): e for d, s, e in _turn_service_groups("turn_on", ["light.k", "lock.f", "cover.g"])}
    assert on[("homeassistant", "turn_on")] == ["light.k"]
    assert on[("lock", "lock")] == ["lock.f"]
    assert on[("cover", "open_cover")] == ["cover.g"]

    off = {(d, s): e for d, s, e in _turn_service_groups("turn_off", ["lock.f", "cover.g"])}
    assert off[("lock", "unlock")] == ["lock.f"]
    assert off[("cover", "close_cover")] == ["cover.g"]


@pytest.mark.asyncio
async def test_turn_on_off_registered_as_executors():
    from custom_components.atm.mcp_view import _EXECUTOR_REGISTRY

    assert "HassTurnOn" in _EXECUTOR_REGISTRY
    assert "HassTurnOff" in _EXECUTOR_REGISTRY
