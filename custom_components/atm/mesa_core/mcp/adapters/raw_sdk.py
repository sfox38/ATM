"""Adapter for the raw MCP Python SDK low-level Server.

Installs a single ``list_tools``/``call_tool`` handler pair on the server and
dispatches by tool name; results are returned as JSON text content.
"""

from __future__ import annotations

import json
from typing import Any

from custom_components.atm.mesa_core.exceptions import MesaError
from custom_components.atm.mesa_core.mcp.adapters import ToolHandler


class RawSDKRegistry:
    def __init__(self, server: Any) -> None:
        if server is None:
            raise MesaError("the raw_sdk adapter requires server=<mcp.server.Server instance>")
        self.server = server
        self._tools: dict[str, tuple[ToolHandler, dict[str, Any], str]] = {}
        self._installed = False

    @property
    def registered(self) -> list[str]:
        return list(self._tools)

    def register_tool(
        self, name: str, handler: ToolHandler, schema: dict[str, Any], description: str
    ) -> None:
        self._tools[name] = (handler, schema, description)
        self._install_once()

    async def dispatch(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        handler, _, _ = self._tools[name]
        return await handler(arguments or {})

    def _install_once(self) -> None:
        if self._installed:
            return
        try:
            from mcp import types
        except ImportError as err:
            raise MesaError(
                "the raw_sdk adapter requires the 'mcp' package (pip install mesa-core[mcp])"
            ) from err

        tools = self._tools  # closures observe later registrations

        @self.server.list_tools()  # type: ignore[untyped-decorator]
        async def _list_tools() -> list[Any]:
            return [
                types.Tool(name=name, description=description, inputSchema=schema)
                for name, (_, schema, description) in tools.items()
            ]

        @self.server.call_tool()  # type: ignore[untyped-decorator]
        async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
            handler, _, _ = tools[name]
            result = await handler(arguments or {})
            return [types.TextContent(type="text", text=json.dumps(result))]

        self._installed = True
