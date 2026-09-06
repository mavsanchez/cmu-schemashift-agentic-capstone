"""Strict local DuckDB read-only execution."""

from __future__ import annotations

import math
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Any
from uuid import UUID

from schemashift.guardrails.sql_safety import SQLSafetyResult, validate_readonly_sql
from schemashift.mcp.context import ToolContext
from schemashift.mcp.registry import RegistryError, resolve_database


@dataclass(slots=True)
class RawQueryResult:
    ok: bool
    database_id: str
    columns: list[dict[str, str]]
    rows: list[tuple[Any, ...]]
    row_count: int
    truncated: bool
    duration_ms: float
    safety: SQLSafetyResult
    error: dict[str, Any] | None = None


class _ConnectionHandle:
    def __init__(self) -> None:
        self.connection: Any | None = None
        self.ready = Event()
        self.lock = Lock()

    def set(self, connection: Any) -> None:
        with self.lock:
            self.connection = connection
        self.ready.set()

    def interrupt(self) -> None:
        with self.lock:
            connection = self.connection
        if connection is not None:
            with suppress(Exception):
                connection.interrupt()


def _error_result(
    database_id: str,
    safety: SQLSafetyResult,
    *,
    code: str,
    message: str,
    started: float,
) -> RawQueryResult:
    return RawQueryResult(
        ok=False,
        database_id=str(database_id),
        columns=[],
        rows=[],
        row_count=0,
        truncated=False,
        duration_ms=round((monotonic() - started) * 1000, 3),
        safety=safety,
        error={"code": code, "message": message},
    )


def _duckdb_connect(path: Path) -> Any:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - dependency configuration
        raise RuntimeError("duckdb is required for SQL execution") from exc

    return duckdb.connect(
        database=str(path),
        read_only=True,
        config={
            "enable_external_access": "false",
            "autoload_known_extensions": "false",
            "autoinstall_known_extensions": "false",
            "threads": "1",
        },
    )


def _run_query(
    path: Path,
    sql: str,
    max_rows: int,
    handle: _ConnectionHandle,
) -> tuple[list[dict[str, str]], list[tuple[Any, ...]], bool]:
    connection = _duckdb_connect(path)
    handle.set(connection)
    try:
        cursor = connection.execute(sql)
        description = cursor.description or []
        columns = [{"name": str(item[0]), "type": str(item[1])} for item in description]
        rows = cursor.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        return columns, [tuple(row) for row in rows[:max_rows]], truncated
    finally:
        connection.close()


def execute_raw_readonly_sql(
    database_id: str,
    sql: str,
    *,
    context: ToolContext,
) -> RawQueryResult:
    """Internal typed executor used by both the public tool and comparator."""

    started = monotonic()
    safety = validate_readonly_sql(sql)
    if not safety.allowed:
        return _error_result(
            database_id,
            safety,
            code="unsafe_sql",
            message="SQL was rejected by the read-only guardrail",
            started=started,
        )

    try:
        database = resolve_database(context.registry, str(database_id))
    except (RegistryError, TypeError, ValueError) as exc:
        return _error_result(
            database_id,
            safety,
            code="unknown_database",
            message=str(exc),
            started=started,
        )

    if not database.path.is_file():
        return _error_result(
            database_id,
            safety,
            code="database_unavailable",
            message=f"Registered DuckDB file does not exist: {database.database_id}",
            started=started,
        )

    handle = _ConnectionHandle()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="schemashift-duckdb")
    future: Future[tuple[list[dict[str, str]], list[tuple[Any, ...]], bool]] = executor.submit(
        _run_query, database.path, sql, context.max_result_rows, handle
    )
    try:
        columns, rows, truncated = future.result(timeout=context.query_timeout_seconds)
    except FutureTimeout:
        handle.interrupt()
        future.cancel()
        return _error_result(
            database_id,
            safety,
            code="query_timeout",
            message=(
                "Query exceeded the configured timeout of "
                f"{context.query_timeout_seconds:g} seconds"
            ),
            started=started,
        )
    except Exception as exc:
        return _error_result(
            database_id,
            safety,
            code="execution_error",
            message=str(exc),
            started=started,
        )
    finally:
        # The worker normally completed or was interrupted.  Avoid waiting forever
        # on a timed-out native query during error reporting.
        executor.shutdown(wait=False, cancel_futures=True)

    return RawQueryResult(
        ok=True,
        database_id=str(database_id),
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        duration_ms=round((monotonic() - started) * 1000, 3),
        safety=safety,
    )


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"non_finite_float": str(value)}
    if isinstance(value, Decimal):
        # Preserve exact decimal semantics across JSON/MCP.
        return {"decimal": str(value)}
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"bytes_hex": bytes(value).hex()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def raw_result_to_dict(result: RawQueryResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": result.ok,
        "database_id": result.database_id,
        "columns": result.columns,
        "rows": [[_json_value(value) for value in row] for row in result.rows],
        "row_count": result.row_count,
        "truncated": result.truncated,
        "duration_ms": result.duration_ms,
        "safety": result.safety.to_dict(),
    }
    if result.error is not None:
        payload["error"] = result.error
    return payload


def execute_readonly_sql(
    database_id: str,
    sql: str,
    *,
    context: ToolContext,
) -> dict[str, Any]:
    """Execute one guarded query against a registered local DuckDB database."""

    return raw_result_to_dict(execute_raw_readonly_sql(database_id, sql, context=context))


__all__ = [
    "RawQueryResult",
    "execute_raw_readonly_sql",
    "execute_readonly_sql",
    "raw_result_to_dict",
]
