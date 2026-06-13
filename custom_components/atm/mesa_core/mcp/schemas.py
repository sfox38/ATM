"""Input schemas for the MESA MCP tools (Spec 9.2, 9.5).

``schemas/mesa_tools.schema.json`` is generated from TOOL_SCHEMAS and shipped
as the machine-readable artifact; a test asserts the two stay in sync.
"""

from __future__ import annotations

from typing import Any

TOOL_DESCRIPTIONS: dict[str, str] = {
    "mesa_query_profiles": (
        "Query MESA semantic profiles by domain, tag, area, intent, or origin, "
        "with pagination. Returns effective (inheritance-resolved) profiles."
    ),
    "mesa_get_profile": (
        "Retrieve the complete effective MESA profile for one entity, optionally "
        "including its diagnostic profile."
    ),
    "mesa_explain_profile": (
        "Return the full inheritance resolution path for an entity: which profile "
        "level contributed each effective field and why. The first tool to reach "
        "for when agent behaviour is unexpected."
    ),
    "mesa_get_caller_context": (
        "Retrieve caller identity and roles for the current session."
    ),
}

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "mesa_query_profiles": {
        "type": "object",
        "properties": {
            "domains": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
            "tags_match": {"enum": ["any", "all"], "default": "any"},
            "areas": {"type": "array", "items": {"type": "string"}},
            "intents": {"type": "array", "items": {"type": "string"}},
            "min_origin_authority": {
                "enum": ["inferred_ai", "hybrid", "user", "developer"]
            },
            "include_inferred": {"type": "boolean", "default": False},
            "include_fields": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
            "cursor": {"type": "string"},
        },
    },
    "mesa_get_profile": {
        "type": "object",
        "required": ["entity_id"],
        "properties": {
            "entity_id": {"type": "string"},
            "include_diagnostic": {"type": "boolean", "default": True},
        },
    },
    "mesa_explain_profile": {
        "type": "object",
        "required": ["entity_id"],
        "properties": {
            "entity_id": {"type": "string"},
            "show_conflicts": {"type": "boolean", "default": True},
        },
    },
    "mesa_get_caller_context": {"type": "object", "properties": {}},
}


def tools_schema_document() -> dict[str, Any]:
    """The document shipped as schemas/mesa_tools.schema.json."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://mesa-spec.org/schemas/mesa_tools.schema.json",
        "title": "MESA MCP tool input schemas",
        "description": (
            "Input schemas for the MESA retrieval API tools (MESA Specification "
            "Section 9). Lease tools (mesa_request_lease, mesa_release_lease) ship "
            "in mesa-core v1.1."
        ),
        "tools": {
            name: {"description": TOOL_DESCRIPTIONS[name], "inputSchema": schema}
            for name, schema in TOOL_SCHEMAS.items()
        },
    }
