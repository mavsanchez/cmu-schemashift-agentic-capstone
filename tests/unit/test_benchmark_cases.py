from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from schemashift.benchmark.cases import (
    BENCHMARK_SEED,
    CASE_COUNT,
    BenchmarkCase,
    SqlForm,
    build_cases,
    load_cases,
    materialize_cases,
)
from schemashift.benchmark.fixtures import create_demo_data
from schemashift.mcp import DatabaseRecord, InMemorySourceRegistry, ToolContext
from schemashift.mcp.tools import compare_results, parse_sql

ROOT = Path(__file__).resolve().parents[2]


def test_matrix_is_exactly_ten_motifs_by_five_forms() -> None:
    cases = build_cases()

    assert len(cases) == CASE_COUNT == 50
    assert len({case.case_id for case in cases}) == 50
    assert {case.seed for case in cases} == {BENCHMARK_SEED}
    assert Counter(case.motif_id for case in cases) == {index: 5 for index in range(1, 11)}
    for motif_id in range(1, 11):
        assert {case.sql_form for case in cases if case.motif_id == motif_id} == set(SqlForm)
    assert sum(case.expected_human_review for case in cases) == 5
    assert {case.motif_id for case in cases if case.expected_human_review} == {10}


def test_checked_manifest_and_sql_files_match_canonical_cases() -> None:
    canonical = build_cases()
    checked = load_cases(ROOT / "benchmark/cases.json")

    assert checked == canonical
    for case in checked:
        assert (ROOT / case.source_paths.source_sql).read_text(encoding="utf-8").strip() == (
            case.original_sql
        )
        assert (ROOT / case.source_paths.gold_sql).read_text(encoding="utf-8").strip() == (
            case.gold_sql
        )
        assert all((ROOT / path).is_file() for path in case.source_paths.knowledge)


def test_materialization_is_idempotent(tmp_path: Path) -> None:
    first = materialize_cases(tmp_path)
    first_bytes = first.read_bytes()
    first_sources = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted((tmp_path / "benchmark").rglob("*.sql"))
    }

    second = materialize_cases(tmp_path)

    assert second == first
    assert second.read_bytes() == first_bytes
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted((tmp_path / "benchmark").rglob("*.sql"))
    } == first_sources


@pytest.fixture(scope="module")
def generated_manifest(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, object]]:
    directory = tmp_path_factory.mktemp("benchmark-oracle")
    return directory, create_demo_data(directory)


def test_all_gold_queries_pass_the_restricted_comparator(
    generated_manifest: tuple[Path, dict[str, object]],
) -> None:
    directory, manifest = generated_manifest
    registry = InMemorySourceRegistry([directory])
    registry.register_database(
        DatabaseRecord(
            database_id="customer_v1",
            path=Path(str(manifest["old_database_path"])),
        )
    )
    registry.register_database(
        DatabaseRecord(
            database_id="customer_v2",
            path=Path(str(manifest["new_database_path"])),
        )
    )
    context = ToolContext(registry=registry)

    failures: list[tuple[str, list[object]]] = []
    for case in build_cases():
        assert parse_sql(case.original_sql)["read_only"] is True
        assert parse_sql(case.gold_sql)["read_only"] is True
        result = compare_results(
            case.old_database_id,
            case.original_sql,
            case.new_database_id,
            case.gold_sql,
            case.comparison_policy,
            context=context,
        )
        if not result["equivalent"]:
            failures.append((case.case_id, result.get("issues", [])))

    assert failures == []


def test_demo_fixture_has_declared_edge_conditions(
    generated_manifest: tuple[Path, dict[str, object]],
) -> None:
    _, manifest = generated_manifest
    assert manifest["seed"] == BENCHMARK_SEED
    assert set(manifest["fixture_features"]) == {
        "nulls",
        "one_to_many_contact_matches",
        "duplicate_lookup_matches",
        "unmatched_lookup_code",
        "boundary_timestamps",
        "empty_lookup_group",
        "repeated_projection_rows",
        "numeric_conversion_edges",
    }
    with duckdb.connect(str(manifest["new_database_path"]), read_only=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sales_orders AS o "
            "LEFT JOIN order_states AS s ON s.state_code = o.order_state_code "
            "WHERE s.state_code IS NULL"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM order_states AS s "
            "LEFT JOIN sales_orders AS o ON o.order_state_code = s.state_code "
            "WHERE o.id IS NULL"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM customer_contacts WHERE customer_id = 4"
        ).fetchone() == (3,)
        # An incomplete customer/contact lookup predicate has two email matches.
        assert connection.execute(
            "SELECT customer_id, contact_type, COUNT(*) FROM customer_contacts "
            "GROUP BY customer_id, contact_type HAVING COUNT(*) > 1"
        ).fetchall() == [(4, "email", 2)]
        # Nulls and the sentinel-backed null conversion are both represented.
        assert connection.execute(
            "SELECT COUNT(*) FROM customers WHERE credit_limit IS NULL"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM customer_profile WHERE region_id IS NULL "
            "AND segment_name = 'UNKNOWN'"
        ).fetchone() == (1,)
        # Leap-day input is retained as the timestamp conversion boundary.
        assert connection.execute(
            "SELECT id FROM customers WHERE signup_ts = TIMESTAMP '2024-02-29'"
        ).fetchall() == [(6,)]
        # Repeated fact projections include customers with two orders.
        assert connection.execute(
            "SELECT customer_id, COUNT(*) FROM sales_orders "
            "GROUP BY customer_id HAVING COUNT(*) > 1 ORDER BY customer_id"
        ).fetchall() == [(1, 2), (2, 2), (4, 2)]
        # Zero, one-cent, fractional-dollar, and >$1,000 conversion edges exist.
        assert connection.execute(
            "SELECT MIN(total_amount), MAX(total_amount), "
            "COUNT(*) FILTER (WHERE total_amount = 0.01) FROM sales_orders"
        ).fetchone() == (Decimal("0.00"), Decimal("1000.01"), 1)


def test_manifest_has_registry_entries_and_stable_logical_fingerprint(
    tmp_path: Path,
) -> None:
    first = create_demo_data(tmp_path)
    payload = json.loads((tmp_path / "demo_manifest.json").read_text(encoding="utf-8"))
    second = create_demo_data(tmp_path)

    assert first["logical_sha256"] == second["logical_sha256"]
    assert payload["databases"] == first["databases"]
    assert {item["role"] for item in payload["sources"]} >= {
        "old_schema",
        "new_schema",
        "migration_knowledge",
    }
    assert all(len(item["sha256"]) == 64 for item in payload["sources"])


def test_case_model_rejects_misaligned_stable_id() -> None:
    payload = build_cases()[0].model_dump(mode="json")
    payload["case_id"] = "m02_projection"
    with pytest.raises(ValueError, match="case_id must be"):
        BenchmarkCase.model_validate(payload)


def test_projection_cases_have_no_filter_clause() -> None:
    projections = [case for case in build_cases() if case.sql_form is SqlForm.PROJECTION]

    assert len(projections) == 10
    assert all(" WHERE " not in case.original_sql.upper() for case in projections)
    assert all(" WHERE " not in case.gold_sql.upper() for case in projections)
