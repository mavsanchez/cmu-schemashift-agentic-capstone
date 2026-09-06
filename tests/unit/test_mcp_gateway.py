from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from schemashift.mcp import TOOL_NAMES, InMemorySourceRegistry, ToolContext
from schemashift.services import mcp_gateway
from schemashift.services.mcp_gateway import SynchronousMCPGateway


class FakeMCPClient:
    names = list(TOOL_NAMES)
    discoveries = 0
    calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def __aenter__(self) -> FakeMCPClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args

    async def discover_tools(self) -> list[str]:
        type(self).discoveries += 1
        return list(type(self).names)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        type(self).calls.append((name, arguments))
        return {"ok": True, "name": name, "arguments": arguments}


@pytest.fixture(autouse=True)
def reset_fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeMCPClient.names = list(TOOL_NAMES)
    FakeMCPClient.discoveries = 0
    FakeMCPClient.calls = []
    monkeypatch.setattr(mcp_gateway, "SchemaShiftMCPClient", FakeMCPClient)


def _gateway(tmp_path: Path) -> SynchronousMCPGateway:
    context = ToolContext(InMemorySourceRegistry((tmp_path,)))
    return SynchronousMCPGateway(context)


def test_gateway_discovers_required_runtime_surface_and_rejects_invented_tools(
    tmp_path: Path,
) -> None:
    gateway = _gateway(tmp_path)

    assert set(gateway.discover_tools()) == set(TOOL_NAMES)
    assert set(gateway.discover_tools()) == set(TOOL_NAMES)
    assert FakeMCPClient.discoveries == 1  # discovery result is cached

    with pytest.raises(ValueError, match="Unknown SchemaShift MCP tool"):
        gateway.call_tool("read_arbitrary_path", {"path": "outside.sql"})
    assert FakeMCPClient.calls == []


def test_gateway_calls_only_a_discovered_tool(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    result = gateway.call_tool("parse_sql", {"sql": "SELECT 1"})

    assert result == {
        "ok": True,
        "name": "parse_sql",
        "arguments": {"sql": "SELECT 1"},
    }
    assert FakeMCPClient.calls == [("parse_sql", {"sql": "SELECT 1"})]


def test_gateway_refuses_an_incomplete_discovered_surface(tmp_path: Path) -> None:
    FakeMCPClient.names = [name for name in TOOL_NAMES if name != "compare_results"]

    with pytest.raises(RuntimeError, match="compare_results"):
        _gateway(tmp_path).discover_tools()


@pytest.mark.asyncio
async def test_sync_gateway_is_safe_when_called_from_an_event_loop(tmp_path: Path) -> None:
    result = _gateway(tmp_path).call_tool("parse_sql", {"sql": "SELECT 1"})

    assert result["ok"] is True
