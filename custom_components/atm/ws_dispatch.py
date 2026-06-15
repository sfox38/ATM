"""In-process dispatch of Home Assistant WebSocket API commands.

Some HA capabilities (notably helper CRUD: input_boolean/create, etc.) are only
exposed through the WebSocket command API. There is no public host-side function
and the underlying storage collections are not reachable. This module invokes
HA's already-registered WS command handlers directly, in-process, with no socket
and no long-lived access token: it looks up the handler in
``hass.data["websocket_api"]``, validates the message against the registered
schema, and runs the handler against a synthetic ``ActiveConnection`` that
captures the result instead of writing to a socket.

This is the one place that leans on HA internals (`ActiveConnection`, the
`async_response` result flow). It is deliberately isolated here, with a version
shim and tests, so an HA change breaks this module loudly rather than the
callers. The create/update/delete commands are `@require_admin`, so dispatch
runs under a real admin user resolved from `hass.auth`; ATM's own capability
gate (e.g. cap_helper_write + Confirm + audit) decides whether a call runs at
all, the same way create_automation performs a privileged file write under a cap.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.websocket_api import const as ws_const
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0


class WsDispatchError(Exception):
    """Raised when an in-process WS command cannot be dispatched or fails."""


class _CapturingConnection(ActiveConnection):
    """An ActiveConnection that captures the command result in a Future.

    Overrides the three result sinks the storage-collection handlers use
    (send_result on success, send_error on validation failure, and
    async_handle_exception on an unexpected error) so the dispatched command's
    outcome can be awaited instead of serialized onto a socket.
    """

    def __init__(self, hass: HomeAssistant, user: Any) -> None:
        super().__init__(_LOGGER, hass, self._noop_send, user, None)
        self.result_future: asyncio.Future = hass.loop.create_future()

    @staticmethod
    def _noop_send(_message: Any) -> None:
        return None

    @callback
    def send_result(self, msg_id: int, result: Any | None = None) -> None:
        if not self.result_future.done():
            self.result_future.set_result(result)

    @callback
    def send_error(self, msg_id: int, code: str, message: str, *args: Any, **kwargs: Any) -> None:
        if not self.result_future.done():
            self.result_future.set_exception(WsDispatchError(f"{code}: {message}"))

    @callback
    def async_handle_exception(self, msg: dict, err: Exception) -> None:
        if not self.result_future.done():
            self.result_future.set_exception(err)


async def _resolve_admin_user(hass: HomeAssistant) -> Any:
    """Return a real active admin user (owner preferred) for require_admin commands."""
    users = await hass.auth.async_get_users()
    active_admins = [u for u in users if u.is_active and u.is_admin and not u.system_generated]
    if not active_admins:
        raise WsDispatchError("No active admin user is available to run this command.")
    return next((u for u in active_admins if u.is_owner), active_admins[0])


async def async_ws_command(
    hass: HomeAssistant,
    command: str,
    payload: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Invoke a registered HA WebSocket command in-process and return its result.

    Raises WsDispatchError if the command is not registered, the payload fails
    the command's schema, no admin user is available, or the handler reports an
    error / times out.
    """
    handlers = hass.data.get(ws_const.DOMAIN)
    if not handlers or command not in handlers:
        raise WsDispatchError(f"WebSocket command not available: {command}")
    handler, schema = handlers[command]

    msg: dict[str, Any] = {"id": 1, "type": command, **payload}
    if schema not in (None, False):
        try:
            msg = schema(msg)
        except vol.Invalid as err:
            raise WsDispatchError(f"Invalid arguments for {command}: {err}") from err

    user = await _resolve_admin_user(hass)

    # Everything from here leans on HA internals: ActiveConnection construction,
    # the require_admin/async_response decorators, and the handler delivering its
    # outcome through the connection. Wrap any unexpected failure (a changed
    # ActiveConnection signature, a handler-side error surfaced via the result
    # future, etc.) as WsDispatchError so callers always get a clean tool error
    # instead of a raw HA exception if HA changes underneath us.
    try:
        connection = _CapturingConnection(hass, user)
        # require_admin runs synchronously (raises if not admin); async_response
        # schedules a background task that resolves result_future.
        handler(hass, connection, msg)
        return await asyncio.wait_for(connection.result_future, timeout)
    except WsDispatchError:
        raise
    except TimeoutError as err:
        raise WsDispatchError(f"WebSocket command {command} timed out.") from err
    except Exception as err:  # noqa: BLE001 - degrade any HA-internal breakage to a clean error
        raise WsDispatchError(f"WebSocket command {command} failed: {err}") from err


_REQUIRED_CONNECTION_PARAMS = ("logger", "hass", "send_message", "user", "refresh_token")
_REQUIRED_CONNECTION_METHODS = ("send_result", "send_error", "async_handle_exception")


def check_ws_dispatch_compat(hass: HomeAssistant) -> str | None:
    """Best-effort check that the HA internals this module relies on are intact.

    Returns None when compatible, or a short human-readable reason when HA appears
    to have changed shape (in which case helper CRUD will still fail per-call with
    a clean WsDispatchError; this just surfaces it once at startup). Detects the
    realistic break scenarios: a changed ActiveConnection constructor, a missing
    result-sink method, or a registry that is no longer a dict.
    """
    registry = hass.data.get(ws_const.DOMAIN)
    if registry is not None and not isinstance(registry, dict):
        return "websocket_api command registry is not a dict"
    try:
        params = inspect.signature(ActiveConnection.__init__).parameters
    except (ValueError, TypeError):
        return "cannot introspect ActiveConnection.__init__"
    missing_params = [p for p in _REQUIRED_CONNECTION_PARAMS if p not in params]
    if missing_params:
        return f"ActiveConnection.__init__ no longer accepts: {', '.join(missing_params)}"
    missing_methods = [m for m in _REQUIRED_CONNECTION_METHODS if not hasattr(ActiveConnection, m)]
    if missing_methods:
        return f"ActiveConnection is missing methods: {', '.join(missing_methods)}"
    return None
