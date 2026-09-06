from __future__ import annotations

from schemashift.mcp.tools.sql_parser import parse_sql


def test_extracts_dependencies_and_complexity() -> None:
    result = parse_sql(
        """
        WITH recent AS (
          SELECT o.customer_id, o.total FROM old.orders AS o
        )
        SELECT c.name, r.total
        FROM recent AS r
        JOIN main.customers AS c ON c.id = r.customer_id
        ORDER BY c.name
        """
    )

    assert result["ok"]
    assert result["read_only"] is True
    assert result["is_read_only"] is True
    assert set(result["tables"]) == {"orders", "customers"}
    assert result["ctes"] == ["recent"]
    assert {"customer_id", "total", "name", "id"}.issubset(result["columns"])
    assert result["has_order_by"]
    assert result["complexity"]["join_count"] == 1
    assert result["complexity"]["cte_count"] == 1


def test_valid_but_unsafe_sql_keeps_parse_information() -> None:
    result = parse_sql("UPDATE customers SET name = 'new' WHERE id = 1")
    assert result["ok"]
    assert not result["read_only"]
    assert result["errors"]
    assert result["error"] == result["errors"][0]


def test_parse_error_is_structured() -> None:
    result = parse_sql("SELECT (")
    assert not result["ok"]
    assert result["error"]["code"] == "parse_error"
    assert result["tables"] == []


def test_multiple_statements_are_parsed_but_not_read_only() -> None:
    result = parse_sql("SELECT 1; SELECT 2")
    assert result["ok"]
    assert result["statement_count"] == 2
    assert not result["read_only"]
