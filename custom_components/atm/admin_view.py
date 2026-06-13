"""Admin API views for the ATM integration."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import uuid
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER
from homeassistant.helpers import entity_registry as er_mod
from homeassistant.util.dt import parse_datetime, utcnow

from .const import (
    ATM_VERSION,
    BLOCKED_DOMAINS,
    CAP_CONFIRM,
    CAP_MODES,
    CAPABILITY_NAMES,
    CONFIRM_AVAILABLE_CAPS,
    DOMAIN,
    GITHUB_URL,
    MAX_REQUEST_BODY_BYTES,
    MESA_CONFIRM_CAP,
    MESA_MODES,
    MIN_HA_VERSION,
    PERSONA_NAMES,
    TOKEN_NAME_REGEX,
)
from .data import ATMData
from .helpers import cancel_expiry_timer, notify_tools_list_changed, terminate_token_connections
from .policy_engine import Permission, get_effective_hint, resolve
from .token_store import PermissionTree, VALID_NODE_STATES, token_name_slug

_LOGGER = logging.getLogger(__name__)


def _err(code: str, message: str, status: int, request_id: str = "") -> web.Response:
    """Return a JSON error response. Uses request_id if supplied, else generates one."""
    rid = request_id or str(uuid.uuid4())
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps({"error": code, "message": message}),
        headers={"X-ATM-Request-ID": rid},
    )


def _ok(body: Any, status: int = 200, request_id: str = "") -> web.Response:
    """Return a JSON success response. Uses request_id if supplied, else generates one."""
    rid = request_id or str(uuid.uuid4())
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps(body, default=str),
        headers={"X-ATM-Request-ID": rid},
    )


def require_admin(method):
    """Decorator for HomeAssistantView methods that require HA admin privileges.

    Generates a per-request ID, stashes it on the request object as 'atm_rid',
    and logs every admin API call so the ID can be correlated with response headers.
    """
    @functools.wraps(method)
    async def wrapper(self, request: web.Request, **kwargs):
        request_id = str(uuid.uuid4())
        request["atm_rid"] = request_id
        user = request.get(KEY_HASS_USER)
        if not request.get(KEY_AUTHENTICATED):
            _LOGGER.info("Admin %s %s unauthenticated rid=%s", request.method, request.path, request_id)
            return _err("unauthorized", "Authentication required.", 401, request_id)
        if not user or not user.is_admin:
            _LOGGER.info("Admin %s %s forbidden rid=%s", request.method, request.path, request_id)
            return _err("forbidden", "Admin access required.", 403, request_id)
        # Logs user.id (UUID) rather than user.name. UUID is stable and non-spoofable;
        # user.name can be changed by the admin. Intentional.
        _LOGGER.info("Admin %s %s rid=%s user=%s", request.method, request.path, request_id, user.id)
        return await method(self, request, **kwargs)
    return wrapper


async def _read_body(request: web.Request, request_id: str = "") -> dict | web.Response:
    """Read and parse the request body as a JSON object.

    Returns an empty dict for requests with no body. Returns an error response
    on read failure, invalid JSON, or a non-object body.
    """
    if request.content_length is not None and request.content_length > MAX_REQUEST_BODY_BYTES:
        return _err("request_too_large", "Request body too large.", 413, request_id)

    try:
        body_bytes = await request.content.read(MAX_REQUEST_BODY_BYTES + 1)
    except Exception:
        return _err("invalid_request", "Failed to read request body.", 400, request_id)

    if len(body_bytes) > MAX_REQUEST_BODY_BYTES:
        return _err("request_too_large", "Request body too large.", 413, request_id)

    if not body_bytes:
        return {}

    try:
        parsed = json.loads(body_bytes)
    except json.JSONDecodeError:
        return _err("invalid_request", "Invalid JSON body.", 400, request_id)

    if not isinstance(parsed, dict):
        return _err("invalid_request", "Request body must be a JSON object.", 400, request_id)

    return parsed


_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ENTITY_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
_MAX_NODE_ID_LEN = 255
_INJECTION_CHARS = frozenset("<>\"'&;")


def _validate_node_id(node_type: str, node_id: str, rid: str) -> web.Response | None:
    """Return an error response if node_id fails length, injection, or format checks."""
    if len(node_id) > _MAX_NODE_ID_LEN:
        return _err("invalid_request", "Node ID is too long.", 400, rid)
    if any(c in node_id for c in _INJECTION_CHARS):
        return _err("invalid_request", "Node ID contains invalid characters.", 400, rid)
    if node_type == "domains" and not _DOMAIN_RE.match(node_id):
        return _err("invalid_request", "Invalid domain name.", 400, rid)
    if node_type == "entities" and not _ENTITY_RE.match(node_id):
        return _err("invalid_request", "Invalid entity ID format.", 400, rid)
    return None


def _validate_permission_tree_body(body: dict, rid: str) -> web.Response | None:
    """Return an error response if any node ID, state, or hint in the permission tree body is invalid."""
    for section, node_type in (("domains", "domains"), ("devices", "devices"), ("entities", "entities")):
        for key, value in body.get(section, {}).items():
            err = _validate_node_id(node_type, key, rid)
            if err:
                return err
            if not isinstance(value, dict):
                return _err(
                    "invalid_request",
                    f"Node {key!r} value must be an object with a 'state' key.",
                    400,
                    rid,
                )
            state = value.get("state", "GREY")
            if state not in VALID_NODE_STATES:
                return _err(
                    "invalid_request",
                    f"Invalid state {state!r} for {node_type[:-1]} {key!r}. "
                    f"Valid states: {sorted(VALID_NODE_STATES)}.",
                    400,
                    rid,
                )
            hint = value.get("hint")
            if hint is not None:
                if not isinstance(hint, str):
                    return _err("invalid_request", f"hint for {key!r} must be a string.", 400, rid)
                if len(hint) > 200:
                    return _err("invalid_request", f"hint for {key!r} exceeds 200 characters.", 400, rid)
    return None


def _build_entity_tree(hass: Any) -> dict:
    """Build a domain-keyed tree of all non-disabled, non-ATM entities.

    Pulls from the entity, device, and area registries (all in-memory dicts).
    Synchronous; never performs I/O. The result is cached in
    ATMData.entity_tree_cache and invalidated on registry change events.
    """
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)

    tree: dict[str, dict] = {}

    for entry in entity_reg.entities.values():
        if entry.disabled_by is not None:
            continue

        entity_id = entry.entity_id
        domain = entity_id.split(".")[0]

        if domain in BLOCKED_DOMAINS:
            continue

        # Exclude ATM's own telemetry sensors (sensor.atm_* entities registered to the
        # atm platform). They live in the sensor domain so BLOCKED_DOMAINS won't catch them.
        # Showing them would let admins grant permissions that the runtime policy engine
        # always blocks, causing confusion. Spec §3.7 only mentions the atm domain, but
        # the intent is that ATM internals don't appear in the permission UI.
        if entry.platform == DOMAIN:
            continue

        state = hass.states.get(entity_id)
        friendly_name = None
        if state:
            friendly_name = state.attributes.get("friendly_name") or state.name

        if domain not in tree:
            tree[domain] = {"devices": {}, "deviceless_entities": [], "entity_details": {}}

        area_id = entry.area_id
        if not area_id and entry.device_id:
            device = device_reg.async_get(entry.device_id)
            if device:
                area_id = device.area_id

        area_name = None
        if area_id:
            area = area_reg.async_get_area(area_id)
            area_name = area.name if area else None

        entity_info: dict[str, Any] = {
            "entity_id": entity_id,
            "friendly_name": friendly_name,
            "device_id": entry.device_id,
            "area_id": area_id,
            "area_name": area_name,
        }

        if entry.device_id:
            device_id = entry.device_id
            if device_id not in tree[domain]["devices"]:
                device = device_reg.async_get(device_id)
                if device:
                    d_area_id = device.area_id
                    d_area_name = None
                    if d_area_id:
                        da = area_reg.async_get_area(d_area_id)
                        d_area_name = da.name if da else None
                    tree[domain]["devices"][device_id] = {
                        "device_id": device_id,
                        "name": device.name_by_user or device.name or device_id,
                        "area_id": d_area_id,
                        "area_name": d_area_name,
                        "entities": [],
                    }
                else:
                    tree[domain]["devices"][device_id] = {
                        "device_id": device_id,
                        "name": device_id,
                        "area_id": None,
                        "area_name": None,
                        "entities": [],
                    }
            tree[domain]["devices"][device_id]["entities"].append(entity_id)
        else:
            tree[domain]["deviceless_entities"].append(entity_id)

        tree[domain]["entity_details"][entity_id] = entity_info

    return tree


def _build_resolution_path(entity_id: str, token: Any, hass: Any) -> list[dict]:
    """Return the ancestor chain and each node's state for a given entity/token pair.

    Used by the resolve admin endpoint to explain why an entity has a particular
    effective permission.
    """
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    er_reg = er.async_get(hass)
    dr_reg = dr.async_get(hass)

    entry = er_reg.async_get(entity_id)
    if entry:
        entity_id = entry.entity_id
    domain = entity_id.split(".")[0]

    path: list[dict] = [{"level": "global", "state": "GREY"}]

    domain_node = token.permissions.domains.get(domain)
    path.append({"level": f"domain:{domain}", "state": domain_node.state if domain_node else "GREY"})

    if entry and entry.device_id:
        device = dr_reg.async_get(entry.device_id)
        if device:
            device_name = device.name_by_user or device.name or entry.device_id
        else:
            device_name = entry.device_id
        device_node = token.permissions.devices.get(entry.device_id)
        path.append({"level": f"device:{device_name}", "state": device_node.state if device_node else "GREY"})

    entity_node = token.permissions.entities.get(entity_id)
    path.append({"level": f"entity:{entity_id}", "state": entity_node.state if entity_node else "GREY"})

    return path


class ATMAdminInfoView(HomeAssistantView):
    """GET /api/atm/admin/info - integration metadata."""

    url = "/api/atm/admin/info"
    name = "api:atm:admin:info"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        return _ok({"version": ATM_VERSION, "min_ha_version": MIN_HA_VERSION, "github_url": GITHUB_URL}, request_id=request["atm_rid"])


class ATMAdminArchivedTokensView(HomeAssistantView):
    """GET /api/atm/admin/tokens/archived - list all archived tokens."""

    url = "/api/atm/admin/tokens/archived"
    name = "api:atm:admin:archived_tokens"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        data: ATMData = self.hass.data[DOMAIN]
        archived = [t.to_dict() for t in data.store.list_archived()]
        return _ok(archived, request_id=request["atm_rid"])


class ATMAdminArchivedTokenView(HomeAssistantView):
    """DELETE /api/atm/admin/tokens/archived/{token_id} - permanently delete an archived record."""

    url = "/api/atm/admin/tokens/archived/{token_id}"
    name = "api:atm:admin:archived_token"
    requires_auth = True

    @require_admin
    async def delete(self, request: web.Request, token_id: str) -> web.Response:
        rid = request["atm_rid"]
        data: ATMData = self.hass.data[DOMAIN]
        deleted = await data.store.async_delete_archived(token_id)
        if not deleted:
            return _err("not_found", "Archived token not found.", 404, rid)
        user = request[KEY_HASS_USER]
        data.audit.record(
            request_id=rid,
            token_id="admin",
            token_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )
        return web.Response(status=204, headers={"X-ATM-Request-ID": rid})


class ATMAdminTokensView(HomeAssistantView):
    """GET /api/atm/admin/tokens - list active tokens.
    POST /api/atm/admin/tokens - create a new token.
    """

    url = "/api/atm/admin/tokens"
    name = "api:atm:admin:tokens"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        data: ATMData = self.hass.data[DOMAIN]
        tokens = [t.to_dict() for t in data.store.list_tokens()]
        return _ok(tokens, request_id=request["atm_rid"])

    @require_admin
    async def post(self, request: web.Request) -> web.Response:

        rid = request["atm_rid"]
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        user = request[KEY_HASS_USER]

        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        name = body.get("name")
        if not name or not isinstance(name, str):
            return _err("invalid_request", "name is required.", 400, rid)
        if not TOKEN_NAME_REGEX.match(name):
            return _err("invalid_request", "name does not match required pattern.", 400, rid)
        pass_through = bool(body.get("pass_through", False))
        if pass_through and not body.get("confirm_pass_through"):
            return _err("invalid_request", "confirm_pass_through: true is required when enabling pass_through.", 400, rid)
        use_assist_exposure = bool(body.get("use_assist_exposure", False)) if pass_through else False

        expires_at = None
        if "expires_at" in body:
            expires_at = parse_datetime(body["expires_at"])
            if expires_at is None:
                return _err("invalid_request", "Invalid expires_at datetime.", 400, rid)

        try:
            rate_limit_requests = int(body.get("rate_limit_requests", 60))
            rate_limit_burst = int(body.get("rate_limit_burst", 10))
        except (TypeError, ValueError):
            return _err("invalid_request", "rate_limit_requests and rate_limit_burst must be integers.", 400, rid)

        if rate_limit_requests < 0 or rate_limit_burst < 0:
            return _err("invalid_request", "rate_limit_requests and rate_limit_burst must be non-negative.", 400, rid)
        if rate_limit_requests > 100_000 or rate_limit_burst > 100_000:
            return _err("invalid_request", "rate_limit_requests and rate_limit_burst must not exceed 100000.", 400, rid)

        async with data.store.async_lock:
            if data.store.name_slug_exists(name):
                return _err("conflict", "A token with that name (or equivalent slug) already exists.", 409, rid)
            record, raw_token = await data.store.async_create_token(
                name=name,
                created_by=user.id,
                expires_at=expires_at,
                pass_through=pass_through,
                use_assist_exposure=use_assist_exposure,
                rate_limit_requests=rate_limit_requests,
                rate_limit_burst=rate_limit_burst,
            )

        if data.async_on_token_created:
            await data.async_on_token_created(record)

        data.audit.record(
            request_id=rid,
            token_id="admin",
            token_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )

        # raw_token is included once in the creation response and never again.
        response_body = record.to_dict()
        response_body["token"] = raw_token
        return _ok(response_body, status=201, request_id=rid)


class ATMAdminTokenView(HomeAssistantView):
    """GET/PATCH/DELETE /api/atm/admin/tokens/{token_id} - manage a single token."""

    url = "/api/atm/admin/tokens/{token_id}"
    name = "api:atm:admin:token"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, token_id: str) -> web.Response:
        rid = request["atm_rid"]
        data: ATMData = self.hass.data[DOMAIN]
        token = data.store.get_token_by_id(token_id)
        if token is None:
            return _err("not_found", "Token not found.", 404, rid)
        return _ok(token.to_dict(), request_id=rid)

    @require_admin
    async def patch(self, request: web.Request, token_id: str) -> web.Response:

        rid = request["atm_rid"]
        data: ATMData = self.hass.data[DOMAIN]

        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        if "name" in body or "expires_at" in body:
            return _err("invalid_request", "name and expires_at are immutable after token creation.", 400, rid)

        async with data.store.async_lock:
            token = data.store.get_token_by_id(token_id)
            if token is None:
                return _err("not_found", "Token not found.", 404, rid)

            if "pass_through" in body:
                enabling = bool(body["pass_through"])
                if enabling and not token.pass_through and not body.get("confirm_pass_through"):
                    return _err("invalid_request", "confirm_pass_through: true is required when enabling pass_through.", 400, rid)

            allowed_keys = {
                "pass_through", "use_assist_exposure", "rate_limit_requests", "rate_limit_burst",
                "persona",
            } | set(CAPABILITY_NAMES)
            patchable = {k: v for k, v in body.items() if k in allowed_keys}
            for cap_name in set(CAPABILITY_NAMES) & patchable.keys():
                value = patchable[cap_name]
                if value not in CAP_MODES:
                    return _err(
                        "invalid_request",
                        f"{cap_name} must be one of: deny, allow, confirm.",
                        400,
                        rid,
                    )
                if value == CAP_CONFIRM and cap_name not in CONFIRM_AVAILABLE_CAPS:
                    return _err(
                        "invalid_request",
                        f"{cap_name} does not support 'confirm' mode.",
                        400,
                        rid,
                    )
            if "persona" in patchable and patchable["persona"] not in PERSONA_NAMES:
                return _err("invalid_request", "Unknown persona.", 400, rid)
            if "use_assist_exposure" in patchable:
                resulting_pass_through = bool(patchable.get("pass_through", token.pass_through))
                if not resulting_pass_through:
                    return _err("invalid_request", "use_assist_exposure is only valid for pass_through tokens.", 400, rid)
            for rl_field in ("rate_limit_requests", "rate_limit_burst"):
                if rl_field in patchable:
                    try:
                        patchable[rl_field] = int(patchable[rl_field])
                    except (TypeError, ValueError):
                        return _err("invalid_request", f"{rl_field} must be an integer.", 400, rid)
                    if patchable[rl_field] < 0:
                        return _err("invalid_request", f"{rl_field} must be non-negative.", 400, rid)
                    if patchable[rl_field] > 100_000:
                        return _err("invalid_request", f"{rl_field} must not exceed 100000.", 400, rid)
            updated = await data.store.async_patch_token(token_id, **patchable)

        _TOOLS_LIST_FLAGS = {
            "pass_through", "use_assist_exposure", "persona",
        } | set(CAPABILITY_NAMES)
        if patchable.keys() & _TOOLS_LIST_FLAGS:
            notify_tools_list_changed(token_id, data.sse_connections)

        user = request[KEY_HASS_USER]
        data.audit.record(
            request_id=rid,
            token_id="admin",
            token_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )
        return _ok(updated.to_dict(), request_id=rid)

    @require_admin
    async def delete(self, request: web.Request, token_id: str) -> web.Response:
        """Revoke a token. Archives it, terminates its SSE connections, fires the bus event."""

        rid = request["atm_rid"]
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        user = request[KEY_HASS_USER]

        async with data.store.async_lock:
            token = data.store.get_token_by_id(token_id)
            if token is None:
                return _err("not_found", "Token not found.", 404, rid)

            token_name = token.name
            now = utcnow()

            await data.store.async_archive_token(token_id, revoked=True, revoked_at=now)
            cancel_expiry_timer(data, token_id)

        await terminate_token_connections(token_id, data.sse_connections)
        data.rate_limiter.destroy(token_id)
        data.rate_limit_notified.pop(token_id, None)
        data.token_counters.pop(token_id, None)

        hass.bus.async_fire("atm_token_revoked", {
            "token_id": token_id,
            "token_name": token_name,
            "revoked_by": user.id,
            "timestamp": now.isoformat(),
        })

        data.audit.record(
            request_id=rid,
            token_id="admin",
            token_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )

        slug = token_name_slug(token_name)
        if data.async_on_token_archived:
            try:
                await data.async_on_token_archived(slug)
            except Exception:
                _LOGGER.warning("Sensor removal failed for token %s; entity registry may have ghost entries", token_id, exc_info=True)

        return web.Response(status=204, headers={"X-ATM-Request-ID": rid})


class ATMAdminPermissionsView(HomeAssistantView):
    """GET/PUT /api/atm/admin/tokens/{token_id}/permissions - read or replace the full permission tree."""

    url = "/api/atm/admin/tokens/{token_id}/permissions"
    name = "api:atm:admin:permissions"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, token_id: str) -> web.Response:
        rid = request["atm_rid"]
        data: ATMData = self.hass.data[DOMAIN]
        token = data.store.get_token_by_id(token_id)
        if token is None:
            return _err("not_found", "Token not found.", 404, rid)
        return _ok(token.permissions.to_dict(), request_id=rid)

    @require_admin
    async def put(self, request: web.Request, token_id: str) -> web.Response:

        rid = request["atm_rid"]
        data: ATMData = self.hass.data[DOMAIN]

        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        err = _validate_permission_tree_body(body, rid)
        if err:
            return err

        try:
            new_tree = PermissionTree.from_dict(body)
        except Exception:
            return _err("invalid_request", "Invalid permission tree structure.", 400, rid)

        async with data.store.async_lock:
            token = data.store.get_token_by_id(token_id)
            if token is None:
                return _err("not_found", "Token not found.", 404, rid)

            updated = await data.store.async_set_permissions(token_id, new_tree)

        user = request[KEY_HASS_USER]
        data.audit.record(
            request_id=rid,
            token_id="admin",
            token_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )
        return _ok(updated.permissions.to_dict(), request_id=rid)


class ATMAdminPermissionDomainView(HomeAssistantView):
    """PATCH /api/atm/admin/tokens/{token_id}/permissions/domains/{node_id}."""

    url = "/api/atm/admin/tokens/{token_id}/permissions/domains/{node_id}"
    name = "api:atm:admin:permission_domain"
    requires_auth = True

    @require_admin
    async def patch(self, request: web.Request, token_id: str, node_id: str) -> web.Response:
        return await _patch_permission_node(request, self.hass, token_id, "domains", node_id)


class ATMAdminPermissionDeviceView(HomeAssistantView):
    """PATCH /api/atm/admin/tokens/{token_id}/permissions/devices/{node_id}."""

    url = "/api/atm/admin/tokens/{token_id}/permissions/devices/{node_id}"
    name = "api:atm:admin:permission_device"
    requires_auth = True

    @require_admin
    async def patch(self, request: web.Request, token_id: str, node_id: str) -> web.Response:
        return await _patch_permission_node(request, self.hass, token_id, "devices", node_id)


class ATMAdminPermissionEntityView(HomeAssistantView):
    """PATCH /api/atm/admin/tokens/{token_id}/permissions/entities/{node_id}."""

    url = "/api/atm/admin/tokens/{token_id}/permissions/entities/{node_id}"
    name = "api:atm:admin:permission_entity"
    requires_auth = True

    @require_admin
    async def patch(self, request: web.Request, token_id: str, node_id: str) -> web.Response:
        return await _patch_permission_node(request, self.hass, token_id, "entities", node_id)


async def _patch_permission_node(
    request: web.Request,
    hass: Any,
    token_id: str,
    node_type: str,
    node_id: str,
) -> web.Response:
    """Shared handler for PATCH on domain/device/entity permission nodes."""
    rid = request["atm_rid"]

    err = _validate_node_id(node_type, node_id, rid)
    if err:
        return err

    data: ATMData = hass.data[DOMAIN]

    body = await _read_body(request, rid)
    if isinstance(body, web.Response):
        return body

    state = body.get("state")
    if state not in VALID_NODE_STATES:
        return _err("invalid_request", f"state must be one of: {', '.join(sorted(VALID_NODE_STATES))}.", 400, rid)

    hint = body.get("hint")
    if hint is not None and not isinstance(hint, str):
        return _err("invalid_request", "hint must be a string.", 400, rid)
    if hint is not None and len(hint) > 200:
        return _err("invalid_request", "hint must be 200 characters or fewer.", 400, rid)

    async with data.store.async_lock:
        token = data.store.get_token_by_id(token_id)
        if token is None:
            return _err("not_found", "Token not found.", 404, rid)

        updated = await data.store.async_patch_permission_node(
            token_id, node_type, node_id, state, hint
        )

    user = request[KEY_HASS_USER]
    data.audit.record(
        request_id=rid,
        token_id="admin",
        token_name=f"admin:{user.id}",
        method=request.method,
        resource=request.path,
        outcome="allowed",
        client_ip=request.remote or "",
        settings=data.store.get_settings(),
    )
    return _ok(updated.permissions.to_dict(), request_id=rid)


class ATMAdminResolveView(HomeAssistantView):
    """GET /api/atm/admin/tokens/{token_id}/resolve/{entity_id} - explain effective permission."""

    url = "/api/atm/admin/tokens/{token_id}/resolve/{entity_id}"
    name = "api:atm:admin:resolve"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, token_id: str, entity_id: str) -> web.Response:
        rid = request["atm_rid"]
        if not _ENTITY_RE.match(entity_id):
            return _err("invalid_request", "Invalid entity ID format.", 400, rid)
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        token = data.store.get_token_by_id(token_id)
        if token is None:
            return _err("not_found", "Token not found.", 404, rid)

        perm = resolve(entity_id, token, hass)
        resolution_path = _build_resolution_path(entity_id, token, hass)

        effective_map = {
            Permission.WRITE: "WRITE",
            Permission.READ: "READ",
            Permission.DENY: "DENY",
            Permission.NO_ACCESS: "NO_ACCESS",
            Permission.NOT_FOUND: "NOT_FOUND",
        }

        effective_hint = get_effective_hint(token, entity_id, hass)

        return _ok({
            "entity_id": entity_id,
            "resolution_path": resolution_path,
            "effective": effective_map.get(perm, "NO_ACCESS"),
            "effective_hint": effective_hint,
        }, request_id=rid)


class ATMAdminScopeView(HomeAssistantView):
    """GET /api/atm/admin/tokens/{token_id}/scope - enumerate all readable/writable entities."""

    url = "/api/atm/admin/tokens/{token_id}/scope"
    name = "api:atm:admin:scope"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, token_id: str) -> web.Response:
        rid = request["atm_rid"]
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        token = data.store.get_token_by_id(token_id)
        if token is None:
            return _err("not_found", "Token not found.", 404, rid)

        all_states = hass.states.async_all()
        readable: list[str] = []
        writable: list[str] = []

        if token.pass_through:
            # Fast path: pass_through tokens have WRITE on everything except BLOCKED_DOMAINS
            # and ATM platform entities. Avoids an O(n) resolve() call per entity.
            registry = er_mod.async_get(hass)
            for state in all_states:
                eid = state.entity_id
                if eid.split(".")[0] in BLOCKED_DOMAINS:
                    continue
                entry = registry.async_get(eid)
                if entry is not None and entry.platform == DOMAIN:
                    continue
                readable.append(eid)
                writable.append(eid)
        else:
            for state in all_states:
                eid = state.entity_id
                perm = resolve(eid, token, hass)
                if perm == Permission.WRITE:
                    readable.append(eid)
                    writable.append(eid)
                elif perm == Permission.READ:
                    readable.append(eid)

        # capability_flags reports raw stored values without the pass_through OR adjustments
        # applied by _build_server_info / _build_context_json. This is intentional: the
        # admin scope view is a diagnostic tool and the admin should see actual stored flags,
        # not the effective values a client would receive.
        return _ok({
            "token_id": token_id,
            "token_name": token.name,
            "readable": sorted(readable),
            "writable": sorted(writable),
            "persona": token.persona,
            "capability_flags": {name: getattr(token, name) for name in CAPABILITY_NAMES},
        }, request_id=rid)


class ATMAdminEntityTreeView(HomeAssistantView):
    """GET /api/atm/admin/entities - return (cached) entity tree for the permission UI."""

    url = "/api/atm/admin/entities"
    name = "api:atm:admin:entities"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["atm_rid"]
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]

        if request.query.get("force_reload"):
            data.entity_tree_cache_valid = False

        async with data.entity_tree_lock:
            if not data.entity_tree_cache_valid or data.entity_tree_cache is None:
                data.entity_tree_cache = _build_entity_tree(hass)
                data.entity_tree_cache_valid = True

        import functools
        json_body = await hass.async_add_executor_job(
            functools.partial(json.dumps, data.entity_tree_cache, default=str)
        )
        return web.Response(
            status=200,
            content_type="application/json",
            text=json_body,
            headers={"X-ATM-Request-ID": rid},
        )


class ATMAdminTokenStatsView(HomeAssistantView):
    """GET /api/atm/admin/tokens/{token_id}/stats - in-memory counters for one token."""

    url = "/api/atm/admin/tokens/{token_id}/stats"
    name = "api:atm:admin:token_stats"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, token_id: str) -> web.Response:
        rid = request["atm_rid"]
        data: ATMData = self.hass.data[DOMAIN]
        token = data.store.get_token_by_id(token_id)
        if token is None:
            return _err("not_found", "Token not found.", 404, rid)

        counters = data.token_counters.get(token_id, {
            "request_count": 0,
            "denied_count": 0,
            "rate_limit_hits": 0,
        })

        last_used = token.last_used_at.isoformat() if token.last_used_at else None

        status = "expired" if token.is_expired() else "active"

        return _ok({
            "token_id": token_id,
            "token_name": token.name,
            "request_count": counters["request_count"],
            "denied_count": counters["denied_count"],
            "rate_limit_hits": counters["rate_limit_hits"],
            "last_used_at": last_used,
            "status": status,
        }, request_id=rid)


class ATMAdminTokenAuditView(HomeAssistantView):
    """GET /api/atm/admin/tokens/{token_id}/audit - paginated audit log for one token."""

    url = "/api/atm/admin/tokens/{token_id}/audit"
    name = "api:atm:admin:token_audit"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, token_id: str) -> web.Response:
        rid = request["atm_rid"]
        data: ATMData = self.hass.data[DOMAIN]
        token = data.store.get_token_by_id(token_id)
        if token is None:
            return _err("not_found", "Token not found.", 404, rid)

        try:
            limit = min(int(request.query.get("limit", 100)), 500)
            offset = max(int(request.query.get("offset", 0)), 0)
        except (TypeError, ValueError):
            return _err("invalid_request", "Invalid pagination parameters.", 400, rid)

        outcome_filter = request.query.get("outcome")
        ip_filter = request.query.get("ip")

        entries = data.audit.query(
            token_id=token_id,
            outcome=outcome_filter,
            client_ip=ip_filter,
            limit=limit,
            offset=offset,
        )
        if entries is None:
            return _err("invalid_request", f"Unknown outcome filter: {outcome_filter!r}.", 400, rid)
        return _ok([e.to_dict() for e in entries], request_id=rid)


class ATMAdminAuditView(HomeAssistantView):
    """GET /api/atm/admin/audit - paginated global audit log with optional filters."""

    url = "/api/atm/admin/audit"
    name = "api:atm:admin:audit"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["atm_rid"]
        data: ATMData = self.hass.data[DOMAIN]

        try:
            limit = min(int(request.query.get("limit", 100)), 500)
            offset = max(int(request.query.get("offset", 0)), 0)
        except (TypeError, ValueError):
            return _err("invalid_request", "Invalid pagination parameters.", 400, rid)

        token_id_filter = request.query.get("token_id")
        outcome_filter = request.query.get("outcome")
        ip_filter = request.query.get("ip")

        entries = data.audit.query(
            token_id=token_id_filter,
            outcome=outcome_filter,
            client_ip=ip_filter,
            limit=limit,
            offset=offset,
        )
        if entries is None:
            return _err("invalid_request", f"Unknown outcome filter: {outcome_filter!r}.", 400, rid)
        return _ok([e.to_dict() for e in entries], request_id=rid)


class ATMAdminSettingsView(HomeAssistantView):
    """GET/PATCH /api/atm/admin/settings - read or update global integration settings."""

    url = "/api/atm/admin/settings"
    name = "api:atm:admin:settings"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        data: ATMData = self.hass.data[DOMAIN]
        return _ok(data.store.get_settings().to_dict(), request_id=request["atm_rid"])

    @require_admin
    async def patch(self, request: web.Request) -> web.Response:

        rid = request["atm_rid"]
        data: ATMData = self.hass.data[DOMAIN]

        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        _VALID_FLUSH_INTERVALS = frozenset({0, 5, 10, 15, 30, 60})
        _VALID_LOG_MAXLENS = frozenset({100, 1000, 5000, 10000})

        _BOOL_SETTINGS = frozenset({
            "kill_switch", "disable_all_logging", "log_allowed", "log_denied",
            "log_rate_limited", "log_entity_names", "log_client_ip", "notify_on_rate_limit",
        })
        patchable = {
            k: v for k, v in body.items()
            if k in _BOOL_SETTINGS | {"audit_flush_interval", "audit_log_maxlen", "mesa_mode"}
        }
        for key in _BOOL_SETTINGS:
            if key in patchable:
                if not isinstance(patchable[key], bool):
                    return _err("invalid_request", f"{key!r} must be a boolean (true or false).", 400, rid)

        if "mesa_mode" in patchable:
            if patchable["mesa_mode"] not in MESA_MODES:
                return _err("invalid_request", f"mesa_mode must be one of: {sorted(MESA_MODES)}.", 400, rid)

        if "audit_flush_interval" in patchable:
            try:
                patchable["audit_flush_interval"] = int(patchable["audit_flush_interval"])
            except (TypeError, ValueError):
                return _err("invalid_request", "audit_flush_interval must be an integer.", 400, rid)
            if patchable["audit_flush_interval"] not in _VALID_FLUSH_INTERVALS:
                return _err("invalid_request", f"audit_flush_interval must be one of: {sorted(_VALID_FLUSH_INTERVALS)}.", 400, rid)

        if "audit_log_maxlen" in patchable:
            try:
                patchable["audit_log_maxlen"] = int(patchable["audit_log_maxlen"])
            except (TypeError, ValueError):
                return _err("invalid_request", "audit_log_maxlen must be an integer.", 400, rid)
            if patchable["audit_log_maxlen"] not in _VALID_LOG_MAXLENS:
                return _err("invalid_request", f"audit_log_maxlen must be one of: {sorted(_VALID_LOG_MAXLENS)}.", 400, rid)

        async with data.store.async_lock:
            old_kill_switch = data.store.get_settings().kill_switch
            updated = await data.store.async_patch_settings(**patchable)

        if "audit_log_maxlen" in patchable:
            data.audit.resize(patchable["audit_log_maxlen"])

        if "mesa_mode" in patchable and data.mesa is not None:
            data.mesa.set_mode(updated.mesa_mode)

        if "kill_switch" in patchable:
            new_kill_switch = updated.kill_switch
            if not old_kill_switch and new_kill_switch:
                # Kill switch just activated: terminate all open SSE connections.
                for token_id in list(data.sse_connections.keys()):
                    await terminate_token_connections(token_id, data.sse_connections)
            elif old_kill_switch and not new_kill_switch:
                # Kill switch just deactivated: re-register routes if not already registered.
                if not data.routes_registered and data.async_register_routes:
                    await data.async_register_routes()
                    data.routes_registered = True

        user = request[KEY_HASS_USER]
        data.audit.record(
            request_id=rid,
            token_id="admin",
            token_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=updated,
        )
        return _ok(updated.to_dict(), request_id=rid)


class ATMAdminWipeView(HomeAssistantView):
    """DELETE /api/atm/admin/wipe - wipe all tokens, audit log, and settings."""

    url = "/api/atm/admin/wipe"
    name = "api:atm:admin:wipe"
    requires_auth = True

    @require_admin
    async def delete(self, request: web.Request) -> web.Response:
        rid = request["atm_rid"]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        if body.get("confirm") != "WIPE":
            return _err("invalid_request", 'confirm must be "WIPE".', 400, rid)

        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        user = request[KEY_HASS_USER]

        async with data.store.async_lock:
            for token_id in list(data.sse_connections.keys()):
                await terminate_token_connections(token_id, data.sse_connections)

            data.rate_limiter.destroy_all()
            data.rate_limit_notified.clear()
            data.token_counters.clear()
            await data.audit.async_wipe()

            for _tid in list(data.expiry_timers):
                cancel_expiry_timer(data, _tid)

            active_slugs = [token_name_slug(t.name) for t in data.store.list_tokens()]
            await data.store.async_wipe()

        # Clear mcp_sessions after storage wipe so any sessions created during the
        # async yields above (audit wipe, lock acquisition) are also removed.
        # Increment wipe_epoch so any ghost SSE heartbeat loops detect the wipe and exit.
        data.mcp_sessions.clear()
        data.wipe_epoch += 1

        if not data.routes_registered and data.async_register_routes:
            await data.async_register_routes()
            data.routes_registered = True

        # Sensor removal runs after the lock is released. A concurrent token creation
        # with the same slug as a just-wiped token could have its sensors removed here.
        # This race is accepted: wipe is a destructive admin operation and should not
        # be run concurrently with token creation.
        if data.async_on_token_archived:
            await asyncio.gather(
                *[data.async_on_token_archived(slug) for slug in active_slugs],
                return_exceptions=True,
            )
        # Clear unconditionally: if any sensor removal above failed and left stale
        # entries in token_id_sensors, they would cause async_write_ha_state() to
        # be called for token IDs that no longer exist. Since the wipe removes all
        # tokens, there are no valid sensors left regardless.
        data.token_id_sensors.clear()

        # Second pass: terminate any SSE connections established during the race window
        # between the first termination pass and the storage wipe completing.
        for token_id in list(data.sse_connections.keys()):
            await terminate_token_connections(token_id, data.sse_connections)

        data.audit.record(
            request_id=rid,
            token_id="admin",
            token_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )

        return web.Response(status=204, headers={"X-ATM-Request-ID": rid})


class ATMAdminTokenRotateView(HomeAssistantView):
    """POST /api/atm/admin/tokens/{token_id}/rotate - replace the raw token value atomically."""

    url = "/api/atm/admin/tokens/{token_id}/rotate"
    name = "api:atm:admin:token_rotate"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, token_id: str) -> web.Response:
        rid = request["atm_rid"]
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        user = request[KEY_HASS_USER]

        async with data.store.async_lock:
            result = await data.store.async_rotate_token(token_id)

        if result is None:
            return _err("not_found", "Token not found.", 404, rid)

        token, raw_token = result

        await terminate_token_connections(token_id, data.sse_connections)

        hass.bus.async_fire("atm_token_rotated", {
            "token_id": token.id,
            "token_name": token.name,
            "rotated_by": user.id,
            "timestamp": utcnow().isoformat(),
        })

        data.audit.record(
            request_id=rid,
            token_id="admin",
            token_name=f"admin:{user.id}",
            method=request.method,
            resource=request.path,
            outcome="allowed",
            client_ip=request.remote or "",
            settings=data.store.get_settings(),
        )

        response_body = token.to_dict()
        response_body["token"] = raw_token
        return _ok(response_body, request_id=rid)


class ATMAdminApprovalsView(HomeAssistantView):
    """GET /api/atm/admin/approvals - list approvals, optionally filtered by status/token."""

    url = "/api/atm/admin/approvals"
    name = "api:atm:admin:approvals"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        from .approvals import list_approvals  # noqa: PLC0415

        rid = request["atm_rid"]
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        status = request.query.get("status")
        token_id = request.query.get("token_id")
        try:
            limit = int(request.query.get("limit", "50"))
        except (TypeError, ValueError):
            limit = 50
        try:
            offset = int(request.query.get("offset", "0"))
        except (TypeError, ValueError):
            offset = 0
        records = list_approvals(data.store, status=status, token_id=token_id)
        total = len(records)
        records = records[offset:offset + limit]
        return _ok({
            "approvals": [r.to_dict() for r in records],
            "total": total,
            "limit": limit,
            "offset": offset,
        }, request_id=rid)


class ATMAdminApprovalView(HomeAssistantView):
    """GET / DELETE /api/atm/admin/approvals/{approval_id}.

    DELETE is an alias for reject with reason 'admin_cancelled'.
    """

    url = "/api/atm/admin/approvals/{approval_id}"
    name = "api:atm:admin:approval"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, approval_id: str) -> web.Response:
        from .approvals import get_approval  # noqa: PLC0415

        rid = request["atm_rid"]
        hass = self.hass
        data: ATMData = hass.data[DOMAIN]
        record = get_approval(data.store, approval_id)
        if record is None:
            return _err("not_found", "Approval not found.", 404, rid)
        return _ok(record.to_dict(), request_id=rid)

    @require_admin
    async def delete(self, request: web.Request, approval_id: str) -> web.Response:
        return await _resolve_approval(
            self.hass, request, approval_id,
            terminal_status="cancelled",
            auto_reason="admin_cancelled",
        )


class ATMAdminApprovalApproveView(HomeAssistantView):
    """POST /api/atm/admin/approvals/{approval_id}/approve."""

    url = "/api/atm/admin/approvals/{approval_id}/approve"
    name = "api:atm:admin:approval_approve"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, approval_id: str) -> web.Response:
        return await _approve_approval(self.hass, request, approval_id)


class ATMAdminApprovalRejectView(HomeAssistantView):
    """POST /api/atm/admin/approvals/{approval_id}/reject."""

    url = "/api/atm/admin/approvals/{approval_id}/reject"
    name = "api:atm:admin:approval_reject"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, approval_id: str) -> web.Response:
        rid = request["atm_rid"]
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        reason = body.get("reason") if isinstance(body, dict) else None
        if reason is not None and not isinstance(reason, str):
            return _err("invalid_request", "reason must be a string.", 400, rid)
        return await _resolve_approval(
            self.hass, request, approval_id,
            terminal_status="rejected",
            auto_reason=reason,
        )


async def _resolve_approval(
    hass,
    request: web.Request,
    approval_id: str,
    *,
    terminal_status: str,
    auto_reason: str | None,
) -> web.Response:
    """Reject or cancel a pending approval. Idempotent on already-resolved records."""
    from .approvals import (  # noqa: PLC0415
        dismiss_approval_notification,
        fire_approval_resolved_event,
        get_approval,
        update_approval_status,
    )

    rid = request["atm_rid"]
    data: ATMData = hass.data[DOMAIN]
    user = request[KEY_HASS_USER]
    async with data.store.async_lock:
        record = get_approval(data.store, approval_id)
        if record is None:
            return _err("not_found", "Approval not found.", 404, rid)
        if record.is_terminal():
            return _ok(record.to_dict(), request_id=rid)
        updated = await update_approval_status(
            data.store,
            approval_id,
            status=terminal_status,
            approved_by_user_id=user.id,
            rejected_reason=auto_reason,
        )
    if updated is None:
        return _err("not_found", "Approval not found.", 404, rid)
    dismiss_approval_notification(hass, approval_id)
    fire_approval_resolved_event(hass, updated)
    data.audit.record(
        request_id=rid,
        token_id="admin",
        token_name=f"admin:{user.id}",
        method=f"approval/{terminal_status}",
        resource=f"approval:{updated.tool_name}:{approval_id}",
        outcome="denied",
        client_ip="",
        settings=data.store.get_settings(),
    )
    return _ok(updated.to_dict(), request_id=rid)


async def _approve_approval(hass, request: web.Request, approval_id: str) -> web.Response:
    """Validate, execute, and finalize a previously-pending approval."""
    from .approvals import (  # noqa: PLC0415
        REASON_CAPABILITY_DENIED,
        REASON_KILL_SWITCH,
        REASON_TOKEN_INACTIVE,
        STATUS_APPROVED,
        STATUS_CANCELLED,
        STATUS_REJECTED,
        dismiss_approval_notification,
        fire_approval_resolved_event,
        get_approval,
        update_approval_status,
    )
    from .helpers import effective_cap  # noqa: PLC0415
    from .mcp_view import execute_approved_tool  # noqa: PLC0415

    rid = request["atm_rid"]
    data: ATMData = hass.data[DOMAIN]
    user = request[KEY_HASS_USER]

    async with data.store.async_lock:
        record = get_approval(data.store, approval_id)
        if record is None:
            return _err("not_found", "Approval not found.", 404, rid)
        if record.is_terminal():
            return _ok(record.to_dict(), request_id=rid)

        token = data.store.get_token_by_id(record.token_id)
        if token is None or not token.is_valid():
            await update_approval_status(
                data.store, approval_id,
                status=STATUS_CANCELLED,
                approved_by_user_id=user.id,
                rejected_reason=REASON_TOKEN_INACTIVE,
            )
            updated = get_approval(data.store, approval_id)
            if updated:
                dismiss_approval_notification(hass, approval_id)
                fire_approval_resolved_event(hass, updated)
            return _err("not_found", "Token no longer active.", 409, rid)

        # The MESA sentinel cap is not a real token capability, so effective_cap
        # would auto-deny it. The MESA re-evaluation happens inside the executor
        # instead (it rejects entities that became prohibited/read_only).
        if record.cap_name != MESA_CONFIRM_CAP and effective_cap(token, record.cap_name) == "deny":
            await update_approval_status(
                data.store, approval_id,
                status=STATUS_REJECTED,
                approved_by_user_id=user.id,
                rejected_reason=REASON_CAPABILITY_DENIED,
            )
            updated = get_approval(data.store, approval_id)
            if updated:
                dismiss_approval_notification(hass, approval_id)
                fire_approval_resolved_event(hass, updated)
            return _err("forbidden", "Capability is now denied for this token.", 409, rid)

        settings = data.store.get_settings()
        if settings.kill_switch:
            await update_approval_status(
                data.store, approval_id,
                status=STATUS_CANCELLED,
                approved_by_user_id=user.id,
                rejected_reason=REASON_KILL_SWITCH,
            )
            updated = get_approval(data.store, approval_id)
            if updated:
                dismiss_approval_notification(hass, approval_id)
                fire_approval_resolved_event(hass, updated)
            return _err("service_unavailable", "Kill switch engaged.", 503, rid)

    # Execute outside the lock so the tool can use it freely.
    try:
        tool_result, outcome, _resource = await execute_approved_tool(
            record.tool_name, record.args, token, hass, data,
        )
    except KeyError:
        return _err("invalid_request", "No executor registered for this tool.", 400, rid)
    except Exception:
        _LOGGER.exception("Approval execution failed for %s", approval_id)
        return _err("internal_error", "Execution failed.", 500, rid)

    is_error = bool(tool_result.get("isError"))
    saved_result = {"tool_result": tool_result, "outcome": outcome}
    final_status = STATUS_REJECTED if is_error else STATUS_APPROVED
    auto_reason = "execution_failed" if is_error else None

    async with data.store.async_lock:
        updated = await update_approval_status(
            data.store, approval_id,
            status=final_status,
            approved_by_user_id=user.id,
            rejected_reason=auto_reason,
            result=saved_result,
        )
    if updated is None:
        return _err("not_found", "Approval not found.", 404, rid)
    dismiss_approval_notification(hass, approval_id)
    fire_approval_resolved_event(hass, updated)
    data.audit.record(
        request_id=rid,
        token_id=record.token_id,
        token_name=record.token_name,
        method=f"approval/{final_status}",
        resource=f"approval:{record.tool_name}:{approval_id}",
        outcome="allowed" if final_status == STATUS_APPROVED else "denied",
        client_ip="",
        settings=settings,
    )
    return _ok(updated.to_dict(), request_id=rid)


# ---------------------------------------------------------------------------
# MESA profile administration (Phase 5)
# ---------------------------------------------------------------------------


def _mesa_runtime(hass, rid):
    """Return the MESA runtime, or an error response when MESA is unavailable."""
    data: ATMData = hass.data[DOMAIN]
    if data.mesa is None:
        return None, _err("service_unavailable", "MESA is not available.", 503, rid)
    return data.mesa, None


def _audit_admin(hass, request, rid, resource) -> None:
    data: ATMData = hass.data[DOMAIN]
    user = request[KEY_HASS_USER]
    data.audit.record(
        request_id=rid,
        token_id="admin",
        token_name=f"admin:{user.id}",
        method=request.method,
        resource=resource,
        outcome="allowed",
        client_ip=request.remote or "",
        settings=data.store.get_settings(),
    )


class ATMAdminMesaProfilesView(HomeAssistantView):
    """GET /api/atm/admin/mesa/profiles - list stored entity profiles (paginated)."""

    url = "/api/atm/admin/mesa/profiles"
    name = "api:atm:admin:mesa:profiles"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err

        from .mesa_core.exceptions import InvalidCursorError  # noqa: PLC0415

        q = request.query
        tag = q.get("tag")
        area = q.get("area")
        try:
            limit = int(q.get("limit", 50))
        except (TypeError, ValueError):
            return _err("invalid_request", "limit must be an integer.", 400, rid)
        try:
            result = runtime.store.list(
                domain=q.get("domain"),
                tags=[tag] if tag else None,
                areas=[area] if area else None,
                origin=q.get("origin"),
                include_inferred=True,  # admin sees every origin, including inferred
                limit=limit,
                cursor=q.get("cursor"),
            )
        except InvalidCursorError as exc:
            return _err("invalid_request", str(exc), 400, rid)
        except ValueError as exc:
            return _err("invalid_request", str(exc), 400, rid)

        return _ok(
            {
                "profiles": [
                    {"entity_id": p.entity_id, "document": p.to_dict()}
                    for p in result.profiles
                ],
                "total_matched": result.total_matched,
                "has_more": result.has_more,
                "next_cursor": result.next_cursor,
            },
            request_id=rid,
        )


class ATMAdminMesaProfileView(HomeAssistantView):
    """GET/PUT/DELETE /api/atm/admin/mesa/profiles/{entity_id} - one entity profile."""

    url = "/api/atm/admin/mesa/profiles/{entity_id}"
    name = "api:atm:admin:mesa:profile"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, entity_id: str) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        stored = runtime.store.get(entity_id)
        effective = runtime.store.get_effective(entity_id)
        explanation = runtime.resolver.explain(entity_id)
        return _ok(
            {
                "entity_id": entity_id,
                "stored": stored.to_dict() if stored is not None else None,
                "effective": effective.to_dict(),
                "explanation": explanation.to_dict(),
            },
            request_id=rid,
        )

    @require_admin
    async def put(self, request: web.Request, entity_id: str) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err

        if not _ENTITY_RE.match(entity_id):
            return _err("invalid_request", "Invalid entity ID format.", 400, rid)

        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        from .mesa import read_automation_configs  # noqa: PLC0415
        from .mesa_core import MetadataOrigin, SemanticProfile  # noqa: PLC0415
        from .mesa_core.exceptions import MesaValidationError  # noqa: PLC0415

        try:
            profile = SemanticProfile.from_dict(
                entity_id, body, default_origin=MetadataOrigin.USER
            )
        except MesaValidationError as exc:
            return _err("invalid_request", str(exc), 400, rid)

        async with runtime.lock:
            runtime.store.set(entity_id, profile)
            await runtime.async_save()

        # Cross-check the new profile against the automation registry.
        configs = await self.hass.async_add_executor_job(read_automation_configs, self.hass)
        issues = runtime.validator.validate_entity(entity_id, lambda: configs)

        _audit_admin(self.hass, request, rid, request.path)
        return _ok(
            {
                "entity_id": entity_id,
                "stored": profile.to_dict(),
                "warnings": [_issue_to_dict(i) for i in issues],
            },
            request_id=rid,
        )

    @require_admin
    async def delete(self, request: web.Request, entity_id: str) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        async with runtime.lock:
            runtime.store.delete(entity_id)
            await runtime.async_save()
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({"entity_id": entity_id, "deleted": True}, request_id=rid)


class ATMAdminMesaDomainView(HomeAssistantView):
    """GET/PUT/DELETE /api/atm/admin/mesa/domains/{domain} - one domain-level profile."""

    url = "/api/atm/admin/mesa/domains/{domain}"
    name = "api:atm:admin:mesa:domain"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, domain: str) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        stored = runtime.store.get_domain_profile(domain)
        return _ok(
            {"domain": domain, "stored": stored.to_dict() if stored is not None else None},
            request_id=rid,
        )

    @require_admin
    async def put(self, request: web.Request, domain: str) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        if not _DOMAIN_RE.match(domain):
            return _err("invalid_request", "Invalid domain name.", 400, rid)
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        from .mesa_core import MetadataOrigin, SemanticProfile  # noqa: PLC0415
        from .mesa_core.exceptions import MesaValidationError  # noqa: PLC0415

        try:
            profile = SemanticProfile.from_dict(domain, body, default_origin=MetadataOrigin.USER)
        except MesaValidationError as exc:
            return _err("invalid_request", str(exc), 400, rid)

        async with runtime.lock:
            runtime.store.set_domain_profile(domain, profile)
            await runtime.async_save()
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({"domain": domain, "stored": profile.to_dict()}, request_id=rid)

    @require_admin
    async def delete(self, request: web.Request, domain: str) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        async with runtime.lock:
            runtime.store.delete_domain_profile(domain)
            await runtime.async_save()
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({"domain": domain, "deleted": True}, request_id=rid)


class ATMAdminMesaAreaView(HomeAssistantView):
    """GET/PUT/DELETE /api/atm/admin/mesa/areas/{area_id} - one area-level profile."""

    url = "/api/atm/admin/mesa/areas/{area_id}"
    name = "api:atm:admin:mesa:area"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, area_id: str) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        stored = runtime.store.get_area_profile(area_id)
        return _ok(
            {"area_id": area_id, "stored": stored.to_dict() if stored is not None else None},
            request_id=rid,
        )

    @require_admin
    async def put(self, request: web.Request, area_id: str) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        node_err = _validate_node_id("area", area_id, rid)
        if node_err is not None:
            return node_err
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body

        from .mesa_core import MetadataOrigin, SemanticProfile  # noqa: PLC0415
        from .mesa_core.exceptions import MesaValidationError  # noqa: PLC0415

        try:
            profile = SemanticProfile.from_dict(area_id, body, default_origin=MetadataOrigin.USER)
        except MesaValidationError as exc:
            return _err("invalid_request", str(exc), 400, rid)

        async with runtime.lock:
            runtime.store.set_area_profile(area_id, profile)
            await runtime.async_save()
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({"area_id": area_id, "stored": profile.to_dict()}, request_id=rid)

    @require_admin
    async def delete(self, request: web.Request, area_id: str) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        async with runtime.lock:
            runtime.store.delete_area_profile(area_id)
            await runtime.async_save()
        _audit_admin(self.hass, request, rid, request.path)
        return _ok({"area_id": area_id, "deleted": True}, request_id=rid)


class ATMAdminMesaDefaultsView(HomeAssistantView):
    """GET/PUT /api/atm/admin/mesa/defaults - deployment defaults for unprofiled entities."""

    url = "/api/atm/admin/mesa/defaults"
    name = "api:atm:admin:mesa:defaults"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        defaults = runtime.store.get_deployment_defaults()
        return _ok(
            {"deployment_defaults": defaults.to_dict() if defaults is not None else None},
            request_id=rid,
        )

    @require_admin
    async def put(self, request: web.Request) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        try:
            async with runtime.lock:
                runtime.store.set_deployment_defaults(body)
                await runtime.async_save()
        except (ValueError, KeyError) as exc:
            return _err("invalid_request", f"Invalid deployment defaults: {exc}", 400, rid)
        _audit_admin(self.hass, request, rid, request.path)
        stored = runtime.store.get_deployment_defaults()
        return _ok(
            {"deployment_defaults": stored.to_dict() if stored is not None else None},
            request_id=rid,
        )


class ATMAdminMesaIssuesView(HomeAssistantView):
    """GET /api/atm/admin/mesa/issues - TriggerValidator issues and orphaned profiles."""

    url = "/api/atm/admin/mesa/issues"
    name = "api:atm:admin:mesa:issues"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request["atm_rid"]
        runtime, err = _mesa_runtime(self.hass, rid)
        if err is not None:
            return err
        if request.query.get("refresh"):
            from .mesa import async_refresh_trigger_issues, refresh_orphans  # noqa: PLC0415

            await async_refresh_trigger_issues(self.hass, runtime)
            refresh_orphans(self.hass, runtime)
        return _ok(
            {
                "issues": [_issue_to_dict(i) for i in runtime.trigger_issues],
                "orphans": list(runtime.orphans),
            },
            request_id=rid,
        )


def _issue_to_dict(issue) -> dict:
    """Serialise a mesa-core ValidationIssue dataclass."""
    import dataclasses  # noqa: PLC0415

    return dataclasses.asdict(issue)


ALL_ADMIN_VIEWS: list[type[HomeAssistantView]] = [
    ATMAdminInfoView,
    ATMAdminArchivedTokensView,
    ATMAdminArchivedTokenView,
    ATMAdminTokensView,
    ATMAdminTokenView,
    ATMAdminPermissionsView,
    ATMAdminPermissionDomainView,
    ATMAdminPermissionDeviceView,
    ATMAdminPermissionEntityView,
    ATMAdminResolveView,
    ATMAdminTokenRotateView,
    ATMAdminScopeView,
    ATMAdminEntityTreeView,
    ATMAdminTokenStatsView,
    ATMAdminTokenAuditView,
    ATMAdminAuditView,
    ATMAdminSettingsView,
    ATMAdminWipeView,
    ATMAdminApprovalsView,
    ATMAdminApprovalView,
    ATMAdminApprovalApproveView,
    ATMAdminApprovalRejectView,
    ATMAdminMesaProfilesView,
    ATMAdminMesaProfileView,
    ATMAdminMesaDomainView,
    ATMAdminMesaAreaView,
    ATMAdminMesaDefaultsView,
    ATMAdminMesaIssuesView,
]
