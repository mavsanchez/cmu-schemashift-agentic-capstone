"""Small high-level MCP v2 client used by graph/service code."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .context import ToolContext
from .server import TOOL_NAMES, create_mcp_server


class SchemaShiftMCPClient:
    """Async client supporting in-process servers, URLs, and MCP transports.

    Passing a :class:`ToolContext` without a target creates an in-process MCP
    server, which is useful for the local application and deterministic tests.
    """

    def __init__(
        self,
        target: Any | None = None,
        *,
        context: ToolContext | None = None,
        raise_exceptions: bool = True,
    ) -> None:
        if target is None:
            if context is None:
                raise ValueError("target or context is required")
            target = create_mcp_server(context)
        self.target = target
        self.raise_exceptions = raise_exceptions
        self._manager: Any | None = None
        self._client: Any | None = None

    async def __aenter__(self) -> SchemaShiftMCPClient:
        try:
            from mcp import Client
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("mcp>=2,<3 is required to use the MCP client") from exc
        self._manager = Client(self.target, raise_exceptions=self.raise_exceptions)
        self._client = await self._manager.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._manager is not None:
            await self._manager.__aexit__(exc_type, exc, traceback)
        self._manager = None
        self._client = None

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("Use SchemaShiftMCPClient inside 'async with'")
        return self._client

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._require_client().list_tools()
        tools = getattr(result, "tools", result)
        normalized: list[dict[str, Any]] = []
        for tool in tools:
            dump = getattr(tool, "model_dump", None)
            if callable(dump):
                normalized.append(dump(by_alias=True, exclude_none=True))
            elif isinstance(tool, dict):
                normalized.append(tool)
            else:
                normalized.append(
                    {
                        "name": str(getattr(tool, "name", "")),
                        "description": getattr(tool, "description", None),
                    }
                )
        return normalized

    async def discover_tools(self) -> list[str]:
        return [tool["name"] for tool in await self.list_tools()]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in TOOL_NAMES:
            raise ValueError(f"Unknown SchemaShift MCP tool: {name}")
        result = await self._require_client().call_tool(name, arguments or {})
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            # Some return annotations are represented as {"result": value}.
            if set(structured) == {"result"} and isinstance(structured["result"], dict):
                return structured["result"]
            return structured

        for block in getattr(result, "content", []):
            text = getattr(block, "text", None)
            if not isinstance(text, str):
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise RuntimeError(f"Tool {name} did not return a structured object")


class SynchronousMCPClient:
    """Synchronous adapter for LangGraph nodes that still uses MCP transport.

    Each call uses an in-process MCP v2 connection.  Tool discovery is cached and
    enforced before the first invocation so a graph cannot invent a tool name.
    """

    def __init__(
        self,
        target: Any | None = None,
        *,
        context: ToolContext | None = None,
        raise_exceptions: bool = True,
    ) -> None:
        if target is None:
            if context is None:
                raise ValueError("target or context is required")
            target = create_mcp_server(context)
        self.target = target
        self.raise_exceptions = raise_exceptions
        self._discovered: set[str] | None = None

    def _run(self, awaitable: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise RuntimeError(
            "SynchronousMCPClient cannot run on an active event-loop thread; "
            "use SchemaShiftMCPClient there"
        )

    async def _list(self) -> list[dict[str, Any]]:
        async with SchemaShiftMCPClient(
            self.target, raise_exceptions=self.raise_exceptions
        ) as client:
            return await client.list_tools()

    def list_tools(self) -> list[dict[str, Any]]:
        tools = self._run(self._list())
        self._discovered = {str(tool["name"]) for tool in tools}
        return tools

    def discover_tools(self) -> list[str]:
        return [str(tool["name"]) for tool in self.list_tools()]

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with SchemaShiftMCPClient(
            self.target, raise_exceptions=self.raise_exceptions
        ) as client:
            return await client.call_tool(name, arguments)

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._discovered is None:
            self.list_tools()
        if name not in (self._discovered or set()):
            raise ValueError(f"Tool was not discovered from the MCP server: {name}")
        return self._run(self._call(name, arguments or {}))


# The unqualified name matches the synchronous parent-graph tool-caller contract.
MCPClient = SynchronousMCPClient


__all__ = ["MCPClient", "SchemaShiftMCPClient", "SynchronousMCPClient"]
