"""Tests for the in-process WebSocket command dispatcher (ws_dispatch)."""

from __future__ import annotations

import pytest
from homeassistant.setup import async_setup_component

from custom_components.atm.ws_dispatch import WsDispatchError, async_ws_command


async def test_create_input_boolean_in_process(hass, hass_admin_user):
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})

    result = await async_ws_command(hass, "input_boolean/create", {"name": "ATM Test"})
    assert result["name"] == "ATM Test"
    assert "id" in result

    await hass.async_block_till_done()
    ids = [s.entity_id for s in hass.states.async_all() if s.entity_id.startswith("input_boolean.")]
    assert ids, "the created input_boolean should appear as an entity"


async def test_delete_input_boolean_in_process(hass, hass_admin_user):
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})
    created = await async_ws_command(hass, "input_boolean/create", {"name": "Temp"})
    await hass.async_block_till_done()

    await async_ws_command(hass, "input_boolean/delete", {"input_boolean_id": created["id"]})
    await hass.async_block_till_done()
    # The deleted helper should no longer exist as an entity (verified via states).
    ids = [s.entity_id for s in hass.states.async_all() if s.entity_id.startswith("input_boolean.")]
    assert not ids, "the deleted input_boolean should no longer appear as an entity"


async def test_unknown_command_raises(hass):
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})
    with pytest.raises(WsDispatchError):
        await async_ws_command(hass, "nonexistent/command", {})


async def test_command_not_on_allowlist_rejected(hass, hass_admin_user):
    # A real, registered HA command that ATM never dispatches must still be
    # refused by the allowlist guard, before any handler lookup or execution.
    # (input_boolean/list is now allowlisted for version-history capture, so a
    # freshly registered throwaway command stands in for "registered but blocked".)
    from homeassistant.components import websocket_api

    @websocket_api.websocket_command({"type": "atm_test/registered_but_blocked"})
    def _handler(hass, connection, msg):
        connection.send_result(msg["id"])

    websocket_api.async_register_command(hass, _handler)
    with pytest.raises(WsDispatchError, match="not allowed"):
        await async_ws_command(hass, "atm_test/registered_but_blocked", {})


async def test_invalid_payload_raises(hass, hass_admin_user):
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})
    # input_boolean/create requires a name; omitting it should fail schema validation.
    with pytest.raises(WsDispatchError):
        await async_ws_command(hass, "input_boolean/create", {})


async def test_handler_side_error_wrapped(hass, hass_admin_user):
    # Deleting a nonexistent item makes the handler fail; that must surface as a
    # clean WsDispatchError, not a raw HA exception (the broad-wrap hardening).
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})
    with pytest.raises(WsDispatchError):
        await async_ws_command(hass, "input_boolean/delete", {"input_boolean_id": "missing"})


async def test_result_via_send_message_bytes_is_captured(hass, hass_admin_user, monkeypatch):
    # Some handlers (notably logbook/get_events) deliver their result through
    # connection.send_message with a pre-serialized JSON result message, not via
    # send_result. _CapturingConnection must capture that too, or the dispatch
    # times out. Regression for the get_logbook timeout.
    import json

    import custom_components.atm.ws_dispatch as wd
    from homeassistant.components import websocket_api
    from homeassistant.components.websocket_api import messages

    @websocket_api.websocket_command({"type": "atm_test/send_message_result"})
    @websocket_api.async_response
    async def _handler(hass, connection, msg):
        payload = json.dumps(messages.result_message(msg["id"], {"ok": True})).encode()
        connection.send_message(payload)

    websocket_api.async_register_command(hass, _handler)
    monkeypatch.setattr(
        wd, "ALLOWED_WS_COMMANDS", wd.ALLOWED_WS_COMMANDS | {"atm_test/send_message_result"}
    )

    result = await wd.async_ws_command(hass, "atm_test/send_message_result", {})
    assert result == {"ok": True}


async def test_compat_probe_passes_on_current_ha(hass):
    from custom_components.atm.ws_dispatch import check_ws_dispatch_compat

    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {}})
    assert check_ws_dispatch_compat(hass) is None


async def test_compat_probe_reports_unsupported_required_param(hass, monkeypatch):
    # Drift detection: a NEW required constructor param ATM cannot supply (the
    # exact class of break that HA 2026.6 caused by adding `remote`).
    import custom_components.atm.ws_dispatch as wd

    class _Extra:
        def __init__(self, logger, hass, send_message, user, refresh_token, remote, mystery):
            ...

        def send_result(self): ...
        def send_error(self): ...
        def async_handle_exception(self): ...

    monkeypatch.setattr(wd, "ActiveConnection", _Extra)
    reason = wd.check_ws_dispatch_compat(hass)
    assert reason is not None and "mystery" in reason


async def test_compat_probe_tolerates_added_param_we_supply(hass, monkeypatch):
    # A new param we DO know how to supply (like `remote`) is not flagged.
    import custom_components.atm.ws_dispatch as wd

    class _WithRemote:
        def __init__(self, logger, hass, send_message, user, refresh_token, remote):
            ...

        def send_result(self): ...
        def send_error(self): ...
        def async_handle_exception(self): ...

    monkeypatch.setattr(wd, "ActiveConnection", _WithRemote)
    assert wd.check_ws_dispatch_compat(hass) is None


async def test_compat_probe_never_raises_on_introspection_failure(hass, monkeypatch):
    # Regression: on Python 3.14, inspect.signature(ActiveConnection.__init__)
    # raised NameError while evaluating a TYPE_CHECKING-only annotation, which
    # aborted ATM setup. The advisory probe must degrade to a string, never raise.
    import custom_components.atm.ws_dispatch as wd

    class _BadCode:
        co_argcount = 1
        co_kwonlyargcount = 0

        @property
        def co_varnames(self):
            raise NameError("WebSocketAdapter")

    class _Init:
        __code__ = _BadCode()

    class _Pathological:
        __init__ = _Init()

        def send_result(self): ...
        def send_error(self): ...
        def async_handle_exception(self): ...

    monkeypatch.setattr(wd, "ActiveConnection", _Pathological)
    reason = wd.check_ws_dispatch_compat(hass)
    assert isinstance(reason, str)
