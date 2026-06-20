"""MESA MCP tool handlers and registration (Spec 9; Module Proposal 5).

``register_mesa_tools`` is the host server's single integration point: it
registers mesa_query_profiles, mesa_get_profile, mesa_explain_profile, and
mesa_get_caller_context into the host's tool registry via an adapter.

Errors are returned as the Spec 9.6 envelope:
``{"error": code, "message": str, "details": {}}``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from custom_components.atm.mesa_core.exceptions import InvalidCursorError, MesaError, MesaValidationError
from custom_components.atm.mesa_core.inheritance import InheritanceResolver
from custom_components.atm.mesa_core.mcp.adapters import ToolRegistry
from custom_components.atm.mesa_core.mcp.schemas import TOOL_DESCRIPTIONS, TOOL_SCHEMAS
from custom_components.atm.mesa_core.privacy import CallerContext
from custom_components.atm.mesa_core.profile import SemanticProfile
from custom_components.atm.mesa_core.store import ProfileStore

logger = logging.getLogger("mesa_core.mcp")

MESA_VERSION = "1.0"

_ANONYMOUS = CallerContext(
    caller_id="anonymous", roles=[], is_authenticated=False, session_id=""
)


def _error(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": code, "message": message, "details": details or {}}


class MesaToolHandlers:
    """The four core retrieval API tools (Spec 9.5), framework-agnostic."""

    def __init__(
        self,
        store: ProfileStore,
        resolver: InheritanceResolver | None = None,
        caller_context_fn: Callable[[], CallerContext] | None = None,
    ) -> None:
        self.store = store
        self.resolver = resolver or InheritanceResolver(store=store)
        self.caller_context_fn = caller_context_fn

    # -- helpers ---------------------------------------------------------------

    def _caller_context(self) -> CallerContext | None:
        if self.caller_context_fn is None:
            return None
        return self.caller_context_fn()

    def _result_object(
        self,
        entity_id: str,
        stored: SemanticProfile,
        effective: SemanticProfile,
        include_fields: list[str] | None,
    ) -> dict[str, Any]:
        doc = effective.to_dict()
        sp = doc.get("semantic_profile", {})
        if include_fields:
            # metadata_origin and schema_version are always included (Spec 9.2);
            # requested fields absent from the profile are silently omitted.
            keep = set(include_fields) | {"metadata_origin", "schema_version"}
            sp = {k: v for k, v in sp.items() if k in keep}
        out: dict[str, Any] = {
            "entity_id": entity_id,
            "component_type": "entity",
            "semantic_profile": sp,
            "privacy_classification": doc.get("privacy_classification"),
        }
        if stored.is_inferred():
            out["staleness_status"] = stored.staleness_status()
        if (
            include_fields
            and "diagnostic_profile" in include_fields
            and effective.diagnostic_profile is not None
        ):
            out["diagnostic_profile"] = effective.diagnostic_profile
        return out

    # -- tools ----------------------------------------------------------------

    async def mesa_query_profiles(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self.store.query(
                domains=params.get("domains"),
                tags=params.get("tags"),
                tags_match=params.get("tags_match", "any"),
                areas=params.get("areas"),
                intents=params.get("intents"),
                include_inferred=bool(params.get("include_inferred", False)),
                min_origin_authority=params.get("min_origin_authority"),
                limit=int(params.get("limit", 50)),
                cursor=params.get("cursor"),
                resolver=self.resolver,
            )
            include_fields = params.get("include_fields")
            response: dict[str, Any] = {
                "mesa_version": MESA_VERSION,
                "results": [
                    self._result_object(
                        row.entity_id, row.stored, row.effective or row.stored, include_fields
                    )
                    for row in result.rows
                ],
                "total_matched": result.total_matched,
                "pagination": {
                    "limit": result.limit,
                    "returned": len(result.rows),
                    "has_more": result.has_more,
                    "next_cursor": result.next_cursor,
                },
            }
            caller = self._caller_context()
            if caller is not None:
                response["caller_context"] = caller.to_dict()
            if result.warnings:
                response["warnings"] = result.warnings
            return response
        except InvalidCursorError as err:
            return _error("invalid_cursor", str(err))
        except (ValueError, MesaValidationError) as err:
            return _error("invalid_query", str(err))
        except Exception:
            logger.exception("mesa_query_profiles failed")
            return _error("server_error", "internal error in mesa_query_profiles")

    async def mesa_get_profile(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            entity_id = params.get("entity_id")
            if not entity_id:
                return _error("invalid_query", "entity_id is required")
            include_diagnostic = bool(params.get("include_diagnostic", True))
            if not self.resolver.has_profile(entity_id):
                return _error(
                    "not_found", f"entity {entity_id!r} has no MESA profile at any level"
                )
            stored = self.store.get(entity_id)
            effective = self.resolver.resolve(entity_id)
            doc = effective.to_dict()
            out: dict[str, Any] = {
                "mesa_version": MESA_VERSION,
                "entity_id": entity_id,
                "component_type": "entity",
                "semantic_profile": doc.get("semantic_profile", {}),
                "privacy_classification": doc.get("privacy_classification"),
            }
            if include_diagnostic and effective.diagnostic_profile is not None:
                out["diagnostic_profile"] = effective.diagnostic_profile
            if stored is not None and stored.is_inferred():
                out["staleness_status"] = stored.staleness_status()
            return out
        except MesaValidationError as err:
            return _error("invalid_query", str(err))
        except Exception:
            logger.exception("mesa_get_profile failed")
            return _error("server_error", "internal error in mesa_get_profile")

    async def mesa_explain_profile(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            entity_id = params.get("entity_id")
            if not entity_id:
                return _error("invalid_query", "entity_id is required")
            show_conflicts = bool(params.get("show_conflicts", True))
            explanation = self.resolver.explain(entity_id)
            out = explanation.to_dict(show_conflicts=show_conflicts)
            out["mesa_version"] = MESA_VERSION
            return out
        except MesaValidationError as err:
            return _error("invalid_query", str(err))
        except Exception:
            logger.exception("mesa_explain_profile failed")
            return _error("server_error", "internal error in mesa_explain_profile")

    async def mesa_get_caller_context(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            caller = self._caller_context() or _ANONYMOUS
            return {"mesa_version": MESA_VERSION, **caller.to_dict()}
        except Exception:
            logger.exception("mesa_get_caller_context failed")
            return _error("server_error", "internal error in mesa_get_caller_context")


def register_mesa_tools(
    store: ProfileStore,
    adapter: str | ToolRegistry = "fastmcp",
    server: Any = None,
    *,
    resolver: InheritanceResolver | None = None,
    enforcer: Any = None,
    lease_manager: Any = None,
    caller_context_fn: Callable[[], CallerContext] | None = None,
) -> ToolRegistry:
    """Register all MESA MCP tools into the host server's tool registry.

    ``adapter`` is "fastmcp", "raw_sdk", or any object implementing the
    ToolRegistry protocol. Returns the registry used.

    ``lease_manager`` is accepted for forward compatibility: lease tools ship
    in mesa-core v1.1 and the parameter is ignored in v1.0. ``enforcer`` is
    likewise accepted for API stability; enforcement is wired into the host's
    service-call path directly (see the Module Proposal, Section 6.2), not
    exposed as a tool.
    """
    registry: ToolRegistry
    if isinstance(adapter, str):
        if adapter == "fastmcp":
            from custom_components.atm.mesa_core.mcp.adapters.fastmcp import FastMCPRegistry

            registry = FastMCPRegistry(server)
        elif adapter == "raw_sdk":
            from custom_components.atm.mesa_core.mcp.adapters.raw_sdk import RawSDKRegistry

            registry = RawSDKRegistry(server)
        else:
            raise MesaError(
                f"unknown adapter {adapter!r}; use 'fastmcp', 'raw_sdk', or a ToolRegistry"
            )
    else:
        registry = adapter

    if lease_manager is not None:
        logger.warning(
            "lease_manager was provided but lease tools ship in mesa-core v1.1; ignored"
        )

    handlers = MesaToolHandlers(
        store=store, resolver=resolver, caller_context_fn=caller_context_fn
    )
    for name, handler in (
        ("mesa_query_profiles", handlers.mesa_query_profiles),
        ("mesa_get_profile", handlers.mesa_get_profile),
        ("mesa_explain_profile", handlers.mesa_explain_profile),
        ("mesa_get_caller_context", handlers.mesa_get_caller_context),
    ):
        registry.register_tool(name, handler, TOOL_SCHEMAS[name], TOOL_DESCRIPTIONS[name])
    return registry
