"""Deterministic SQL safety checks for SchemaShift validation queries.

This module deliberately does not try to make arbitrary SQL safe.  It accepts a
small query-only language and rejects everything else before DuckDB sees it.
The database executor adds a second boundary by opening only registry-resolved
databases in read-only mode with external access disabled.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_DENIED_NODE_NAMES = frozenset(
    {
        "alter",
        "analyze",
        "attach",
        "cache",
        "command",
        "commit",
        "copy",
        "create",
        "delete",
        "detach",
        "drop",
        "execute",
        "grant",
        "insert",
        "install",
        "into",
        "load",
        "lock",
        "merge",
        "pragma",
        "replace",
        "revoke",
        "rollback",
        "set",
        "transaction",
        "truncate",
        "truncatetable",
        "uncache",
        "update",
        "use",
        "vacuum",
    }
)

# DuckDB can read local files, remote URLs, and attached databases through table
# functions.  None are needed to validate data already staged in a registered DB.
_DENIED_TABLE_FUNCTIONS = frozenset(
    {
        "azure_scan",
        "csv_scan",
        "delta_scan",
        "excel_scan",
        "glob",
        "httpfs_scan",
        "iceberg_scan",
        "json_scan",
        "mysql_scan",
        "parquet_scan",
        "postgres_scan",
        "query",
        "query_table",
        "read_blob",
        "read_csv",
        "read_csv_auto",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_parquet",
        "read_text",
        "sqlite_scan",
        "sniff_csv",
        "duckdb_secrets",
        "which_secret",
    }
)

_FILE_TABLE_RE = re.compile(
    r"(?:[/\\]|^[A-Za-z]:|^\.{1,2}[/\\]|^[A-Za-z][A-Za-z0-9+.-]*://|"
    r"\.(?:csv|json|jsonl|ndjson|parquet|duckdb|db)(?:$|[?#]))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SQLSafetyIssue:
    """One machine-readable reason a query was rejected."""

    code: str
    message: str
    node: str | None = None


@dataclass(frozen=True, slots=True)
class SQLSafetyResult:
    """Result returned by :func:`validate_readonly_sql`."""

    allowed: bool
    statement_count: int
    statement_type: str | None
    normalized_sql: str | None
    has_order_by: bool
    issues: tuple[SQLSafetyIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "statement_count": self.statement_count,
            "statement_type": self.statement_type,
            "normalized_sql": self.normalized_sql,
            "has_order_by": self.has_order_by,
            "issues": [asdict(issue) for issue in self.issues],
        }


class SQLSafetyViolation(ValueError):
    """Raised by :func:`require_readonly_sql` for a rejected statement."""

    def __init__(self, result: SQLSafetyResult) -> None:
        self.result = result
        detail = "; ".join(issue.message for issue in result.issues)
        super().__init__(detail or "SQL did not pass the read-only policy")


def _node_name(node: object) -> str:
    return type(node).__name__.lower()


def _function_name(node: object) -> str:
    """Return a sqlglot function name without depending on a concrete version."""

    sql_name = getattr(node, "sql_name", None)
    if callable(sql_name):
        try:
            name = sql_name()
            if name and name.upper() != "ANONYMOUS":
                return str(name).lower()
        except (AttributeError, TypeError):
            pass
    name = getattr(node, "name", None)
    return str(name or "").lower()


def _is_denied_function(name: str) -> bool:
    return name in _DENIED_TABLE_FUNCTIONS or name.startswith("read_") or name.endswith("_scan")


def validate_readonly_sql(sql: str, *, dialect: str = "duckdb") -> SQLSafetyResult:
    """Validate that *sql* is exactly one self-contained read-only query.

    The check is AST based.  It permits SELECT/CTE/set-operation query
    expressions, but denies side-effecting nodes wherever they appear and denies
    DuckDB file/network table functions.  Parse errors are represented in the
    result so callers can feed them back to the migration graph as observations.
    """

    if not isinstance(sql, str) or not sql.strip():
        issue = SQLSafetyIssue("empty_sql", "SQL must be a non-empty string")
        return SQLSafetyResult(False, 0, None, None, False, (issue,))

    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.errors import ParseError
    except ImportError as exc:  # pragma: no cover - packaging/configuration error
        raise RuntimeError("sqlglot is required for SQL validation") from exc

    try:
        statements = sqlglot.parse(sql, read=dialect)
    except (ParseError, ValueError) as exc:
        issue = SQLSafetyIssue("parse_error", f"SQL could not be parsed: {exc}")
        return SQLSafetyResult(False, 0, None, None, False, (issue,))

    statements = [statement for statement in statements if statement is not None]
    if len(statements) != 1:
        issue = SQLSafetyIssue(
            "statement_count",
            f"Exactly one SQL statement is required; received {len(statements)}",
        )
        statement_type = _node_name(statements[0]).upper() if statements else None
        return SQLSafetyResult(False, len(statements), statement_type, None, False, (issue,))

    statement = statements[0]
    statement_type = _node_name(statement).upper()
    issues: list[SQLSafetyIssue] = []

    query_class = getattr(exp, "Query", ())
    if not query_class or not isinstance(statement, query_class):
        issues.append(
            SQLSafetyIssue(
                "unsupported_root",
                "Only SELECT, CTE, UNION, INTERSECT, and EXCEPT queries are allowed",
                statement_type,
            )
        )

    seen_denied: set[str] = set()
    for node in statement.walk():
        name = _node_name(node)
        if name in _DENIED_NODE_NAMES and name not in seen_denied:
            seen_denied.add(name)
            issues.append(
                SQLSafetyIssue(
                    "denied_operation",
                    f"Operation {name.upper()} is not allowed in validation SQL",
                    name,
                )
            )

    func_class = getattr(exp, "Func", ())
    if func_class:
        seen_functions: set[str] = set()
        for function in statement.find_all(func_class):
            name = _function_name(function)
            if _is_denied_function(name) and name not in seen_functions:
                seen_functions.add(name)
                issues.append(
                    SQLSafetyIssue(
                        "external_access",
                        f"External reader function {name} is not allowed",
                        name,
                    )
                )

    table_class = getattr(exp, "Table", ())
    if table_class:
        for table in statement.find_all(table_class):
            name = str(getattr(table, "name", "") or "")
            # DuckDB accepts `FROM 'file.parquet'` as an implicit file reader.
            if name and _FILE_TABLE_RE.search(name):
                issues.append(
                    SQLSafetyIssue(
                        "external_access",
                        "File paths and URI-like table names are not allowed",
                        name,
                    )
                )

    order = statement.args.get("order") if hasattr(statement, "args") else None
    normalized = statement.sql(dialect=dialect, pretty=False)
    return SQLSafetyResult(
        allowed=not issues,
        statement_count=1,
        statement_type=statement_type,
        normalized_sql=normalized,
        has_order_by=order is not None,
        issues=tuple(issues),
    )


def require_readonly_sql(sql: str, *, dialect: str = "duckdb") -> SQLSafetyResult:
    """Return a successful safety result or raise :class:`SQLSafetyViolation`."""

    result = validate_readonly_sql(sql, dialect=dialect)
    if not result.allowed:
        raise SQLSafetyViolation(result)
    return result


# A concise name for graph nodes and backwards-compatible callers.
check_sql_safety = validate_readonly_sql


__all__ = [
    "SQLSafetyIssue",
    "SQLSafetyResult",
    "SQLSafetyViolation",
    "check_sql_safety",
    "require_readonly_sql",
    "validate_readonly_sql",
]
