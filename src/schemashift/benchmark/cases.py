"""The checked, deterministic 10-motif by 5-form benchmark matrix."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path, PurePosixPath
from textwrap import dedent
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BENCHMARK_SEED = 20260905
MOTIF_COUNT = 10
FORMS_PER_MOTIF = 5
CASE_COUNT = MOTIF_COUNT * FORMS_PER_MOTIF


class SqlForm(StrEnum):
    PROJECTION = "projection"
    FILTER = "filter"
    JOIN = "join"
    AGGREGATE = "aggregate"
    CTE_SUBQUERY = "cte_subquery"


class CaseSources(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    old_schema: str
    new_schema: str
    source_sql: str
    gold_sql: str
    knowledge: list[str] = Field(min_length=1)

    @field_validator("old_schema", "new_schema", "source_sql", "gold_sql")
    @classmethod
    def relative_file(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("benchmark source paths must be repository-relative")
        return path.as_posix()

    @field_validator("knowledge")
    @classmethod
    def relative_knowledge(cls, values: list[str]) -> list[str]:
        return [cls.relative_file(value) for value in values]


class BenchmarkCase(BaseModel):
    """One stable benchmark task and its independently executable oracle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^m(0[1-9]|10)_(projection|filter|join|aggregate|cte_subquery)$")
    seed: Literal[20260905] = BENCHMARK_SEED
    motif_id: int = Field(ge=1, le=10)
    motif: str = Field(min_length=1)
    sql_form: SqlForm
    prompt: str = Field(min_length=1)
    source_paths: CaseSources
    original_sql: str = Field(min_length=1)
    gold_sql: str = Field(min_length=1)
    expected_impact: dict[str, list[str]]
    expected_evidence_ids: list[str] = Field(min_length=1)
    old_database_id: Literal["customer_v1"] = "customer_v1"
    new_database_id: Literal["customer_v2"] = "customer_v2"
    comparison_policy: dict[str, Any]
    expected_verdict: Literal["pass", "human_review"]
    expected_human_review: bool
    expected_equivalent: bool = True

    @model_validator(mode="after")
    def coherent_identity(self) -> BenchmarkCase:
        expected_id = f"m{self.motif_id:02d}_{self.sql_form.value}"
        if self.case_id != expected_id:
            raise ValueError(f"case_id must be {expected_id}")
        if (self.expected_verdict == "human_review") != self.expected_human_review:
            raise ValueError("review verdict and expected_human_review must agree")
        return self


def _sql(value: str) -> str:
    return dedent(value).strip().rstrip(";") + ";"


def _document_id(path: str) -> str:
    # Knowledge ingestion uses the path relative to data/knowledge as document identity.
    document = PurePosixPath(path).name
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _pair(old: str, new: str) -> tuple[str, str]:
    return _sql(old), _sql(new)


_MOTIFS: tuple[dict[str, Any], ...] = (
    {
        "name": "table_rename",
        "summary": "orders was renamed to sales_orders",
        "knowledge": ["data/knowledge/order_migration.md"],
        "impact": {
            "tables": ["orders", "sales_orders"],
            "columns": ["orders.order_id", "sales_orders.id"],
            "expressions": ["table and identifier rename"],
        },
        "queries": {
            SqlForm.PROJECTION: _pair(
                "SELECT customer_id FROM orders",
                "SELECT customer_id FROM sales_orders",
            ),
            SqlForm.FILTER: _pair(
                "SELECT customer_id FROM orders WHERE customer_id >= 4",
                "SELECT customer_id FROM sales_orders WHERE customer_id >= 4",
            ),
            SqlForm.JOIN: _pair(
                """
                SELECT o.customer_id
                FROM orders AS o
                JOIN customer AS c ON c.customer_id = o.customer_id
                WHERE c.account_state = 'OPEN'
                """,
                """
                SELECT o.customer_id
                FROM sales_orders AS o
                JOIN customers AS c ON c.id = o.customer_id
                WHERE c.account_state = 'OPEN'
                """,
            ),
            SqlForm.AGGREGATE: _pair(
                "SELECT customer_id, COUNT(*) AS order_count FROM orders GROUP BY customer_id",
                "SELECT customer_id, COUNT(*) AS order_count FROM sales_orders "
                "GROUP BY customer_id",
            ),
            SqlForm.CTE_SUBQUERY: _pair(
                """
                WITH recent_orders AS (
                    SELECT customer_id FROM orders WHERE order_id >= 104
                )
                SELECT customer_id FROM recent_orders
                """,
                """
                WITH recent_orders AS (
                    SELECT customer_id FROM sales_orders WHERE id >= 104
                )
                SELECT customer_id FROM recent_orders
                """,
            ),
        },
    },
    {
        "name": "column_rename",
        "summary": "customer identifiers and names were renamed",
        "knowledge": ["data/knowledge/customer_migration.md"],
        "impact": {
            "tables": ["customer", "customers"],
            "columns": ["customer_id", "id", "full_name", "display_name"],
            "expressions": ["column aliases preserve the legacy output contract"],
        },
        "queries": {
            SqlForm.PROJECTION: _pair(
                "SELECT customer_id, full_name FROM customer",
                "SELECT id AS customer_id, display_name AS full_name FROM customers",
            ),
            SqlForm.FILTER: _pair(
                "SELECT customer_id, full_name FROM customer WHERE full_name LIKE 'A%'",
                "SELECT id AS customer_id, display_name AS full_name FROM customers "
                "WHERE display_name LIKE 'A%'",
            ),
            SqlForm.JOIN: _pair(
                """
                SELECT c.customer_id, c.full_name
                FROM customer AS c
                JOIN orders AS o ON o.customer_id = c.customer_id
                """,
                """
                SELECT c.id AS customer_id, c.display_name AS full_name
                FROM customers AS c
                JOIN sales_orders AS o ON o.customer_id = c.id
                """,
            ),
            SqlForm.AGGREGATE: _pair(
                "SELECT full_name, COUNT(*) AS customer_count FROM customer GROUP BY full_name",
                "SELECT display_name AS full_name, COUNT(*) AS customer_count "
                "FROM customers GROUP BY display_name",
            ),
            SqlForm.CTE_SUBQUERY: _pair(
                """
                WITH named_customers AS (
                    SELECT customer_id, full_name FROM customer
                )
                SELECT customer_id, full_name FROM named_customers WHERE customer_id <= 3
                """,
                """
                WITH named_customers AS (
                    SELECT id AS customer_id, display_name AS full_name FROM customers
                )
                SELECT customer_id, full_name FROM named_customers WHERE customer_id <= 3
                """,
            ),
        },
    },
    {
        "name": "integer_cents_to_decimal_currency",
        "summary": "integer order cents became decimal currency",
        "knowledge": ["data/knowledge/order_migration.md"],
        "impact": {
            "tables": ["orders", "sales_orders"],
            "columns": ["total_cents", "total_amount"],
            "expressions": ["CAST(total_amount * 100 AS BIGINT)"],
        },
        "queries": {
            SqlForm.PROJECTION: _pair(
                "SELECT order_id, total_cents FROM orders",
                "SELECT id AS order_id, CAST(total_amount * 100 AS BIGINT) AS total_cents "
                "FROM sales_orders",
            ),
            SqlForm.FILTER: _pair(
                "SELECT order_id, total_cents FROM orders WHERE total_cents >= 10000",
                "SELECT id AS order_id, CAST(total_amount * 100 AS BIGINT) AS total_cents "
                "FROM sales_orders WHERE total_amount >= 100.00",
            ),
            SqlForm.JOIN: _pair(
                """
                SELECT c.customer_id, o.total_cents
                FROM customer AS c
                JOIN orders AS o ON o.customer_id = c.customer_id
                """,
                """
                SELECT c.id AS customer_id, CAST(o.total_amount * 100 AS BIGINT) AS total_cents
                FROM customers AS c
                JOIN sales_orders AS o ON o.customer_id = c.id
                """,
            ),
            SqlForm.AGGREGATE: _pair(
                "SELECT customer_id, SUM(total_cents) AS total_cents "
                "FROM orders GROUP BY customer_id",
                "SELECT customer_id, SUM(CAST(total_amount * 100 AS BIGINT)) AS total_cents "
                "FROM sales_orders GROUP BY customer_id",
            ),
            SqlForm.CTE_SUBQUERY: _pair(
                """
                WITH order_values AS (
                    SELECT order_id, total_cents FROM orders
                )
                SELECT order_id, total_cents FROM order_values
                WHERE total_cents BETWEEN 2500 AND 50000
                """,
                """
                WITH order_values AS (
                    SELECT id AS order_id, CAST(total_amount * 100 AS BIGINT) AS total_cents
                    FROM sales_orders
                )
                SELECT order_id, total_cents FROM order_values
                WHERE total_cents BETWEEN 2500 AND 50000
                """,
            ),
        },
    },
    {
        "name": "text_date_to_timestamp",
        "summary": "legacy date text became a timestamp",
        "knowledge": [
            "data/knowledge/customer_migration.md",
            "data/knowledge/sql_standards.md",
        ],
        "impact": {
            "tables": ["customer", "customers"],
            "columns": ["signup_date", "signup_ts"],
            "expressions": ["strftime(signup_ts, '%Y-%m-%d')"],
        },
        "queries": {
            SqlForm.PROJECTION: _pair(
                "SELECT customer_id, signup_date FROM customer",
                "SELECT id AS customer_id, strftime(signup_ts, '%Y-%m-%d') AS signup_date "
                "FROM customers",
            ),
            SqlForm.FILTER: _pair(
                "SELECT customer_id, signup_date FROM customer WHERE signup_date >= '2024-01-01'",
                "SELECT id AS customer_id, strftime(signup_ts, '%Y-%m-%d') AS signup_date "
                "FROM customers WHERE signup_ts >= TIMESTAMP '2024-01-01'",
            ),
            SqlForm.JOIN: _pair(
                """
                SELECT c.customer_id, o.order_date
                FROM customer AS c
                JOIN orders AS o ON o.customer_id = c.customer_id
                WHERE o.order_date >= c.signup_date
                """,
                """
                SELECT c.id AS customer_id, strftime(o.ordered_at, '%Y-%m-%d') AS order_date
                FROM customers AS c
                JOIN sales_orders AS o ON o.customer_id = c.id
                WHERE o.ordered_at >= c.signup_ts
                """,
            ),
            SqlForm.AGGREGATE: _pair(
                "SELECT account_state, MIN(signup_date) AS first_signup_date "
                "FROM customer GROUP BY account_state",
                "SELECT account_state, strftime(MIN(signup_ts), '%Y-%m-%d') "
                "AS first_signup_date FROM customers GROUP BY account_state",
            ),
            SqlForm.CTE_SUBQUERY: _pair(
                """
                WITH signup_days AS (
                    SELECT customer_id, signup_date FROM customer
                )
                SELECT signup_date FROM signup_days
                WHERE signup_date BETWEEN '2024-01-01' AND '2024-12-31'
                """,
                """
                WITH signup_days AS (
                    SELECT id AS customer_id, strftime(signup_ts, '%Y-%m-%d') AS signup_date
                    FROM customers
                )
                SELECT signup_date FROM signup_days
                WHERE signup_date BETWEEN '2024-01-01' AND '2024-12-31'
                """,
            ),
        },
    },
    {
        "name": "status_moved_to_lookup_table",
        "summary": "descriptive customer status moved to a lookup table",
        "knowledge": ["data/knowledge/status_migration.md"],
        "impact": {
            "tables": ["customer", "customers", "customer_status"],
            "columns": ["customer_status", "status_code", "status_description"],
            "expressions": ["left join to customer_status"],
        },
        "queries": {
            SqlForm.PROJECTION: _pair(
                "SELECT customer_id, customer_status FROM customer",
                """
                SELECT c.id AS customer_id, s.status_description AS customer_status
                FROM customers AS c
                LEFT JOIN customer_status AS s ON s.status_code = c.status_code
                """,
            ),
            SqlForm.FILTER: _pair(
                "SELECT customer_id FROM customer WHERE customer_status = 'Active'",
                """
                SELECT c.id AS customer_id
                FROM customers AS c
                JOIN customer_status AS s ON s.status_code = c.status_code
                WHERE s.status_description = 'Active'
                """,
            ),
            SqlForm.JOIN: _pair(
                """
                SELECT o.order_id, c.customer_status
                FROM orders AS o
                JOIN customer AS c ON c.customer_id = o.customer_id
                """,
                """
                SELECT o.id AS order_id, s.status_description AS customer_status
                FROM sales_orders AS o
                JOIN customers AS c ON c.id = o.customer_id
                LEFT JOIN customer_status AS s ON s.status_code = c.status_code
                """,
            ),
            SqlForm.AGGREGATE: _pair(
                "SELECT customer_status, COUNT(*) AS customer_count "
                "FROM customer GROUP BY customer_status",
                """
                SELECT s.status_description AS customer_status, COUNT(*) AS customer_count
                FROM customers AS c
                LEFT JOIN customer_status AS s ON s.status_code = c.status_code
                GROUP BY s.status_description
                """,
            ),
            SqlForm.CTE_SUBQUERY: _pair(
                """
                WITH statuses AS (
                    SELECT customer_id, customer_status FROM customer
                )
                SELECT customer_id FROM statuses WHERE customer_status = 'Pending'
                """,
                """
                WITH statuses AS (
                    SELECT c.id AS customer_id, s.status_description AS customer_status
                    FROM customers AS c
                    LEFT JOIN customer_status AS s ON s.status_code = c.status_code
                )
                SELECT customer_id FROM statuses WHERE customer_status = 'Pending'
                """,
            ),
        },
    },
    {
        "name": "attribute_moved_to_another_table",
        "summary": "customer region moved through profile and region tables",
        "knowledge": ["data/knowledge/customer_profile_migration.md"],
        "impact": {
            "tables": ["customer", "customers", "customer_profile", "regions"],
            "columns": ["region", "region_id", "region_name"],
            "expressions": ["left joins preserve null regions"],
        },
        "queries": {
            SqlForm.PROJECTION: _pair(
                "SELECT customer_id, region FROM customer",
                """
                SELECT c.id AS customer_id, r.region_name AS region
                FROM customers AS c
                LEFT JOIN customer_profile AS p ON p.customer_id = c.id
                LEFT JOIN regions AS r ON r.region_id = p.region_id
                """,
            ),
            SqlForm.FILTER: _pair(
                "SELECT customer_id FROM customer WHERE region = 'West'",
                """
                SELECT c.id AS customer_id
                FROM customers AS c
                JOIN customer_profile AS p ON p.customer_id = c.id
                JOIN regions AS r ON r.region_id = p.region_id
                WHERE r.region_name = 'West'
                """,
            ),
            SqlForm.JOIN: _pair(
                """
                SELECT o.order_id, c.region
                FROM orders AS o
                JOIN customer AS c ON c.customer_id = o.customer_id
                """,
                """
                SELECT o.id AS order_id, r.region_name AS region
                FROM sales_orders AS o
                JOIN customers AS c ON c.id = o.customer_id
                LEFT JOIN customer_profile AS p ON p.customer_id = c.id
                LEFT JOIN regions AS r ON r.region_id = p.region_id
                """,
            ),
            SqlForm.AGGREGATE: _pair(
                "SELECT region, COUNT(*) AS customer_count FROM customer GROUP BY region",
                """
                SELECT r.region_name AS region, COUNT(*) AS customer_count
                FROM customers AS c
                LEFT JOIN customer_profile AS p ON p.customer_id = c.id
                LEFT JOIN regions AS r ON r.region_id = p.region_id
                GROUP BY r.region_name
                """,
            ),
            SqlForm.CTE_SUBQUERY: _pair(
                """
                WITH westerners AS (
                    SELECT customer_id, region FROM customer WHERE region = 'West'
                )
                SELECT customer_id, region FROM westerners
                """,
                """
                WITH westerners AS (
                    SELECT c.id AS customer_id, r.region_name AS region
                    FROM customers AS c
                    JOIN customer_profile AS p ON p.customer_id = c.id
                    JOIN regions AS r ON r.region_id = p.region_id
                    WHERE r.region_name = 'West'
                )
                SELECT customer_id, region FROM westerners
                """,
            ),
        },
    },
    {
        "name": "derived_business_rule_mapping",
        "summary": "customer_active is derived from account state",
        "knowledge": ["data/knowledge/customer_active_rule.md"],
        "impact": {
            "tables": ["customer", "customers"],
            "columns": ["customer_active", "account_state"],
            "expressions": ["account_state = 'OPEN'"],
        },
        "queries": {
            SqlForm.PROJECTION: _pair(
                "SELECT customer_id, customer_active FROM customer",
                "SELECT id AS customer_id, (account_state = 'OPEN') AS customer_active "
                "FROM customers",
            ),
            SqlForm.FILTER: _pair(
                "SELECT customer_id FROM customer WHERE customer_active",
                "SELECT id AS customer_id FROM customers WHERE account_state = 'OPEN'",
            ),
            SqlForm.JOIN: _pair(
                """
                SELECT o.order_id, c.customer_active
                FROM orders AS o
                JOIN customer AS c ON c.customer_id = o.customer_id
                """,
                """
                SELECT o.id AS order_id, (c.account_state = 'OPEN') AS customer_active
                FROM sales_orders AS o
                JOIN customers AS c ON c.id = o.customer_id
                """,
            ),
            SqlForm.AGGREGATE: _pair(
                "SELECT customer_active, COUNT(*) AS customer_count "
                "FROM customer GROUP BY customer_active",
                """
                SELECT (account_state = 'OPEN') AS customer_active, COUNT(*) AS customer_count
                FROM customers
                GROUP BY (account_state = 'OPEN')
                """,
            ),
            SqlForm.CTE_SUBQUERY: _pair(
                """
                WITH flags AS (
                    SELECT customer_id, customer_active FROM customer
                )
                SELECT customer_id FROM flags WHERE customer_active = FALSE
                """,
                """
                WITH flags AS (
                    SELECT id AS customer_id, (account_state = 'OPEN') AS customer_active
                    FROM customers
                )
                SELECT customer_id FROM flags WHERE customer_active = FALSE
                """,
            ),
        },
    },
    {
        "name": "one_to_many_cardinality_hazard",
        "summary": "primary email moved into a one-to-many contact table",
        "knowledge": ["data/knowledge/contact_migration.md"],
        "impact": {
            "tables": ["customer", "customers", "customer_contacts"],
            "columns": ["primary_email", "contact_type", "is_primary", "contact_value"],
            "expressions": ["cardinality-safe filtered left join"],
        },
        "queries": {
            SqlForm.PROJECTION: _pair(
                "SELECT customer_id, primary_email FROM customer",
                """
                SELECT c.id AS customer_id, e.contact_value AS primary_email
                FROM customers AS c
                LEFT JOIN customer_contacts AS e
                  ON e.customer_id = c.id AND e.contact_type = 'email' AND e.is_primary
                """,
            ),
            SqlForm.FILTER: _pair(
                "SELECT customer_id FROM customer WHERE primary_email IS NOT NULL",
                """
                SELECT c.id AS customer_id
                FROM customers AS c
                JOIN customer_contacts AS e
                  ON e.customer_id = c.id AND e.contact_type = 'email' AND e.is_primary
                WHERE e.contact_value IS NOT NULL
                """,
            ),
            SqlForm.JOIN: _pair(
                """
                SELECT o.order_id, c.primary_email
                FROM orders AS o
                JOIN customer AS c ON c.customer_id = o.customer_id
                """,
                """
                SELECT o.id AS order_id, e.contact_value AS primary_email
                FROM sales_orders AS o
                JOIN customers AS c ON c.id = o.customer_id
                LEFT JOIN customer_contacts AS e
                  ON e.customer_id = c.id AND e.contact_type = 'email' AND e.is_primary
                """,
            ),
            SqlForm.AGGREGATE: _pair(
                "SELECT COUNT(primary_email) AS email_count FROM customer",
                """
                SELECT COUNT(e.contact_value) AS email_count
                FROM customers AS c
                LEFT JOIN customer_contacts AS e
                  ON e.customer_id = c.id AND e.contact_type = 'email' AND e.is_primary
                """,
            ),
            SqlForm.CTE_SUBQUERY: _pair(
                """
                WITH emails AS (
                    SELECT customer_id, primary_email FROM customer
                )
                SELECT customer_id, primary_email
                FROM emails WHERE primary_email LIKE '%@example.test'
                """,
                """
                WITH emails AS (
                    SELECT c.id AS customer_id, e.contact_value AS primary_email
                    FROM customers AS c
                    LEFT JOIN customer_contacts AS e
                      ON e.customer_id = c.id AND e.contact_type = 'email' AND e.is_primary
                )
                SELECT customer_id, primary_email
                FROM emails WHERE primary_email LIKE '%@example.test'
                """,
            ),
        },
    },
    {
        "name": "null_default_semantic_change",
        "summary": "a nullable segment became the UNKNOWN sentinel",
        "knowledge": [
            "data/knowledge/customer_profile_migration.md",
            "data/knowledge/profile_defaults.md",
        ],
        "impact": {
            "tables": ["customer", "customer_profile"],
            "columns": ["segment", "segment_name"],
            "expressions": ["NULLIF(segment_name, 'UNKNOWN')"],
        },
        "queries": {
            SqlForm.PROJECTION: _pair(
                "SELECT customer_id, segment FROM customer",
                "SELECT customer_id, NULLIF(segment_name, 'UNKNOWN') AS segment "
                "FROM customer_profile",
            ),
            SqlForm.FILTER: _pair(
                "SELECT customer_id FROM customer WHERE segment IS NULL",
                "SELECT customer_id FROM customer_profile WHERE segment_name = 'UNKNOWN'",
            ),
            SqlForm.JOIN: _pair(
                """
                SELECT o.order_id, c.segment
                FROM orders AS o
                JOIN customer AS c ON c.customer_id = o.customer_id
                """,
                """
                SELECT o.id AS order_id, NULLIF(p.segment_name, 'UNKNOWN') AS segment
                FROM sales_orders AS o
                JOIN customer_profile AS p ON p.customer_id = o.customer_id
                """,
            ),
            SqlForm.AGGREGATE: _pair(
                "SELECT segment, COUNT(*) AS customer_count FROM customer GROUP BY segment",
                """
                SELECT NULLIF(segment_name, 'UNKNOWN') AS segment, COUNT(*) AS customer_count
                FROM customer_profile
                GROUP BY NULLIF(segment_name, 'UNKNOWN')
                """,
            ),
            SqlForm.CTE_SUBQUERY: _pair(
                """
                WITH segments AS (
                    SELECT customer_id, segment FROM customer
                )
                SELECT customer_id, segment FROM segments
                WHERE segment IS NULL OR segment = 'premium'
                """,
                """
                WITH segments AS (
                    SELECT customer_id, NULLIF(segment_name, 'UNKNOWN') AS segment
                    FROM customer_profile
                )
                SELECT customer_id, segment FROM segments
                WHERE segment IS NULL OR segment = 'premium'
                """,
            ),
        },
    },
    {
        "name": "conflicting_or_insufficient_evidence",
        "summary": "status evidence conflicts and requires human review",
        "knowledge": [
            "data/knowledge/status_migration.md",
            "data/knowledge/status_conflict.md",
        ],
        "impact": {
            "tables": ["customer", "customers", "customer_status"],
            "columns": ["customer_status", "status_code"],
            "expressions": ["conflicting Pending-to-code mappings"],
        },
        "review": True,
        "queries": {
            SqlForm.PROJECTION: _pair(
                "SELECT customer_id, customer_status FROM customer",
                """
                SELECT c.id AS customer_id, s.status_description AS customer_status
                FROM customers AS c
                LEFT JOIN customer_status AS s ON s.status_code = c.status_code
                """,
            ),
            SqlForm.FILTER: _pair(
                "SELECT customer_id FROM customer WHERE customer_status IN ('Pending', 'Inactive')",
                """
                SELECT c.id AS customer_id
                FROM customers AS c
                JOIN customer_status AS s ON s.status_code = c.status_code
                WHERE s.status_description IN ('Pending', 'Inactive')
                """,
            ),
            SqlForm.JOIN: _pair(
                """
                SELECT o.order_id, c.customer_status
                FROM orders AS o
                JOIN customer AS c ON c.customer_id = o.customer_id
                WHERE c.customer_status = 'Pending'
                """,
                """
                SELECT o.id AS order_id, s.status_description AS customer_status
                FROM sales_orders AS o
                JOIN customers AS c ON c.id = o.customer_id
                JOIN customer_status AS s ON s.status_code = c.status_code
                WHERE s.status_description = 'Pending'
                """,
            ),
            SqlForm.AGGREGATE: _pair(
                "SELECT customer_status, COUNT(*) AS customer_count "
                "FROM customer GROUP BY customer_status",
                """
                SELECT s.status_description AS customer_status, COUNT(*) AS customer_count
                FROM customers AS c
                LEFT JOIN customer_status AS s ON s.status_code = c.status_code
                GROUP BY s.status_description
                """,
            ),
            SqlForm.CTE_SUBQUERY: _pair(
                """
                WITH pending_customers AS (
                    SELECT customer_id FROM customer WHERE customer_status = 'Pending'
                )
                SELECT customer_id FROM pending_customers
                """,
                """
                WITH pending_customers AS (
                    SELECT c.id AS customer_id
                    FROM customers AS c
                    JOIN customer_status AS s ON s.status_code = c.status_code
                    WHERE s.status_description = 'Pending'
                )
                SELECT customer_id FROM pending_customers
                """,
            ),
        },
    },
)


def build_cases() -> tuple[BenchmarkCase, ...]:
    """Build all 50 cases in a stable motif-major, form-minor order."""

    cases: list[BenchmarkCase] = []
    for motif_id, specification in enumerate(_MOTIFS, start=1):
        knowledge = list(specification["knowledge"])
        expected_review = bool(specification.get("review", False))
        for sql_form in SqlForm:
            case_id = f"m{motif_id:02d}_{sql_form.value}"
            original_sql, gold_sql = specification["queries"][sql_form]
            cases.append(
                BenchmarkCase(
                    case_id=case_id,
                    motif_id=motif_id,
                    motif=str(specification["name"]),
                    sql_form=sql_form,
                    prompt=(
                        f"Migrate this {sql_form.value.replace('_', '/')} query for the "
                        f"customer_v2 schema. Preserve column names, types, nulls, row "
                        f"multiplicity, and business meaning. Migration focus: "
                        f"{specification['summary']}."
                    ),
                    source_paths=CaseSources(
                        old_schema="data/schemas/customer_v1.sql",
                        new_schema="data/schemas/customer_v2.sql",
                        source_sql=f"benchmark/sql/{case_id}.sql",
                        gold_sql=f"benchmark/gold/{case_id}.sql",
                        knowledge=knowledge,
                    ),
                    original_sql=original_sql,
                    gold_sql=gold_sql,
                    expected_impact={
                        key: list(values) for key, values in specification["impact"].items()
                    },
                    expected_evidence_ids=[_document_id(path) for path in knowledge],
                    comparison_policy={
                        "ordered": False,
                        "absolute_tolerance": 1e-6,
                        "relative_tolerance": 1e-6,
                        "sample_limit": 20,
                    },
                    expected_verdict="human_review" if expected_review else "pass",
                    expected_human_review=expected_review,
                )
            )
    if len(cases) != CASE_COUNT:
        raise AssertionError(f"benchmark matrix contains {len(cases)} cases, expected {CASE_COUNT}")
    return tuple(cases)


def _canonical_payload(cases: tuple[BenchmarkCase, ...]) -> list[dict[str, Any]]:
    return [case.model_dump(mode="json") for case in cases]


def load_cases(path: str | Path | None = None) -> tuple[BenchmarkCase, ...]:
    """Load the checked manifest, or return the in-code canonical matrix."""

    if path is None:
        return build_cases()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("benchmark case manifest must contain a JSON array")
    cases = tuple(BenchmarkCase.model_validate(item) for item in payload)
    _validate_matrix(cases)
    return cases


def _validate_matrix(cases: tuple[BenchmarkCase, ...]) -> None:
    if len(cases) != CASE_COUNT:
        raise ValueError(f"expected exactly {CASE_COUNT} benchmark cases")
    ids = [case.case_id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("benchmark case IDs must be unique")
    observed = {(case.motif_id, case.sql_form) for case in cases}
    expected = {(motif, form) for motif in range(1, MOTIF_COUNT + 1) for form in SqlForm}
    if observed != expected:
        raise ValueError("benchmark must contain each of ten motifs in all five SQL forms")


def materialize_cases(root: str | Path, cases: tuple[BenchmarkCase, ...] | None = None) -> Path:
    """Idempotently write the case manifest and its source/gold SQL fixtures."""

    repository = Path(root).resolve()
    selected = cases or build_cases()
    _validate_matrix(selected)
    for case in selected:
        for relative, sql in (
            (case.source_paths.source_sql, case.original_sql),
            (case.source_paths.gold_sql, case.gold_sql),
        ):
            target = (repository / relative).resolve()
            if repository not in target.parents:
                raise ValueError("benchmark fixture path escaped the repository")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(sql.rstrip() + "\n", encoding="utf-8")
    manifest = repository / "benchmark" / "cases.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(_canonical_payload(selected), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def case_index(cases: tuple[BenchmarkCase, ...]) -> Mapping[str, BenchmarkCase]:
    return {case.case_id: case for case in cases}


__all__ = [
    "BENCHMARK_SEED",
    "CASE_COUNT",
    "FORMS_PER_MOTIF",
    "MOTIF_COUNT",
    "BenchmarkCase",
    "CaseSources",
    "SqlForm",
    "build_cases",
    "case_index",
    "load_cases",
    "materialize_cases",
]
