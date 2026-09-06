from __future__ import annotations

import time
from pathlib import Path

import duckdb

from schemashift.mcp import DatabaseRecord, InMemorySourceRegistry, ToolContext
from schemashift.mcp.tools.sql_executor import execute_readonly_sql


def _database(path: Path) -> None:
    connection = duckdb.connect(str(path))
    connection.execute("CREATE TABLE items(id INTEGER, name VARCHAR)")
    connection.execute("INSERT INTO items VALUES (1, 'one'), (2, 'two'), (3, 'three')")
    connection.close()


def _context(tmp_path: Path, *, max_rows: int = 10) -> ToolContext:
    path = tmp_path / "fixture.duckdb"
    _database(path)
    registry = InMemorySourceRegistry((tmp_path,))
    registry.register_database(DatabaseRecord(database_id="fixture", path=path))
    return ToolContext(registry=registry, max_result_rows=max_rows)


def test_executes_registered_database_read_only(tmp_path: Path) -> None:
    result = execute_readonly_sql(
        "fixture", "SELECT id, name FROM items ORDER BY id", context=_context(tmp_path)
    )
    assert result["ok"]
    assert result["row_count"] == 3
    assert result["rows"][0] == [1, "one"]
    assert not result["truncated"]


def test_rejects_write_before_connecting(tmp_path: Path) -> None:
    context = _context(tmp_path)
    result = execute_readonly_sql("fixture", "DELETE FROM items", context=context)
    assert not result["ok"]
    assert result["error"]["code"] == "unsafe_sql"


def test_requires_database_registry_id(tmp_path: Path) -> None:
    context = _context(tmp_path)
    result = execute_readonly_sql(str(tmp_path / "fixture.duckdb"), "SELECT 1", context=context)
    assert not result["ok"]
    assert result["error"]["code"] == "unknown_database"


def test_caps_results_and_marks_truncation(tmp_path: Path) -> None:
    result = execute_readonly_sql(
        "fixture", "SELECT id FROM items", context=_context(tmp_path, max_rows=2)
    )
    assert result["ok"]
    assert result["row_count"] == 2
    assert result["truncated"]


def test_timeout_is_a_structured_observation(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    context = ToolContext(
        registry=context.registry,
        query_timeout_seconds=0.01,
        max_result_rows=context.max_result_rows,
    )

    import schemashift.mcp.tools.sql_executor as executor_module

    original = executor_module._run_query

    def slow_query(*args, **kwargs):
        time.sleep(0.05)
        return original(*args, **kwargs)

    monkeypatch.setattr(executor_module, "_run_query", slow_query)
    result = execute_readonly_sql("fixture", "SELECT 1", context=context)
    assert not result["ok"]
    assert result["error"]["code"] == "query_timeout"
