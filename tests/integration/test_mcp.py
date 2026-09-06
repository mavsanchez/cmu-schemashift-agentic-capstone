from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pytest

from schemashift.mcp import (
    DatabaseRecord,
    InMemorySourceRegistry,
    SchemaShiftMCPClient,
    SynchronousMCPClient,
    ToolContext,
    create_mcp_server,
)


def _context(tmp_path: Path) -> ToolContext:
    database_path = tmp_path / "mcp.duckdb"
    connection = duckdb.connect(str(database_path))
    connection.execute("CREATE TABLE items(id INTEGER, name VARCHAR)")
    connection.execute("INSERT INTO items VALUES (1, 'one'), (2, 'two')")
    connection.close()
    registry = InMemorySourceRegistry((tmp_path,))
    registry.register_database(DatabaseRecord("fixture", database_path))
    return ToolContext(registry)


@pytest.mark.asyncio
async def test_mcp_v2_discovers_and_calls_tools_in_process(tmp_path: Path) -> None:
    context = _context(tmp_path)
    server = create_mcp_server(context)

    async with SchemaShiftMCPClient(server) as client:
        names = await client.discover_tools()
        parsed = await client.call_tool("parse_sql", {"sql": "SELECT id FROM items"})
        executed = await client.call_tool(
            "execute_readonly_sql",
            {"database_id": "fixture", "sql": "SELECT id FROM items ORDER BY id"},
        )

    assert set(names) == {
        "inspect_schema",
        "parse_sql",
        "execute_readonly_sql",
        "compare_results",
        "list_uploaded_sources",
        "read_source",
    }
    assert parsed["ok"] and parsed["read_only"]
    assert executed["ok"] and executed["rows"] == [[1], [2]]


@pytest.mark.asyncio
async def test_mcp_safety_failure_is_tool_observation_not_protocol_error(
    tmp_path: Path,
) -> None:
    async with SchemaShiftMCPClient(context=_context(tmp_path)) as client:
        result = await client.call_tool(
            "execute_readonly_sql",
            {"database_id": "fixture", "sql": "DROP TABLE items"},
        )
    assert not result["ok"]
    assert result["error"]["code"] == "unsafe_sql"


@pytest.mark.asyncio
async def test_mcp_v2_stdio_discovery_and_call_smoke(tmp_path: Path) -> None:
    from mcp import Client, StdioServerParameters
    from mcp.client.stdio import stdio_client

    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            (str(project_root / "src"), environment.get("PYTHONPATH", "")),
        )
    )
    environment["SCHEMASHIFT_DATA_ROOT"] = str(tmp_path)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "schemashift.mcp.server"],
        cwd=project_root,
        env=environment,
    )

    async with Client(stdio_client(parameters), raise_exceptions=True) as client:
        listed = await client.list_tools()
        result = await client.call_tool("parse_sql", {"sql": "SELECT 1 AS value"})

    assert {tool.name for tool in listed.tools} == {
        "inspect_schema",
        "parse_sql",
        "execute_readonly_sql",
        "compare_results",
        "list_uploaded_sources",
        "read_source",
    }
    assert result.structured_content["ok"]
    assert result.structured_content["read_only"]


def test_sync_graph_adapter_discovers_before_calling(tmp_path: Path) -> None:
    client = SynchronousMCPClient(context=_context(tmp_path))
    result = client.call_tool("parse_sql", {"sql": "SELECT 1"})
    assert result["ok"]
    assert set(client.discover_tools()) == {
        "inspect_schema",
        "parse_sql",
        "execute_readonly_sql",
        "compare_results",
        "list_uploaded_sources",
        "read_source",
    }
