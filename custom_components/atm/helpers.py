"""Shared helpers used by multiple ATM views."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aiohttp import web
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util.dt import parse_datetime, utcnow

from .const import (
    BLOCKED_DOMAINS,
    CAP_ALLOW,
    CAP_CONFIRM,
    CAP_DENY,
    CAPABILITY_NAMES,
    DOMAIN,
    MAX_REQUEST_BODY_BYTES,
    PASS_THROUGH_EXEMPT_CAPS,
    SENSITIVE_ATTRIBUTES,
    TOKEN_LENGTH,
    TOKEN_PREFIX,
)
from .policy_engine import (
    Permission,
    is_sensitive_key,
    parse_relative_time,
    resolve,
    template_blocklist_vars,
)
from .token_store import token_name_slug

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import ATMData
    from .rate_limiter import RateLimitResult
    from .token_store import TokenRecord

_LOGGER = logging.getLogger(__name__)


def build_permitted_states(token: TokenRecord, hass: HomeAssistant) -> dict:
    """Return a {entity_id: ScrubbedState} dict for entities accessible to a token.

    For pass_through tokens this includes every entity except those in BLOCKED_DOMAINS,
    entities registered to the ATM platform (sensor.atm_* telemetry sensors), and -
    when use_assist_exposure is True - entities not exposed to HA Assist.
    For scoped tokens only READ/WRITE-accessible entities are included.

    This is the single source of truth for template sandboxes in both proxy_view.py
    and mcp_view.py. All template handlers must use this function so the ATM-platform
    check and use_assist_exposure filter never diverge.
    """
    from homeassistant.helpers import entity_registry as er_mod

    if token.pass_through:
        registry = er_mod.async_get(hass)
        _expose_check = None
        if token.use_assist_exposure:
            from homeassistant.components.homeassistant.exposed_entities import (  # noqa: PLC0415
                async_should_expose as _should_expose,
            )
            _expose_check = lambda eid: _should_expose(hass, "conversation", eid)
        result: dict = {}
        for s in hass.states.async_all():
            eid = s.entity_id
            if eid.split(".")[0] in BLOCKED_DOMAINS:
                continue
            entry = registry.async_get(eid)
            if entry is not None and entry.platform == DOMAIN:
                continue
            if _expose_check is not None and not _expose_check(eid):
                continue
            result[eid] = ScrubbedState(s)
        return result
    return {
        s.entity_id: ScrubbedState(s)
        for s in hass.states.async_all()
        if resolve(s.entity_id, token, hass) in (Permission.READ, Permission.WRITE)
    }


def build_permitted_entity_ids(token: TokenRecord, hass: HomeAssistant) -> set:
    """Return the set of entity IDs accessible to a token, including registry-only entities.

    Unlike build_permitted_states (which needs current State objects), this function
    unions live states with the entity registry so that history and statistics endpoints
    can query recorder data for entities that are temporarily offline or disabled.
    Also applies use_assist_exposure filtering for pass_through tokens.
    """
    from homeassistant.helpers import entity_registry as er_mod

    registry = er_mod.async_get(hass)
    candidate_ids: set[str] = {s.entity_id for s in hass.states.async_all()}
    candidate_ids.update(entry.entity_id for entry in registry.entities.values())

    if token.pass_through:
        _expose_check = None
        if token.use_assist_exposure:
            from homeassistant.components.homeassistant.exposed_entities import (  # noqa: PLC0415
                async_should_expose as _should_expose,
            )
            _expose_check = lambda eid: _should_expose(hass, "conversation", eid)
        return {
            eid for eid in candidate_ids
            if eid.split(".")[0] not in BLOCKED_DOMAINS
            and not (
                (entry := registry.async_get(eid)) is not None
                and entry.platform == DOMAIN
            )
            and (_expose_check is None or _expose_check(eid))
        }
    return {
        eid for eid in candidate_ids
        if resolve(eid, token, hass) in (Permission.READ, Permission.WRITE)
    }


def build_error_response(
    code: str,
    message: str,
    status: int,
    request_id: str,
    suggestions: list[str] | None = None,
) -> web.Response:
    """Return a JSON error response with an X-ATM-Request-ID header."""
    body: dict[str, Any] = {"error": code, "message": message}
    if suggestions:
        body["suggestions"] = suggestions
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps(body),
        headers={"X-ATM-Request-ID": request_id},
    )


def get_client_ip(request: web.Request) -> str:
    """Return the remote IP address, or an empty string if unavailable."""
    return request.remote or ""


def log_request(
    data: ATMData,
    token: TokenRecord,
    *,
    request_id: str,
    method: str,
    resource: str,
    outcome: str,
    client_ip: str,
    payload: dict | None = None,
    mesa_advisory: bool = False,
) -> None:
    """Record an audit entry and update in-memory token counters."""
    data.audit.record(
        request_id=request_id,
        token_id=token.id,
        token_name=token.name,
        method=method,
        resource=resource,
        outcome=outcome,
        client_ip=client_ip,
        settings=data.store.get_settings(),
        pass_through=token.pass_through,
        payload=payload,
        mesa_advisory=mesa_advisory,
    )
    update_token_counter(data, token.id, outcome)


def fire_rate_limit_events(hass: HomeAssistant, data: ATMData, token: TokenRecord) -> None:
    """Fire the atm_rate_limited bus event and optional persistent notification.

    The event fires on every 429 (spec §3.8 item 4 has no throttle qualifier).
    The persistent notification is throttled to at most once per token per minute
    to prevent notification flooding during sustained abuse (spec §3.8 item 3).
    """
    # Event fires on every 429 - not throttled.
    hass.bus.async_fire("atm_rate_limited", {
        "token_id": token.id,
        "token_name": token.name,
        "timestamp": utcnow().isoformat(),
    })
    # Notification is throttled.
    settings = data.store.get_settings()
    if settings.notify_on_rate_limit:
        now_mono = time.monotonic()
        last = data.rate_limit_notified.get(token.id, 0.0)
        if now_mono - last >= 60.0:
            data.rate_limit_notified[token.id] = now_mono
            hass.async_create_task(
                hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "message": f"ATM: token '{token.name}' has hit its rate limit.",
                        "title": "ATM Alert",
                        "notification_id": f"atm_rate_limit_{token.id}",
                    },
                )
            )


async def read_json_body(request: web.Request, request_id: str) -> dict | web.Response:
    """Read and size-check the request body, return a parsed dict or an error response."""
    if request.content_length is not None and request.content_length > MAX_REQUEST_BODY_BYTES:
        return build_error_response("request_too_large", "Request body too large.", 413, request_id)

    try:
        body_bytes = await request.content.read(MAX_REQUEST_BODY_BYTES + 1)
    except Exception:
        return build_error_response("invalid_request", "Failed to read request body.", 400, request_id)

    if len(body_bytes) > MAX_REQUEST_BODY_BYTES:
        return build_error_response("request_too_large", "Request body too large.", 413, request_id)

    if not body_bytes:
        return {}

    try:
        parsed = json.loads(body_bytes)
    except json.JSONDecodeError:
        return build_error_response("invalid_request", "Invalid JSON body.", 400, request_id)

    if not isinstance(parsed, dict):
        return build_error_response("invalid_request", "Request body must be a JSON object.", 400, request_id)

    return parsed


def parse_time_param(value: str) -> datetime:
    """Parse a relative time string or ISO timestamp. Raises ValueError for unknown formats."""
    try:
        return parse_relative_time(value)
    except ValueError:
        pass
    dt = parse_datetime(value)
    if dt is None:
        raise ValueError(f"Unrecognized time format: {value!r}")
    return dt


async def get_authenticated_token(
    hass: HomeAssistant,
    request: web.Request,
    data: ATMData,
    request_id: str,
    resource: str,
) -> tuple[TokenRecord, RateLimitResult] | web.Response:
    """Validate the ATM bearer token and check rate limits.

    Returns (token, rl_result) on success, or an aiohttp Response on failure.
    Checks for kill switch, query-param token leakage, format pre-validation,
    hash lookup, revocation, expiry, and rate limits in that order.
    """
    if data.shutting_down:
        return build_error_response("service_unavailable", "Service unavailable.", 503, request_id)

    if data.store.get_settings().kill_switch:
        # Spec §4.1 says kill-switch mode should make ATM "invisible on the network."
        # At startup that is achieved by not registering any routes. At runtime, aiohttp
        # does not support unregistering routes, so 503 is the closest approximation.
        # This is a known architectural limitation; the routes exist but refuse service.
        return build_error_response("service_unavailable", "Service unavailable.", 503, request_id)

    _401 = build_error_response("unauthorized", "Unauthorized.", 401, request_id)
    _401.headers["WWW-Authenticate"] = 'Bearer realm="ATM"'

    for key in ("token", "access_token"):
        if key in request.query:
            return _401

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return _401

    presented = auth_header[7:]
    if not presented.startswith(TOKEN_PREFIX) or len(presented) != TOKEN_LENGTH:
        return _401

    token_hash = hashlib.sha256(presented.encode()).hexdigest()
    token = data.store.get_token_by_hash(token_hash)

    if token is None:
        return _401

    if not token.is_valid():
        if token.is_expired():
            await archive_expired_token(hass, data, token)
        return _401

    # Update last_used before the rate limit check so last_access reflects every
    # attempted request, not just allowed ones. This keeps last_access consistent
    # with request_count, which also increments on rate-limited requests.
    data.store.update_last_used(token.id, utcnow())

    rl_result = data.rate_limiter.check(
        token.id,
        token.rate_limit_requests,
        token.rate_limit_burst,
    )

    if not rl_result.allowed:
        fire_rate_limit_events(hass, data, token)
        log_request(
            data,
            token,
            request_id=request_id,
            method=request.method,
            resource=resource,
            outcome="rate_limited",
            client_ip=get_client_ip(request),
        )
        resp = build_error_response("rate_limited", "Rate limit exceeded.", 429, request_id)
        resp.headers["Retry-After"] = str(rl_result.retry_after)
        return resp

    return token, rl_result


def cancel_expiry_timer(data: ATMData, token_id: str) -> None:
    """Cancel and remove the pending expiry timer for a token, if one exists."""
    cancel = data.expiry_timers.pop(token_id, None)
    if cancel is not None:
        cancel()


def schedule_expiry_timer(hass: HomeAssistant, data: ATMData, token: TokenRecord) -> None:
    """Schedule a timer to archive a token at its expiry time.

    If the token has no expiry, or has already expired, no timer is scheduled.
    Any previously registered timer for this token is cancelled first.
    """
    if token.expires_at is None:
        return
    cancel_expiry_timer(data, token.id)
    delay = (token.expires_at - utcnow()).total_seconds()
    if delay <= 0:
        return

    @callback
    def _on_expiry(_now=None) -> None:
        data.expiry_timers.pop(token.id, None)
        hass.async_create_background_task(
            archive_expired_token(hass, data, token),
            f"atm_expire_{token.id}",
        )

    data.expiry_timers[token.id] = async_call_later(hass, delay, _on_expiry)


async def archive_expired_token(
    hass: HomeAssistant,
    data: ATMData,
    token: TokenRecord,
) -> None:
    """Move an expired token to the archive and perform full cleanup.

    Archives the record to storage, destroys rate limiter and counter state,
    fires the atm_token_expired bus event, and removes sensor entities.
    """
    now = utcnow()
    slug = token_name_slug(token.name)
    cancel_expiry_timer(data, token.id)
    archived = await data.store.async_archive_token(token.id, revoked=False, revoked_at=now)
    if archived is None:
        return
    data.rate_limiter.destroy(token.id)
    data.rate_limit_notified.pop(token.id, None)
    data.token_counters.pop(token.id, None)
    hass.bus.async_fire("atm_token_expired", {
        "token_id": token.id,
        "token_name": token.name,
        "timestamp": now.isoformat(),
    })
    if data.async_on_token_archived:
        try:
            await data.async_on_token_archived(slug)
        except Exception:
            _LOGGER.warning(
                "Sensor cleanup failed for expired token %s", token.id, exc_info=True,
            )


class _ContextProxy(dict):
    """Dict subclass that also supports attribute access.

    Used by ScrubbedState.context so templates can use both context.id and
    context | tojson without TypeError. Behaves as a plain dict for json.dumps().
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class ScrubbedState:
    """Lightweight State wrapper that strips sensitive attributes for use in template sandboxes."""

    def __init__(self, raw: Any) -> None:
        self.entity_id = raw.entity_id
        self.state = raw.state
        self.attributes = {k: v for k, v in raw.attributes.items() if k not in SENSITIVE_ATTRIBUTES}
        self.last_updated = getattr(raw, "last_updated", None)
        self.last_changed = getattr(raw, "last_changed", None)
        self.last_reported = getattr(raw, "last_reported", None)
        # Strip user_id from context to prevent HA user ID enumeration via templates.
        ctx = getattr(raw, "context", None)
        if ctx is not None:
            self.context = _ContextProxy({
                "id": getattr(ctx, "id", None),
                "parent_id": getattr(ctx, "parent_id", None),
                "user_id": None,
            })
        else:
            self.context = None

    @property
    def domain(self) -> str:
        return self.entity_id.split(".")[0]

    @property
    def object_id(self) -> str:
        return self.entity_id.split(".", 1)[1] if "." in self.entity_id else self.entity_id

    @property
    def name(self) -> str:
        friendly = self.attributes.get("friendly_name")
        if friendly:
            return str(friendly)
        return self.object_id.replace("_", " ").title()

    def as_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "state": self.state,
            "attributes": self.attributes,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "last_changed": self.last_changed.isoformat() if self.last_changed else None,
            "context": {
                "id": getattr(self.context, "id", None),
                "parent_id": getattr(self.context, "parent_id", None),
                "user_id": None,
            } if self.context is not None else None,
        }


class _DomainFilteredStates:
    """Iterable wrapper for a single domain's entities inside FilteredStates.

    Supports both iteration ({% for state in states.light %}) yielding
    ScrubbedState objects, and attribute access (states.light.living_room)
    returning individual entities by object_id.
    """

    def __init__(self, entities: dict) -> None:
        self._entities = entities

    def __iter__(self):
        return iter(self._entities.values())

    def __len__(self) -> int:
        return len(self._entities)

    def __getattr__(self, object_id: str):
        if object_id.startswith("_"):
            raise AttributeError(object_id)
        return self._entities.get(object_id)


class FilteredStates:
    """Callable proxy over a permitted-entity dict mimicking the HA template 'states' global.

    HA templates use 'states' as both a callable (states('sensor.foo')) and a
    domain-keyed accessor (states.light). A plain dict breaks the callable form,
    so this proxy implements both protocols while restricting access to permitted entities.
    """

    def __init__(self, permitted: dict) -> None:
        self._permitted = permitted

    def __call__(self, entity_id: str, default: str = "unknown") -> str:
        s = self._permitted.get(entity_id)
        return s.state if s is not None else default

    def __getitem__(self, entity_id: str):
        return self._permitted.get(entity_id)

    def __iter__(self):
        return iter(self._permitted.values())

    def __len__(self) -> int:
        return len(self._permitted)

    def __getattr__(self, domain: str):
        if domain.startswith("_"):
            raise AttributeError(domain)
        entities = {
            eid.split(".", 1)[1]: s
            for eid, s in self._permitted.items()
            if eid.split(".")[0] == domain
        }
        return _DomainFilteredStates(entities)


_SAFE_TEMPLATE_ENV = None


def safe_template_env():
    """Return the cached hass-less TemplateEnvironment used for token template renders.

    A TemplateEnvironment constructed without hass never registers the hass-aware
    helpers (states, expand, area_entities, integration_entities, ...) as globals,
    filters, or tests, so entity access exists only through the permission-filtered
    variables ATM injects. Rendering in the full hass environment and shadowing
    globals with render variables is NOT safe: Jinja2 variables never shadow
    filters or tests, so {{ 'sensor.x' | states }} would bypass the sandbox.
    """
    global _SAFE_TEMPLATE_ENV
    if _SAFE_TEMPLATE_ENV is None:
        from homeassistant.helpers.template import TemplateEnvironment  # noqa: PLC0415
        _SAFE_TEMPLATE_ENV = TemplateEnvironment(None)
    return _SAFE_TEMPLATE_ENV


def render_template_for_token(template_str: str, token: TokenRecord, hass: HomeAssistant) -> str:
    """Render a Jinja2 template against the token's permitted entity state.

    Renders in safe_template_env() with permission-restricted replacements for the
    HA state helpers and pure dt_util shims for the time helpers the hass-less
    environment lacks. Raises (TemplateError, ValueError, jinja2 errors) on any
    failure; callers map exceptions to an invalid_request response.
    """
    from homeassistant.helpers.template import MAX_TEMPLATE_OUTPUT  # noqa: PLC0415
    from homeassistant.exceptions import TemplateError  # noqa: PLC0415
    from homeassistant.util import dt as dt_util  # noqa: PLC0415

    permitted = build_permitted_states(token, hass)
    filtered_states = FilteredStates(permitted)

    # Permission-restricted versions of the HA template state helpers.
    def _state_attr(entity_id: str, attr: str):
        s = permitted.get(entity_id)
        return s.attributes.get(attr) if s is not None else None

    def _is_state(entity_id: str, value: str) -> bool:
        s = permitted.get(entity_id)
        return s is not None and s.state == value

    def _is_state_attr(entity_id: str, attr: str, value) -> bool:
        s = permitted.get(entity_id)
        return s is not None and s.attributes.get(attr) == value

    def _has_value(entity_id: str) -> bool:
        s = permitted.get(entity_id)
        return s is not None and s.state not in ("unknown", "unavailable")

    # Time helpers absent from the hass-less environment. These mirror HA's
    # DateTimeExtension implementations; they touch only dt_util, never entities.
    def _today_at(time_str: str = "") -> datetime:
        today = dt_util.start_of_local_day()
        if not time_str:
            return today
        parsed = dt_util.parse_time(time_str)
        if parsed is None:
            raise ValueError(f"could not convert str to datetime: '{time_str}'")
        return datetime.combine(today, parsed, today.tzinfo)

    def _localize(value: datetime) -> datetime:
        return value if value.tzinfo else dt_util.as_local(value)

    def _relative_time(value):
        if not isinstance(value, datetime):
            return value
        value = _localize(value)
        return value if dt_util.now() < value else dt_util.get_age(value)

    def _time_since(value, precision: int = 1):
        if not isinstance(value, datetime):
            return value
        value = _localize(value)
        return value if dt_util.now() < value else dt_util.get_age(value, precision)

    def _time_until(value, precision: int = 1):
        if not isinstance(value, datetime):
            return value
        value = _localize(value)
        return value if dt_util.now() > value else dt_util.get_time_remaining(value, precision)

    variables = {
        "states": filtered_states,
        "state_attr": _state_attr,
        "is_state": _is_state,
        "is_state_attr": _is_state_attr,
        "has_value": _has_value,
        "now": dt_util.now,
        "utcnow": dt_util.utcnow,
        "today_at": _today_at,
        "relative_time": _relative_time,
        "time_since": _time_since,
        "time_until": _time_until,
        # Defense in depth: these names do not exist in the hass-less environment,
        # but spec section 3.4 requires the enumeration helpers to return empty
        # values rather than raise, so keep the stubs as render variables.
        **template_blocklist_vars(),
    }

    compiled = safe_template_env().from_string(template_str)
    rendered = compiled.render(variables)
    if len(rendered) > MAX_TEMPLATE_OUTPUT:
        raise TemplateError(
            f"Template output exceeded maximum size of {MAX_TEMPLATE_OUTPUT} characters"
        )
    return rendered.strip()


_LOG_LEVEL_RANK: dict[str, int] = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 3}
_ATM_TOKEN_SCRUB_RE = re.compile(r"atm_[0-9a-f]{64}", re.IGNORECASE)
# Home Assistant long-lived access tokens (and other JWTs) are three base64url
# segments whose header always begins "eyJ". Redact so a leaked LLAT in a log
# line is not handed back to a token holding cap_log_read.
_JWT_SCRUB_RE = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}")
# Credentials embedded in URLs/log text: query params (token=..., api_password=...)
# and userinfo (https://user:pass@host). Over-redaction in logs is acceptable.
_URL_CRED_QUERY_RE = re.compile(
    r"(?i)(access_token|refresh_token|api_password|password|api_key|apikey|client_secret|secret|token|auth)=[^\s&\"';]+"
)
_URL_CRED_USERINFO_RE = re.compile(r"://[^/\s:@]+:[^/\s:@]+@")
_ATM_LOGGER_PREFIXES = ("homeassistant.components.atm", "custom_components.atm")


def _scrub_log_text(text: str) -> str:
    """Redact ATM tokens, JWTs/LLATs, and URL-embedded credentials from a log line."""
    text = _ATM_TOKEN_SCRUB_RE.sub("<atm-token>", text)
    text = _JWT_SCRUB_RE.sub("<token>", text)
    text = _URL_CRED_QUERY_RE.sub(r"\1=<redacted>", text)
    text = _URL_CRED_USERINFO_RE.sub("://<redacted>@", text)
    return text


# A "key: value" or "key = value" line in a YAML/config diff, capturing the
# leading key so its value can be redacted when the key name looks sensitive.
_CONFIG_SECRET_LINE_RE = re.compile(r"^(\s*)([\w.\-]+)(\s*[:=]\s*)(\S.*)$")


def redact_secrets_in_text(text: str | None) -> str | None:
    """Redact secret-valued config lines and embedded credentials from diff text.

    Applied to approval diffs (file writes, configuration.yaml edits) before they
    persist to .storage so secrets are not copied to disk verbatim. A line whose
    key name is sensitive (is_sensitive_key) has its value replaced; JWTs and
    URL-embedded credentials anywhere in the text are scrubbed too. The structure
    of the change stays visible to the reviewing admin.
    """
    if not text:
        return text
    out: list[str] = []
    for line in text.split("\n"):
        m = _CONFIG_SECRET_LINE_RE.match(line)
        if m is not None and is_sensitive_key(m.group(2)):
            out.append(f"{m.group(1)}{m.group(2)}{m.group(3)}<redacted>")
        else:
            out.append(line)
    return _scrub_log_text("\n".join(out))


def redact_structure(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact secrets from a JSON-able structure.

    A dict value whose key name is sensitive (is_sensitive_key) becomes
    "<redacted>"; string values are scrubbed for secret-valued config lines and
    embedded credentials (redact_secrets_in_text). Other scalars pass through
    unchanged. Used for audit payloads and the admin-facing copy of approval
    args so secrets are never serialised verbatim. Recursion is depth-bounded so
    a pathologically nested payload cannot raise RecursionError on the logging
    path; subtrees past the limit collapse to "<redacted>".
    """
    if _depth > 25:
        return "<redacted>"
    if isinstance(obj, dict):
        return {
            k: "<redacted>" if is_sensitive_key(k) else redact_structure(v, _depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_structure(item, _depth + 1) for item in obj]
    if isinstance(obj, str):
        return redact_secrets_in_text(obj)
    return obj


# Network-topology scrubbing for integration-defined diagnostics (get_system_health).
# ATM already withholds network topology / host layout from agents elsewhere
# (build_safe_config drops internal_url/external_url/config_dir/paths from get_config),
# but system_health values are integration-defined free-form strings that can carry the
# same infrastructure detail (LAN IPs, hostnames inside URLs, filesystem paths), which
# redact_structure (secret-keyed values + embedded credentials only) does not catch.
# These patterns are deliberately conservative. The IPv4 set is restricted to
# PRIVATE/loopback/link-local ranges, so a public-IP-shaped version string like
# "4.8.0.1" is NOT matched (it is outside these ranges); that avoids the main
# false-positive while still scrubbing the LAN topology that is the actual concern.
_PRIVATE_IPV4_RE = re.compile(
    r"\b(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"                  # 10.0.0.0/8
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"               # loopback 127.0.0.0/8
    r"|169\.254\.\d{1,3}\.\d{1,3}"                   # link-local 169.254.0.0/16
    r"|192\.168\.\d{1,3}\.\d{1,3}"                   # 192.168.0.0/16
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"  # 172.16.0.0/12
    r")\b"
)
# Link-local (fe80::/10) and unique-local (fc00::/7) IPv6.
_PRIVATE_IPV6_RE = re.compile(
    r"\b(?:fe80|f[cd][0-9a-f]{2})(?::[0-9a-f]{0,4}){2,7}\b", re.IGNORECASE
)
# Bare http(s) URL (host + optional port/path). URL-embedded credentials are already
# scrubbed upstream; this removes the host/topology itself.
_BARE_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
# Absolute filesystem paths: unix /a/b... (>=2 segments, so a lone "/" or a URL path
# already handled above is not matched) and Windows drive paths C:\a\b\... .
_UNIX_PATH_RE = re.compile(r"(?<![\w/])/(?:[\w.\-]+/)+[\w.\-]+")
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[\w.\-]+\\?){2,}")


def _scrub_network_topology(text: str) -> str:
    """Replace LAN IPs, bare URLs, and absolute filesystem paths with placeholders."""
    text = _BARE_URL_RE.sub("<redacted-url>", text)
    text = _PRIVATE_IPV4_RE.sub("<redacted-ip>", text)
    text = _PRIVATE_IPV6_RE.sub("<redacted-ip>", text)
    text = _WIN_PATH_RE.sub("<redacted-path>", text)
    text = _UNIX_PATH_RE.sub("<redacted-path>", text)
    return text


def _scrub_diagnostic_str(text: str) -> str:
    """Full diagnostic-string scrub: embedded credentials/JWTs then network topology."""
    return _scrub_network_topology(redact_secrets_in_text(text) or "")


def redact_diagnostics(obj: Any, _depth: int = 0) -> Any:
    """redact_structure plus a conservative network-topology scrub for diagnostics.

    get_system_health values are integration-defined and can disclose LAN IPs,
    hostnames-in-URLs, and filesystem paths that ATM already withholds elsewhere
    (build_safe_config). Layered on redact_structure (secret-keyed values + embedded
    credentials), each string is also scrubbed for private/loopback/link-local IPs,
    bare URLs, and absolute paths. Because integration-defined payloads are free-form,
    dict KEYS get the same string scrub as values (an integration may key its health
    data by a URL, LAN IP, or path); a sensitive-named key still redacts its value.
    cap_diagnostics is an elevated read, so a little over-redaction is acceptable; the
    diagnostic shape is preserved.
    """
    if _depth > 25:
        return "<redacted>"
    if isinstance(obj, dict):
        return {
            (_scrub_diagnostic_str(k) if isinstance(k, str) else k):
                "<redacted>" if is_sensitive_key(k) else redact_diagnostics(v, _depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_diagnostics(item, _depth + 1) for item in obj]
    if isinstance(obj, str):
        return _scrub_diagnostic_str(obj)
    return obj


# Config keys safe to disclose to a cap_config_read token. Allowlist, not denylist,
# so a new HA config key defaults to excluded. Deliberately omits precise location
# (latitude/longitude/elevation/radius), internal_url/external_url, and filesystem
# paths (config_dir, allowlist_external_dirs/urls, media_dirs) which reveal home
# coordinates, network topology, and host layout beyond what an agent needs.
_SAFE_CONFIG_KEYS = frozenset({
    "location_name", "time_zone", "unit_system", "currency", "country",
    "language", "version", "config_source", "state", "safe_mode",
    "recovery_mode", "components",
})


def build_safe_config(hass: Any) -> dict:
    """Return the cap_config_read-safe subset of hass.config.as_dict().

    Agent-useful context (HA version, time zone, units, location name, loaded
    components for capability detection) with the sensitive fields removed; ATM's
    own components are stripped so a token cannot enumerate our routes.
    """
    raw = hass.config.as_dict()
    safe = {k: raw[k] for k in _SAFE_CONFIG_KEYS if k in raw}
    if "components" in safe:
        safe["components"] = sorted(
            c for c in safe["components"]
            if c != DOMAIN and not c.startswith(DOMAIN + ".")
        )
    return safe


def collect_log_entries(hass: Any, level: str, integration: str | None, limit: int) -> list[dict]:
    """Read system_log records, filter, scrub, and return newest-first.

    Accesses hass.data["system_log"].records directly - this is an undocumented
    HA internal API with no public alternative. Falls back to an empty list if
    the structure changes across HA versions.
    """
    min_rank = _LOG_LEVEL_RANK.get(level.upper(), _LOG_LEVEL_RANK["WARNING"])
    syslog = hass.data.get("system_log")
    if syslog is None:
        return []
    records = getattr(syslog, "records", {})
    entries: list[dict] = []
    for record in records.values():
        record_level = getattr(record, "level", "")
        if _LOG_LEVEL_RANK.get(record_level, -1) < min_rank:
            continue
        logger_name = getattr(record, "name", "")
        if any(logger_name.startswith(pfx) for pfx in _ATM_LOGGER_PREFIXES):
            continue
        if integration:
            if not (
                logger_name.startswith(f"homeassistant.components.{integration}")
                or logger_name.startswith(f"custom_components.{integration}")
            ):
                continue
        messages = getattr(record, "message", [])
        msg = list(messages)[-1] if messages else ""
        exc_parts = getattr(record, "exception", [])
        exc_str: str | None = "".join(exc_parts) if exc_parts else None
        entries.append({
            "timestamp": getattr(record, "timestamp", 0),
            "first_occurred": getattr(record, "first_occurred", 0),
            "level": record_level,
            "logger": logger_name,
            "message": _scrub_log_text(msg),
            "exception": _scrub_log_text(exc_str) if exc_str else None,
            "occurrences": getattr(record, "count", 1),
        })
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return entries[:limit]


def update_token_counter(data: ATMData, token_id: str, outcome: str) -> None:
    """Increment the in-memory request/denied/rate-limit counters for a token.

    Counters are initialised on first use and read by sensor.py and the admin stats view.
    Calls async_write_ha_state() on each sensor for this token so HA reflects the new
    values immediately without polling.
    """
    if token_id not in data.token_counters:
        data.token_counters[token_id] = {
            "request_count": 0,
            "denied_count": 0,
            "rate_limit_hits": 0,
        }
    counters = data.token_counters[token_id]
    counters["request_count"] += 1
    if outcome in ("denied", "not_found"):
        counters["denied_count"] += 1
    elif outcome == "rate_limited":
        counters["rate_limit_hits"] += 1

    for sensor in data.token_id_sensors.get(token_id, []):
        if sensor.hass is not None:
            sensor.async_write_ha_state()


# Capability evaluation
# ---------------------
# evaluate_capability returns one of three results:
#   ("allow", None)          -> proceed to side-effect.
#   ("deny", None)           -> return forbidden to caller.
#   ("confirm", approval_id) -> create pending approval, return pending response.
#
# effective_cap collapses pass_through interaction into a single value used by
# self-summary endpoints. It is NOT a substitute for evaluate_capability when
# enforcing a check, because it does not go through the approval queue.


def effective_cap(token: TokenRecord, cap_name: str) -> str:
    """Return the cap mode after applying pass_through interaction rules.

    Exempt caps are unaffected by pass_through. For non-exempt caps under
    pass_through, "deny" becomes "allow" but "confirm" is preserved
    (the admin's intent to gate is honored).
    """
    raw = getattr(token, cap_name, CAP_DENY)
    if cap_name in PASS_THROUGH_EXEMPT_CAPS:
        return raw
    if token.pass_through:
        if raw == CAP_CONFIRM:
            return CAP_CONFIRM
        return CAP_ALLOW
    return raw


def token_has_write_scope(token: TokenRecord) -> bool:
    """True if the token can write to at least one entity.

    Used to decide whether to announce the control tools (call_service, the
    native Hass* action tools) in the MCP tools/list. Pass-through always has
    write scope; otherwise any GREEN grant in the permission tree counts. This
    is an advisory over-approximation (a GREEN under a RED ancestor still counts
    here), which is fine: the per-call permission check is the real gate.
    """
    if token.pass_through:
        return True
    tree = token.permissions
    for nodes in (tree.domains, tree.devices, tree.entities):
        for node in nodes.values():
            if node.state == "GREEN":
                return True
    return False


def effective_caps(token: TokenRecord) -> dict[str, str]:
    """Return the full cap_*->effective_mode mapping for a token."""
    return {name: effective_cap(token, name) for name in CAPABILITY_NAMES}


@dataclass
class CapabilityResult:
    """Outcome of an evaluate_capability call.

    mode is one of "allow" / "deny" / "confirm". When mode is "confirm",
    approval is the freshly created PendingApproval record and the caller
    must return a pending response without executing.
    """

    mode: str
    approval: Any | None = None

    @property
    def is_allow(self) -> bool:
        return self.mode == CAP_ALLOW

    @property
    def is_deny(self) -> bool:
        return self.mode == CAP_DENY

    @property
    def is_pending(self) -> bool:
        return self.mode == CAP_CONFIRM


async def evaluate_capability(
    cap_name: str,
    token: TokenRecord,
    hass: HomeAssistant,
    data: ATMData,
    *,
    tool_name: str,
    args: dict,
    request_id: str,
    diff: dict | None = None,
    client_ip: str | None = None,
) -> CapabilityResult:
    """Resolve a capability check into Allow / Deny / Pending(approval).

    Reads the effective cap mode (after pass-through interaction) and either
    permits, denies, or creates a pending approval and returns a Confirm result.
    Diff is supplied by the caller; it appears in the admin review UI.
    """
    from .approvals import (  # noqa: PLC0415
        create_approval_notification,
        create_pending_approval,
        fire_approval_requested_event,
    )

    mode = effective_cap(token, cap_name)
    if mode == CAP_ALLOW:
        return CapabilityResult(CAP_ALLOW)
    if mode == CAP_DENY:
        return CapabilityResult(CAP_DENY)
    async with data.store.async_lock:
        approval = await create_pending_approval(
            data.store,
            token_id=token.id,
            token_name=token.name,
            tool_name=tool_name,
            cap_name=cap_name,
            args=args,
            diff=diff or {},
            request_id=request_id,
            client_ip=client_ip,
        )
    create_approval_notification(hass, approval)
    fire_approval_requested_event(hass, approval)
    return CapabilityResult(CAP_CONFIRM, approval=approval)
