"""Restricted MCP data-engineering boundary for SchemaShift."""

from .client import MCPClient, SchemaShiftMCPClient, SynchronousMCPClient
from .context import ToolContext
from .registry import (
    DatabaseRecord,
    InMemorySourceRegistry,
    ManifestSourceRegistry,
    PathOutsideRegistryError,
    SourceRecord,
    SourceRegistry,
    UnknownDatabaseError,
    UnknownSourceError,
)
from .server import TOOL_NAMES, create_mcp_server
from .tools import (
    ComparisonPolicy,
    compare_results,
    execute_readonly_sql,
    inspect_schema,
    list_uploaded_sources,
    parse_sql,
    read_source,
)

__all__ = [
    "ComparisonPolicy",
    "DatabaseRecord",
    "InMemorySourceRegistry",
    "MCPClient",
    "ManifestSourceRegistry",
    "PathOutsideRegistryError",
    "SchemaShiftMCPClient",
    "SourceRecord",
    "SourceRegistry",
    "SynchronousMCPClient",
    "TOOL_NAMES",
    "ToolContext",
    "UnknownDatabaseError",
    "UnknownSourceError",
    "compare_results",
    "create_mcp_server",
    "execute_readonly_sql",
    "inspect_schema",
    "list_uploaded_sources",
    "parse_sql",
    "read_source",
]
