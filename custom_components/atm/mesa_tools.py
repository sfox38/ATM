"""Token-scoped wrappers around the vendored mesa-core retrieval tools.

mesa-core ships the four retrieval handlers (mesa_query_profiles,
mesa_get_profile, mesa_explain_profile, mesa_get_caller_context). ATM does not
reimplement them; it builds a per-request ScopedProfileStore so the handlers
only ever see entities the token may read, then delegates to mesa-core. This
keeps total_matched counts, pagination cursors, and the fingerprint all
scope-relative so there is no entity-enumeration oracle.

Out-of-scope and ghost entity lookups return the byte-identical mesa-core
not_found envelope, so an inaccessible entity is indistinguishable from a
nonexistent one.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from .const import CAP_DENY, MAX_PREVIEW_ENTITY_IDS
from .helpers import effective_cap
from .mesa import build_caller_context
from .mesa_core import InheritanceResolver, ProfileQueryResult, ProfileStore
from .mesa_core.mcp.schemas import TOOL_DESCRIPTIONS, TOOL_SCHEMAS
from .mesa_core.mcp.tools import MesaToolHandlers
from .policy_engine import Permission, resolve

if TYPE_CHECKING:
    from .data import ATMData
    from .mesa import MesaRuntime
    from .mesa_core import SemanticProfile
    from .token_store import TokenRecord

MESA_TOOL_NAMES = frozenset({
    "mesa_query_profiles",
    "mesa_get_profile",
    "mesa_explain_profile",
    "mesa_get_caller_context",
})

# Tools whose entity_id argument must be scope-checked before the handler runs.
_ENTITY_TARGETED = frozenset({"mesa_get_profile", "mesa_explain_profile"})

# All four mesa_* tools are gated by this capability: profiles are configuration
# metadata, the same sensitivity class as get_config.
MESA_TOOLS_CAP = "cap_config_read"


def mesa_tool_defs() -> list[dict[str, Any]]:
    """tools/list entries for the mesa_* tools, tagged with the gating cap.

    Built from the vendored MCP schemas/descriptions so ATM never re-specifies
    them. mcp_view strips the 'cap' key before exposing the tool.
    """
    return [
        {
            "name": name,
            "description": TOOL_DESCRIPTIONS[name],
            "cap": MESA_TOOLS_CAP,
            "inputSchema": TOOL_SCHEMAS[name],
        }
        for name in ("mesa_query_profiles", "mesa_get_profile",
                     "mesa_explain_profile", "mesa_get_caller_context")
    ]


def _not_found_envelope(entity_id: str) -> dict[str, Any]:
    """Replicate mesa_core.mcp.tools._error('not_found', ...) byte-for-byte."""
    return {
        "error": "not_found",
        "message": f"entity {entity_id!r} has no MESA profile at any level",
        "details": {},
    }


def _is_visible(entity_id: str, token: TokenRecord, hass: Any) -> bool:
    """True when the token may read the entity (READ or WRITE)."""
    return resolve(entity_id, token, hass) in (Permission.READ, Permission.WRITE)


class ScopedProfileStore:
    """A read-only ProfileStore view filtered to one token's permission scope.

    Entity-level reads are hidden when the token cannot read the entity, so the
    delegated mesa-core handlers and resolver never observe out-of-scope
    entities. Domain, area, and deployment-default profiles are deployment-wide
    metadata (not entity-existence facts) and pass through unfiltered.
    """

    def __init__(self, inner, token: TokenRecord, hass: Any) -> None:
        self._inner = inner
        self._token = token
        self._hass = hass

    def entity_keys(self) -> list[str]:
        return [k for k in self._inner.entity_keys() if _is_visible(k, self._token, self._hass)]

    def get(self, entity_id: str) -> SemanticProfile | None:
        if not _is_visible(entity_id, self._token, self._hass):
            return None
        return self._inner.get(entity_id)

    def get_domain_profile(self, domain: str) -> SemanticProfile | None:
        return self._inner.get_domain_profile(domain)

    def get_integration_profile(self, integration: str) -> SemanticProfile | None:
        return self._inner.get_integration_profile(integration)

    def get_area_profile(self, area_id: str) -> SemanticProfile | None:
        return self._inner.get_area_profile(area_id)

    def get_deployment_defaults(self):
        return self._inner.get_deployment_defaults()

    @property
    def get_entity_area(self):
        return self._inner.get_entity_area

    @property
    def get_entity_integration(self):
        return self._inner.get_entity_integration

    def _fingerprint(self) -> str:
        digest = hashlib.sha256("|".join(self.entity_keys()).encode())
        return digest.hexdigest()[:16]

    def query(self, **kwargs: Any) -> ProfileQueryResult:
        """Run mesa-core's profile query over this scoped view.

        mesa-core's MesaToolHandlers delegates mesa_query_profiles to
        store.query(); driving the real ProfileStore.query with this scoped
        store as self keeps total_matched, the cursor, and the fingerprint all
        scope-relative (no entity-enumeration oracle). The handler always passes
        a scoped resolver; _default_resolver mirrors it for the no-resolver path.
        """
        return ProfileStore.query(self, **kwargs)

    def _default_resolver(self) -> InheritanceResolver:
        return InheritanceResolver(
            store=self,
            get_entity_area=self.get_entity_area,
            get_entity_integration=self.get_entity_integration,
        )


# Control modes surfaced as an explicit "do not operate / observe only" list in
# get_overview. Other authored modes (e.g. a confirm override) appear only in the
# counts, since confirm is the common domain default and listing every confirm
# entity would reintroduce the baseline noise a rollup must avoid.
_RESTRICTIVE_MODES = ("prohibited", "read_only")


def authored_restrictions(
    runtime: "MesaRuntime",
    token: "TokenRecord",
    hass: Any,
    *,
    limit: int = MAX_PREVIEW_ENTITY_IDS,
) -> dict[str, Any]:
    """Scope-relative summary of ADMIN-AUTHORED entity profiles, for get_overview.

    Iterates only entities with a stored (operator-authored) profile via the
    scoped store, never baseline-derived modes, so the rollup reflects operator
    intent rather than domain defaults (the reason a naive control_mode count was
    originally omitted). ScopedProfileStore hides out-of-scope authored entities,
    so this is not an enumeration oracle.
    """
    scoped = ScopedProfileStore(runtime.store, token, hass)
    by_mode: dict[str, int] = {}
    restricted: list[dict[str, str]] = []
    truncated = 0
    for entity_id in sorted(scoped.entity_keys()):
        profile = scoped.get(entity_id)
        if profile is None:
            continue
        boundaries = profile.operational_boundaries
        cm = boundaries.control_mode
        mode = getattr(cm, "value", cm)
        if mode is None:
            continue
        by_mode[mode] = by_mode.get(mode, 0) + 1
        if mode in _RESTRICTIVE_MODES:
            if len(restricted) < limit:
                entry = {"entity_id": entity_id, "control_mode": mode}
                reason = getattr(boundaries, "control_reason", None)
                if reason:
                    entry["reason"] = reason
                restricted.append(entry)
            else:
                truncated += 1
    summary: dict[str, Any] = {
        "authored_profile_count": sum(by_mode.values()),
        "by_control_mode": dict(sorted(by_mode.items())),
        "restricted_entities": restricted,
    }
    if truncated:
        summary["restricted_truncated"] = truncated
    return summary


def _build_handlers(
    runtime: MesaRuntime, token: TokenRecord, hass: Any, session_id: str
) -> MesaToolHandlers:
    scoped = ScopedProfileStore(runtime.store, token, hass)
    resolver = InheritanceResolver(
        store=scoped,
        get_entity_area=runtime.store.get_entity_area,
        get_entity_integration=runtime.store.get_entity_integration,
    )
    ctx = build_caller_context(token, session_id)
    return MesaToolHandlers(store=scoped, resolver=resolver, caller_context_fn=lambda: ctx)


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _tool_success(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


async def call_mesa_tool(
    tool_name: str,
    args: dict,
    token: TokenRecord,
    hass: Any,
    data: ATMData,
    session_id: str,
) -> tuple[dict, str, str]:
    """Dispatch a mesa_* retrieval tool with token scoping.

    Returns the standard ATM tool tuple (result, outcome, resource).
    """
    if effective_cap(token, MESA_TOOLS_CAP) == CAP_DENY:
        return _tool_error("Forbidden."), "denied", tool_name

    runtime = data.mesa
    if runtime is None:
        return _tool_error("MESA is not available."), "denied", tool_name

    if tool_name in _ENTITY_TARGETED:
        entity_id = args.get("entity_id")
        # An out-of-scope or ghost entity must look exactly like a nonexistent
        # one. resolve() also enforces the atm-domain blocklist.
        if entity_id and not _is_visible(entity_id, token, hass):
            return (
                _tool_success(json.dumps(_not_found_envelope(entity_id))),
                "not_found",
                entity_id,
            )

    handlers = _build_handlers(runtime, token, hass, session_id)
    handler = getattr(handlers, tool_name)
    result = await handler(args)
    return _tool_success(json.dumps(result, default=str)), "allowed", tool_name
