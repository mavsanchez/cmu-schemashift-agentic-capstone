"""MCP v2 adapter over SchemaShift's deterministic data-engineering tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .context import ToolContext
from .registry import ManifestSourceRegistry
from .tools import sources as source_tools
from .tools.compare import compare_results as _compare_results
from .tools.schema import inspect_schema as _inspect_schema
from .tools.sql_executor import execute_readonly_sql as _execute_readonly_sql
from .tools.sql_parser import parse_sql as _parse_sql

TOOL_NAMES = (
    "inspect_schema",
    "parse_sql",
    "execute_readonly_sql",
    "compare_results",
    "list_uploaded_sources",
    "read_source",
)


def create_mcp_server(
    context: ToolContext,
    name: str = "SchemaShift Data Engineering Tools",
) -> Any:
    """Create an MCP v2 server with six intentionally narrow tools."""

    try:
        from mcp.server import MCPServer
    except ImportError as exc:  # pragma: no cover - packaging configuration
        raise RuntimeError("mcp>=2,<3 is required to create the MCP server") from exc

    server = MCPServer(
        name,
        instructions=(
            "Restricted local data-engineering tools for schema inspection, SQL "
            "parsing, read-only DuckDB validation, result comparison, and registered "
            "source reads. Tools accept opaque registry IDs and expose no shell, "
            "write-SQL, arbitrary path, or external-target capability."
        ),
    )

    @server.tool()
    def inspect_schema(source_id: str) -> dict[str, Any]:
        """Normalize a registered JSON/DDL/DuckDB schema source."""

        return _inspect_schema(source_id, context=context)

    @server.tool()
    def parse_sql(sql: str) -> dict[str, Any]:
        """Parse SQL and report dependencies plus read-only guardrail status."""

        return _parse_sql(sql)

    @server.tool()
    def execute_readonly_sql(database_id: str, sql: str) -> dict[str, Any]:
        """Execute one safe query against a registered local DuckDB database."""

        return _execute_readonly_sql(database_id, sql, context=context)

    @server.tool()
    def compare_results(
        old_database_id: str,
        old_sql: str,
        new_database_id: str,
        new_sql: str,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compare old/new registered query outputs under a bounded policy."""

        return _compare_results(
            old_database_id,
            old_sql,
            new_database_id,
            new_sql,
            policy,
            context=context,
        )

    @server.tool()
    def list_uploaded_sources(conversation_id: str, session_id: str) -> dict[str, Any]:
        """List registered source metadata scoped to a conversation/session."""

        return source_tools.list_uploaded_sources(conversation_id, session_id, context=context)

    @server.tool()
    def read_source(source_id: str, max_chars: int | None = None) -> dict[str, Any]:
        """Read bounded text or preview metadata for a registered source."""

        return source_tools.read_source(source_id, context=context, max_chars=max_chars)

    return server


def default_tool_context() -> ToolContext:
    """Build the standalone stdio context from local environment configuration."""

    repository_root = Path(__file__).resolve().parents[3]
    data_root = Path(os.environ.get("SCHEMASHIFT_DATA_ROOT", repository_root / "data")).resolve()
    manifest = Path(
        os.environ.get(
            "SCHEMASHIFT_SOURCE_MANIFEST",
            data_root / "generated" / "source_registry.json",
        )
    ).resolve()
    registry = ManifestSourceRegistry(manifest, (data_root,))
    return ToolContext(registry=registry)


def main() -> None:
    """Run the restricted server over stdio (``python -m schemashift.mcp.server``)."""

    server = create_mcp_server(default_tool_context())
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover - exercised by stdio smoke tests
    main()


__all__ = ["TOOL_NAMES", "create_mcp_server", "default_tool_context", "main"]
