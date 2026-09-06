"""Schema normalization and inspection for registered local sources."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from schemashift.mcp.context import ToolContext
from schemashift.mcp.registry import RegistryError, resolve_source

from .sql_executor import _duckdb_connect, _json_value

_JSON_TYPE_MAP = {
    "array": "ARRAY",
    "boolean": "BOOLEAN",
    "integer": "BIGINT",
    "null": "NULL",
    "number": "DOUBLE",
    "object": "STRUCT",
    "string": "VARCHAR",
}


def _type_name(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, str):
        return value.strip().upper() or "UNKNOWN"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        names = [_type_name(item) for item in value]
        return " | ".join(names)
    if isinstance(value, Mapping):
        if "type" in value:
            return _type_name(value["type"])
        return "STRUCT"
    return str(value).upper()


def _normalize_column(
    name: str,
    definition: Any,
    *,
    required: set[str] | None = None,
) -> dict[str, Any]:
    required = required or set()
    if isinstance(definition, str):
        data: Mapping[str, Any] = {"type": definition}
    elif isinstance(definition, Mapping):
        data = definition
    else:
        data = {"type": definition}

    raw_type = data.get("data_type", data.get("type", data.get("kind")))
    if isinstance(raw_type, str) and raw_type.lower() in _JSON_TYPE_MAP:
        column_type = _JSON_TYPE_MAP[raw_type.lower()]
    else:
        column_type = _type_name(raw_type)
    nullable = data.get("nullable")
    if nullable is None:
        nullable = name not in required and not bool(data.get("required", False))
    return {
        "name": str(name),
        "type": column_type,
        "nullable": bool(nullable),
        "default": _json_value(data.get("default")),
        "primary_key": bool(data.get("primary_key", data.get("primaryKey", False))),
    }


def _normalize_table(name: str, definition: Any) -> dict[str, Any]:
    if not isinstance(definition, Mapping):
        raise ValueError(f"Table {name!r} must be an object")
    raw_columns = definition.get("columns")
    if raw_columns is None and isinstance(definition.get("properties"), Mapping):
        raw_columns = definition["properties"]
    if raw_columns is None:
        # Permit a compact {table: {column: type}} form.
        reserved = {"name", "schema", "description", "primary_key", "required"}
        raw_columns = {key: value for key, value in definition.items() if key not in reserved}
    required = {str(item) for item in definition.get("required", [])}

    columns: list[dict[str, Any]] = []
    if isinstance(raw_columns, Mapping):
        columns = [
            _normalize_column(str(column_name), column, required=required)
            for column_name, column in raw_columns.items()
        ]
    elif isinstance(raw_columns, Sequence) and not isinstance(raw_columns, (str, bytes, bytearray)):
        for column in raw_columns:
            if not isinstance(column, Mapping) or not column.get("name"):
                raise ValueError(f"Table {name!r} contains an invalid column")
            columns.append(_normalize_column(str(column["name"]), column, required=required))
    else:
        raise ValueError(f"Table {name!r} columns must be an object or array")

    declared_primary_key = definition.get("primary_key") or definition.get("primaryKey")
    if isinstance(declared_primary_key, str):
        primary_names = {declared_primary_key}
    elif isinstance(declared_primary_key, Sequence):
        primary_names = {str(item) for item in declared_primary_key}
    else:
        primary_names = set()
    if primary_names:
        for column in columns:
            if column["name"] in primary_names:
                column["primary_key"] = True
                column["nullable"] = False

    return {
        "schema": str(definition.get("schema") or "main"),
        "name": str(name),
        "columns": columns,
    }


def normalize_json_schema(payload: Any) -> dict[str, Any]:
    """Normalize supported JSON schema shapes into the SchemaShift table model."""

    if not isinstance(payload, Mapping):
        raise ValueError("JSON schema root must be an object")

    raw_tables = payload.get("tables")
    if raw_tables is None and isinstance(payload.get("schemas"), Mapping):
        combined: list[dict[str, Any]] = []
        for schema_name, schema in payload["schemas"].items():
            if not isinstance(schema, Mapping):
                continue
            tables = schema.get("tables", {})
            iterator = tables.items() if isinstance(tables, Mapping) else []
            for table_name, table in iterator:
                if not isinstance(table, Mapping):
                    raise ValueError(f"Table {table_name!r} must be an object")
                table_data = dict(table)
                table_data.setdefault("schema", str(schema_name))
                combined.append({"name": table_name, **table_data})
        raw_tables = combined
    if raw_tables is None and isinstance(payload.get("properties"), Mapping):
        table_name = str(payload.get("name") or payload.get("title") or "record")
        raw_tables = [
            {
                "name": table_name,
                "columns": payload["properties"],
                "required": payload.get("required", []),
            }
        ]

    tables: list[dict[str, Any]] = []
    if isinstance(raw_tables, Mapping):
        tables = [
            _normalize_table(str(name), definition) for name, definition in raw_tables.items()
        ]
    elif isinstance(raw_tables, Sequence) and not isinstance(raw_tables, (str, bytes, bytearray)):
        for table in raw_tables:
            if not isinstance(table, Mapping) or not table.get("name"):
                raise ValueError("Every JSON table requires a name")
            tables.append(_normalize_table(str(table["name"]), table))
    else:
        raise ValueError("JSON schema requires a tables object or array")

    return {
        "schema_version": payload.get("schema_version") or payload.get("version"),
        "tables": tables,
    }


def _constraint_text(column: Any, dialect: str) -> str:
    constraints = column.args.get("constraints") or []
    return " ".join(
        constraint.sql(dialect=dialect, pretty=False) for constraint in constraints
    ).upper()


def normalize_sql_schema(sql: str, *, dialect: str = "duckdb") -> dict[str, Any]:
    """Normalize CREATE TABLE DDL without executing it."""

    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.errors import ParseError
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("sqlglot is required for schema inspection") from exc

    try:
        statements = [item for item in sqlglot.parse(sql, read=dialect) if item is not None]
    except (ParseError, ValueError) as exc:
        raise ValueError(f"Schema DDL could not be parsed: {exc}") from exc

    tables: list[dict[str, Any]] = []
    for statement in statements:
        if not isinstance(statement, exp.Create):
            continue
        if str(statement.args.get("kind") or "").upper() != "TABLE":
            continue
        schema_expression = statement.this
        if isinstance(schema_expression, exp.Schema):
            table_expression = schema_expression.this
            definitions = schema_expression.expressions
        else:
            table_expression = schema_expression
            definitions = statement.expressions
        table = (
            table_expression
            if isinstance(table_expression, exp.Table)
            else statement.find(exp.Table)
        )
        if table is None or not table.name:
            continue

        primary_key_names: set[str] = set()
        for primary_key in statement.find_all(exp.PrimaryKey):
            primary_key_names.update(
                str(expression.name)
                for expression in primary_key.expressions
                if getattr(expression, "name", None)
            )
        columns: list[dict[str, Any]] = []
        for definition in definitions:
            if not isinstance(definition, exp.ColumnDef):
                continue
            name = str(definition.this.name)
            kind = definition.args.get("kind")
            raw_constraint_text = " ".join(
                constraint.sql(dialect=dialect, pretty=False)
                for constraint in definition.args.get("constraints") or []
            )
            constraint_text = raw_constraint_text.upper()
            default_match = re.search(
                r"\bDEFAULT\s+(.+?)"
                r"(?:\s+(?:NOT\s+NULL|PRIMARY\s+KEY|UNIQUE|CHECK|REFERENCES)\b|$)",
                raw_constraint_text,
                re.IGNORECASE,
            )
            primary_key = "PRIMARY KEY" in constraint_text or name in primary_key_names
            columns.append(
                {
                    "name": name,
                    "type": (
                        kind.sql(dialect=dialect, pretty=False).upper()
                        if kind is not None
                        else "UNKNOWN"
                    ),
                    "nullable": "NOT NULL" not in constraint_text and not primary_key,
                    "default": default_match.group(1).strip() if default_match else None,
                    "primary_key": primary_key,
                }
            )
        tables.append(
            {
                "schema": str(table.db or "main"),
                "name": str(table.name),
                "columns": columns,
            }
        )
    if not tables:
        raise ValueError("No CREATE TABLE definitions were found")
    return {"schema_version": None, "tables": tables}


def _inspect_duckdb(path: Path) -> dict[str, Any]:
    connection = _duckdb_connect(path)
    try:
        rows = connection.execute(
            """
            SELECT table_schema, table_name, column_name, data_type,
                   is_nullable, column_default, ordinal_position
            FROM information_schema.columns
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_schema, table_name, ordinal_position
            """
        ).fetchall()
        try:
            pk_rows = connection.execute(
                """
                SELECT k.table_schema, k.table_name, k.column_name
                FROM information_schema.key_column_usage AS k
                JOIN information_schema.table_constraints AS t
                  ON t.constraint_catalog = k.constraint_catalog
                 AND t.constraint_schema = k.constraint_schema
                 AND t.constraint_name = k.constraint_name
                WHERE t.constraint_type = 'PRIMARY KEY'
                """
            ).fetchall()
        except Exception:
            pk_rows = []
    finally:
        connection.close()
    primary_keys = {(str(row[0]), str(row[1]), str(row[2])) for row in pk_rows}
    by_table: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for schema_name, table_name, column_name, data_type, nullable, default, _ in rows:
        key = (str(schema_name), str(table_name))
        by_table.setdefault(key, []).append(
            {
                "name": str(column_name),
                "type": str(data_type).upper(),
                "nullable": str(nullable).upper() == "YES",
                "default": _json_value(default),
                "primary_key": (*key, str(column_name)) in primary_keys,
            }
        )
    return {
        "schema_version": None,
        "tables": [
            {"schema": schema_name, "name": table_name, "columns": columns}
            for (schema_name, table_name), columns in by_table.items()
        ],
    }


def inspect_schema(source_id: str, *, context: ToolContext) -> dict[str, Any]:
    """Inspect a registered JSON schema, SQL DDL, or DuckDB database."""

    try:
        source = resolve_source(context.registry, str(source_id))
    except (RegistryError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "source_id": str(source_id),
            "error": {"code": "unknown_source", "message": str(exc)},
            "errors": [{"code": "unknown_source", "message": str(exc)}],
        }
    if not source.path.is_file():
        message = f"Registered source file does not exist: {source.source_id}"
        return {
            "ok": False,
            "source_id": source.source_id,
            "error": {"code": "source_unavailable", "message": message},
            "errors": [{"code": "source_unavailable", "message": message}],
        }

    suffix = source.path.suffix.lower()
    try:
        if suffix == ".json":
            normalized = normalize_json_schema(json.loads(source.path.read_text(encoding="utf-8")))
            source_format = "json"
        elif suffix == ".sql":
            normalized = normalize_sql_schema(source.path.read_text(encoding="utf-8"))
            source_format = "sql_ddl"
        elif suffix in {".duckdb", ".db"}:
            normalized = _inspect_duckdb(source.path)
            source_format = "duckdb"
        else:
            raise ValueError(
                "Schema inspection supports registered .json, .sql, .duckdb, and .db sources"
            )
    except Exception as exc:
        error = {"code": "schema_inspection_failed", "message": str(exc)}
        return {
            "ok": False,
            "source_id": source.source_id,
            "error": error,
            "errors": [error],
        }

    return {
        "ok": True,
        "source_id": source.source_id,
        "format": source_format,
        "schema_version": normalized.get("schema_version"),
        "tables": normalized["tables"],
        "table_count": len(normalized["tables"]),
        "errors": [],
    }


__all__ = ["inspect_schema", "normalize_json_schema", "normalize_sql_schema"]
