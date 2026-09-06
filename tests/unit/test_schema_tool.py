from __future__ import annotations

import json
from pathlib import Path

import duckdb

from schemashift.mcp import InMemorySourceRegistry, SourceRecord, ToolContext
from schemashift.mcp.tools.schema import (
    inspect_schema,
    normalize_json_schema,
    normalize_sql_schema,
)


def test_normalizes_mapping_json_schema() -> None:
    normalized = normalize_json_schema(
        {
            "schema_version": "v2",
            "tables": {
                "customers": {
                    "columns": {
                        "id": {"type": "integer", "nullable": False},
                        "name": "varchar",
                    },
                    "primary_key": ["id"],
                }
            },
        }
    )
    table = normalized["tables"][0]
    assert normalized["schema_version"] == "v2"
    assert table["name"] == "customers"
    assert table["columns"][0] == {
        "name": "id",
        "type": "BIGINT",
        "nullable": False,
        "default": None,
        "primary_key": True,
    }


def test_normalizes_sql_ddl_without_executing() -> None:
    normalized = normalize_sql_schema(
        """
        CREATE TABLE sales.customers (
          id INTEGER PRIMARY KEY,
          name VARCHAR NOT NULL,
          score DOUBLE DEFAULT 0.0
        );
        """
    )
    table = normalized["tables"][0]
    assert table["schema"] == "sales"
    assert table["name"] == "customers"
    assert [column["name"] for column in table["columns"]] == ["id", "name", "score"]
    assert table["columns"][0]["primary_key"]
    assert not table["columns"][1]["nullable"]


def test_inspects_registered_json_file(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps({"tables": {"items": {"columns": {"id": "INTEGER"}}}}))
    registry = InMemorySourceRegistry((tmp_path,))
    registry.register_source(SourceRecord("schema", "c", "s", path, role="old_schema"))
    result = inspect_schema("schema", context=ToolContext(registry))
    assert result["ok"]
    assert result["format"] == "json"
    assert result["tables"][0]["name"] == "items"


def test_inspects_registered_duckdb_metadata(tmp_path: Path) -> None:
    path = tmp_path / "data.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value DECIMAL(8,2))")
    connection.close()
    registry = InMemorySourceRegistry((tmp_path,))
    registry.register_source(SourceRecord("db-source", "c", "s", path, role="old_data"))
    result = inspect_schema("db-source", context=ToolContext(registry))
    assert result["ok"]
    assert result["format"] == "duckdb"
    assert result["tables"][0]["name"] == "items"


def test_unknown_source_is_structured(tmp_path: Path) -> None:
    registry = InMemorySourceRegistry((tmp_path,))
    result = inspect_schema("missing", context=ToolContext(registry))
    assert not result["ok"]
    assert result["error"]["code"] == "unknown_source"
