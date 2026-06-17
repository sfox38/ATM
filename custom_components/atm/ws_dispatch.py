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
import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.websocket_api import const as ws_const
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0

# The only WS commands ATM is permitted to dispatch in-process. async_ws_command
# refuses anything outside this set, so a future caller cannot turn user input
# into an arbitrary privileged command run as admin. Adding a command here is a
# deliberate act. Keep in sync with the callers in mcp_view.py (helper CRUD,
# backup read/create, lovelace dashboard CRUD). restore_backup is deliberately
# absent (too destructive).
_HELPER_DOMAINS = (
    "input_boolean", "input_number", "input_text",
    "input_select", "input_datetime", "counter", "timer",
)
ALLOWED_WS_COMMANDS: frozenset[str] = frozenset(
    [f"{domain}/{op}" for domain in _HELPER_DOMAINS for op in ("create", "update", "delete")]
    + [
        "backup/agents/info", "backup/info", "backup/generate",
        "lovelace/dashboards/list", "lovelace/dashboards/create",
        "lovelace/dashboards/update", "lovelace/dashboards/delete",
    ]
)


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
        # Build the constructor kwargs against the live ActiveConnection
        # signature and pass only what it accepts, so ATM works across the HA
        # versions it supports: parameters have been added over time (e.g.
        # `remote` in HA 2026.6). Read names from the code object, never
        # inspect.signature, which on Python 3.14 evaluates ActiveConnection's
        # TYPE_CHECKING-only annotations and raises NameError.
        available: dict[str, Any] = {
            "logger": _LOGGER,
            "hass": hass,
            "send_message": self._noop_send,
            "user": user,
            "refresh_token": None,
            "remote": None,
        }
        code = ActiveConnection.__init__.__code__
        accepted = set(code.co_varnames[: code.co_argcount + code.co_kwonlyargcount])
        super().__init__(**{k: v for k, v in available.items() if k in accepted})
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

    Raises WsDispatchError if the command is not on the ALLOWED_WS_COMMANDS
    allowlist, is not registered, the payload fails the command's schema, no
    admin user is available, or the handler reports an error / times out.
    """
    if command not in ALLOWED_WS_COMMANDS:
        raise WsDispatchError(f"WebSocket command not allowed: {command}")

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


# The ActiveConnection.__init__ params _CapturingConnection knows how to supply.
# Must stay in sync with the `available` dict in _CapturingConnection.__init__.
# HA adds params over time (e.g. `remote` in 2026.6); construction supplies the
# intersection with the live signature, so old and new HA both work.
_SUPPLIED_CONNECTION_PARAMS = frozenset(
    {"logger", "hass", "send_message", "user", "refresh_token", "remote"}
)
_REQUIRED_CONNECTION_METHODS = ("send_result", "send_error", "async_handle_exception")


def check_ws_dispatch_compat(hass: HomeAssistant) -> str | None:
    """Best-effort check that the HA internals this module relies on are intact.

    Returns None when compatible, or a short human-readable reason when HA appears
    to have changed shape (in which case helper CRUD will still fail per-call with
    a clean WsDispatchError; this just surfaces it once at startup). Detects the
    realistic break scenarios: a new required ActiveConnection constructor param
    ATM cannot supply, a missing result-sink method, or a registry that is no
    longer a dict.
    """
    try:
        registry = hass.data.get(ws_const.DOMAIN)
        if registry is not None and not isinstance(registry, dict):
            return "websocket_api command registry is not a dict"
        # Read parameter names straight from the code object. Do NOT use
        # inspect.signature here: on Python 3.14 it eagerly evaluates the
        # target's annotations, and ActiveConnection.__init__ annotates a
        # TYPE_CHECKING-only name (WebSocketAdapter) that is undefined at
        # runtime, so signature() raises NameError and would abort setup.
        code = getattr(ActiveConnection.__init__, "__code__", None)
        if code is not None:
            posargs = code.co_varnames[1 : code.co_argcount]  # skip self
            defaults = getattr(ActiveConnection.__init__, "__defaults__", None) or ()
            required = posargs[: len(posargs) - len(defaults)] if defaults else posargs
            unsupported = [p for p in required if p not in _SUPPLIED_CONNECTION_PARAMS]
            if unsupported:
                return (
                    "ActiveConnection.__init__ has required params ATM cannot "
                    f"supply: {', '.join(unsupported)}"
                )
        missing_methods = [m for m in _REQUIRED_CONNECTION_METHODS if not hasattr(ActiveConnection, m)]
        if missing_methods:
            return f"ActiveConnection is missing methods: {', '.join(missing_methods)}"
        return None
    except Exception as err:  # noqa: BLE001 - advisory probe must never abort setup
        return f"compatibility probe could not run: {err}"
