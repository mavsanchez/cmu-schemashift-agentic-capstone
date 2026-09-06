"""SQL parsing and dependency extraction tool."""

from __future__ import annotations

from typing import Any

from schemashift.guardrails.sql_safety import validate_readonly_sql


def _append_unique(items: list[Any], seen: set[Any], value: Any) -> None:
    marker = tuple(sorted(value.items())) if isinstance(value, dict) else value
    if marker not in seen:
        seen.add(marker)
        items.append(value)


def parse_sql(sql: str, *, dialect: str = "duckdb") -> dict[str, Any]:
    """Parse SQL and return stable, JSON-serializable structural metadata.

    Syntax validity and read-only validity are intentionally separate: a valid
    ``UPDATE`` has ``ok=True`` but ``read_only=False``.  This distinction gives
    the migration graph precise feedback without ever executing the statement.
    """

    safety = validate_readonly_sql(sql, dialect=dialect)
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.errors import ParseError
    except ImportError as exc:  # pragma: no cover - dependency configuration
        raise RuntimeError("sqlglot is required for SQL parsing") from exc

    try:
        statements = [
            statement for statement in sqlglot.parse(sql, read=dialect) if statement is not None
        ]
    except (ParseError, ValueError) as exc:
        error = {"code": "parse_error", "message": str(exc), "severity": "error"}
        return {
            "ok": False,
            "dialect": dialect,
            "statement_count": 0,
            "statement_type": None,
            "read_only": False,
            "is_read_only": False,
            "normalized_sql": None,
            "tables": [],
            "columns": [],
            "table_references": [],
            "column_references": [],
            "ctes": [],
            "has_order_by": False,
            "complexity": _empty_complexity(),
            "diagnostics": [error],
            "errors": [error],
            "error": error,
        }

    cte_names: set[str] = set()
    for statement in statements:
        for cte in statement.find_all(exp.CTE):
            alias = str(cte.alias_or_name or "")
            if alias:
                cte_names.add(alias)

    tables: list[str] = []
    table_refs: list[dict[str, str | None]] = []
    seen_tables: set[Any] = set()
    seen_table_refs: set[Any] = set()
    for statement in statements:
        for table in statement.find_all(exp.Table):
            name = str(table.name or "")
            if not name or name in cte_names:
                continue
            _append_unique(tables, seen_tables, name)
            reference = {
                "catalog": str(table.catalog or "") or None,
                "schema": str(table.db or "") or None,
                "name": name,
                "alias": str(table.alias or "") or None,
            }
            _append_unique(table_refs, seen_table_refs, reference)

    columns: list[str] = []
    column_refs: list[dict[str, str | None]] = []
    seen_columns: set[Any] = set()
    seen_column_refs: set[Any] = set()
    for statement in statements:
        for column in statement.find_all(exp.Column):
            name = str(column.name or "")
            if not name:
                continue
            _append_unique(columns, seen_columns, name)
            reference = {
                "table": str(column.table or "") or None,
                "name": name,
            }
            _append_unique(column_refs, seen_column_refs, reference)
        for _star in statement.find_all(exp.Star):
            _append_unique(columns, seen_columns, "*")

    node_count = sum(1 for statement in statements for _ in statement.walk())
    complexity = {
        "node_count": node_count,
        "join_count": sum(1 for statement in statements for _ in statement.find_all(exp.Join)),
        "subquery_count": sum(
            1 for statement in statements for _ in statement.find_all(exp.Subquery)
        ),
        "cte_count": sum(1 for statement in statements for _ in statement.find_all(exp.CTE)),
        "set_operation_count": sum(
            1
            for statement in statements
            for node in statement.walk()
            if type(node).__name__.lower() in {"union", "intersect", "except"}
        ),
    }

    diagnostics = [{**issue, "severity": "error"} for issue in safety.to_dict()["issues"]]
    normalized = statements[0].sql(dialect=dialect, pretty=False) if len(statements) == 1 else None
    statement_type = type(statements[0]).__name__.upper() if len(statements) == 1 else None
    return {
        "ok": True,
        "dialect": dialect,
        "statement_count": len(statements),
        "statement_type": statement_type,
        "read_only": safety.allowed,
        "is_read_only": safety.allowed,
        "normalized_sql": normalized,
        "tables": tables,
        "columns": columns,
        "table_references": table_refs,
        "column_references": column_refs,
        "ctes": sorted(cte_names),
        "has_order_by": safety.has_order_by,
        "complexity": complexity,
        "diagnostics": diagnostics,
        "errors": diagnostics,
        "error": diagnostics[0] if diagnostics else None,
    }


def _empty_complexity() -> dict[str, int]:
    return {
        "node_count": 0,
        "join_count": 0,
        "subquery_count": 0,
        "cte_count": 0,
        "set_operation_count": 0,
    }


__all__ = ["parse_sql"]
