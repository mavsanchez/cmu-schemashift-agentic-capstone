"""Low-level PostgreSQL connection and schema initialization."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from schemashift.config import Settings, get_settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class PostgresDatabase:
    """Creates short-lived transactional connections for a local application."""

    def __init__(self, dsn: str, *, connect_timeout_seconds: int = 5) -> None:
        self.dsn = dsn
        self.connect_timeout_seconds = connect_timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> PostgresDatabase:
        configured = settings or get_settings()
        return cls(
            configured.postgres_dsn,
            connect_timeout_seconds=configured.postgres_connect_timeout_seconds,
        )

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """Yield a connection that commits on success and rolls back on failure."""

        with psycopg.connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
            row_factory=dict_row,
        ) as connection:
            yield connection

    def initialize(self) -> None:
        """Create all tables, constraints, and indexes transactionally and idempotently."""

        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.transaction() as connection:
            connection.execute(schema, prepare=False)

    def health_check(self) -> bool:
        with self.transaction() as connection:
            row = connection.execute("SELECT 1 AS healthy").fetchone()
        return bool(row and row["healthy"] == 1)
