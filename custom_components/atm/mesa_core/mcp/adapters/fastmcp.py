"""Adapter for FastMCP-style servers (both fastmcp 2.x and mcp.server.fastmcp).

Registration prefers the ``server.tool(...)`` decorator API, which both FastMCP
lineages expose, falling back to ``add_tool`` for older versions.
"""

from __future__ import annotations

from typing import Any

from custom_components.atm.mesa_core.exceptions import MesaError
from custom_components.atm.mesa_core.mcp.adapters import ToolHandler


class FastMCPRegistry:
    def __init__(self, server: Any) -> None:
        if server is None:
            raise MesaError("the fastmcp adapter requires server=<FastMCP instance>")
        self.server = server
        self.registered: list[str] = []

    def register_tool(
        self, name: str, handler: ToolHandler, schema: dict[str, Any], description: str
    ) -> None:
        async def tool_fn(params: dict[str, Any] | None = None) -> dict[str, Any]:
            return await handler(params or {})

        tool_fn.__name__ = name
        tool_fn.__doc__ = description

        tool_decorator = getattr(self.server, "tool", None)
        add_tool = getattr(self.server, "add_tool", None)
        if callable(tool_decorator):
            try:
                tool_decorator(name=name, description=description)(tool_fn)
            except TypeError:
                tool_decorator()(tool_fn)
        elif callable(add_tool):
            try:
                add_tool(tool_fn, name=name, description=description)
            except TypeError:
                add_tool(tool_fn)
        else:
            raise MesaError(
                "server does not look like a FastMCP instance (no .tool or .add_tool)"
            )
        self.registered.append(name)
