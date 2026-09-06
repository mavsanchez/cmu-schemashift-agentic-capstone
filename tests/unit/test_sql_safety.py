from __future__ import annotations

import pytest

from schemashift.guardrails.sql_safety import (
    SQLSafetyViolation,
    require_readonly_sql,
    validate_readonly_sql,
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, name FROM customers",
        "WITH active AS (SELECT * FROM customers WHERE active) SELECT * FROM active",
        "SELECT 1 AS n UNION ALL SELECT 2 AS n",
        "SELECT * FROM customers ORDER BY id",
    ],
)
def test_allows_one_query_expression(sql: str) -> None:
    assert validate_readonly_sql(sql).allowed


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO customers VALUES (1)",
        "UPDATE customers SET name = 'x'",
        "DELETE FROM customers",
        "DROP TABLE customers",
        "TRUNCATE TABLE customers",
        "ALTER TABLE customers ADD COLUMN n INTEGER",
        "CREATE TABLE copied AS SELECT * FROM customers",
        "COPY customers TO 'out.csv'",
        "ATTACH 'other.duckdb' AS other",
        "PRAGMA database_list",
        "INSTALL httpfs",
        "LOAD httpfs",
    ],
)
def test_rejects_non_query_operations(sql: str) -> None:
    result = validate_readonly_sql(sql)
    assert not result.allowed
    assert result.issues


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv_auto('customers.csv')",
        "SELECT * FROM read_parquet('customers.parquet')",
        "SELECT * FROM 'customers.parquet'",
        "SELECT * FROM postgres_scan('secret', 'public', 'customer')",
        "SELECT * FROM query('SELECT * FROM customers')",
        "SELECT * FROM duckdb_secrets()",
    ],
)
def test_rejects_external_reader_paths(sql: str) -> None:
    result = validate_readonly_sql(sql)
    assert not result.allowed
    assert any(issue.code == "external_access" for issue in result.issues)


def test_rejects_multiple_statements_and_malformed_sql() -> None:
    multiple = validate_readonly_sql("SELECT 1; SELECT 2")
    assert not multiple.allowed
    assert multiple.statement_count == 2

    malformed = validate_readonly_sql("SELECT FROM")
    assert not malformed.allowed


def test_keyword_inside_literal_is_not_treated_as_operation() -> None:
    assert validate_readonly_sql("SELECT 'DROP TABLE x' AS documentation").allowed


def test_require_raises_structured_violation() -> None:
    with pytest.raises(SQLSafetyViolation) as raised:
        require_readonly_sql("DELETE FROM customers")
    assert not raised.value.result.allowed
