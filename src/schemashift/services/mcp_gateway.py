"""Synchronous, discovery-enforcing access to the async MCP v2 client."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from schemashift.mcp import TOOL_NAMES, SchemaShiftMCPClient, ToolContext

ResultT = TypeVar("ResultT")


def _run_async(operation: Coroutine[Any, Any, ResultT]) -> ResultT:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(operation)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="schemashift-mcp") as pool:
        return pool.submit(asyncio.run, operation).result()


class SynchronousMCPGateway:
    """Discover and call the six restricted tools through an in-process MCP server."""

    def __init__(self, context: ToolContext) -> None:
        self.context = context
        self._discovered: frozenset[str] | None = None

    async def _discover(self) -> frozenset[str]:
        async with SchemaShiftMCPClient(context=self.context) as client:
            return frozenset(await client.discover_tools())

    def discover_tools(self, *, refresh: bool = False) -> tuple[str, ...]:
        if self._discovered is None or refresh:
            self._discovered = _run_async(self._discover())
        missing = set(TOOL_NAMES) - set(self._discovered)
        if missing:
            raise RuntimeError(
                "SchemaShift MCP server did not expose required tools: "
                + ", ".join(sorted(missing))
            )
        return tuple(sorted(self._discovered))

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with SchemaShiftMCPClient(context=self.context) as client:
            discovered = set(await client.discover_tools())
            if name not in discovered:
                raise ValueError(f"MCP tool was not discovered at runtime: {name}")
            return await client.call_tool(name, arguments)

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if name not in self.discover_tools():
            raise ValueError(f"Unknown SchemaShift MCP tool: {name}")
        return _run_async(self._call(name, dict(arguments or {})))


__all__ = ["SynchronousMCPGateway"]
