"""Framework adapters for MESA MCP tool registration (Module Proposal 6.3).

Host servers using other frameworks implement the ToolRegistry protocol:
``register_tool(name, handler, schema, description)`` where ``handler`` is an
async callable taking one params dict and returning a result dict.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@runtime_checkable
class ToolRegistry(Protocol):
    def register_tool(
        self, name: str, handler: ToolHandler, schema: dict[str, Any], description: str
    ) -> None: ...


class DictToolRegistry:
    """Minimal in-memory registry: useful for tests and custom dispatch loops."""

    def __init__(self) -> None:
        self.tools: dict[str, tuple[ToolHandler, dict[str, Any], str]] = {}

    def register_tool(
        self, name: str, handler: ToolHandler, schema: dict[str, Any], description: str
    ) -> None:
        self.tools[name] = (handler, schema, description)

    async def call(self, name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        handler, _, _ = self.tools[name]
        return await handler(params or {})


__all__ = ["DictToolRegistry", "ToolHandler", "ToolRegistry"]
