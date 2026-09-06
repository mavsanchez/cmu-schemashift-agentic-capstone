from __future__ import annotations

import importlib
import time
from pathlib import Path

import duckdb

from schemashift.mcp import DatabaseRecord, InMemorySourceRegistry, ToolContext
from schemashift.mcp.tools.compare import (
    ComparisonPolicy,
    _unordered_diff,
    compare_results,
)


def _make_db(path: Path, statements: list[str]) -> None:
    connection = duckdb.connect(str(path))
    for statement in statements:
        connection.execute(statement)
    connection.close()


def _context(
    tmp_path: Path,
    old_sql: list[str],
    new_sql: list[str],
    *,
    cap: int = 100,
) -> ToolContext:
    old_path, new_path = tmp_path / "old.duckdb", tmp_path / "new.duckdb"
    _make_db(old_path, old_sql)
    _make_db(new_path, new_sql)
    registry = InMemorySourceRegistry((tmp_path,))
    registry.register_database(DatabaseRecord("old", old_path))
    registry.register_database(DatabaseRecord("new", new_path))
    return ToolContext(registry=registry, max_result_rows=cap)


def test_unordered_comparison_preserves_duplicate_multiplicity(tmp_path: Path) -> None:
    context = _context(
        tmp_path,
        ["CREATE TABLE values_old(v INTEGER)", "INSERT INTO values_old VALUES (1), (1), (2)"],
        ["CREATE TABLE values_new(v INTEGER)", "INSERT INTO values_new VALUES (2), (1), (1)"],
    )
    result = compare_results(
        "old",
        "SELECT v FROM values_old",
        "new",
        "SELECT v FROM values_new",
        context=context,
    )
    assert result["ok"]
    assert result["equivalent"]
    assert result["duplicates"]["old"]["duplicate_row_excess"] == 1


def test_ordered_results_must_match_positionally(tmp_path: Path) -> None:
    context = _context(
        tmp_path,
        ["CREATE TABLE old_t(v INTEGER)", "INSERT INTO old_t VALUES (1), (2)"],
        ["CREATE TABLE new_t(v INTEGER)", "INSERT INTO new_t VALUES (2), (1)"],
    )
    result = compare_results(
        "old",
        "SELECT v FROM old_t",
        "new",
        "SELECT v FROM new_t",
        {"ordered": True},
        context=context,
    )
    assert not result["equivalent"]
    assert result["comparison"]["position_mismatch_count"] == 2


def test_float_tolerance_does_not_apply_to_decimal(tmp_path: Path) -> None:
    context = _context(
        tmp_path,
        [
            "CREATE TABLE old_t(f DOUBLE, d DECIMAL(10,6))",
            "INSERT INTO old_t VALUES (1.0, 1.000001)",
        ],
        [
            "CREATE TABLE new_t(f DOUBLE, d DECIMAL(10,6))",
            "INSERT INTO new_t VALUES (1.0005, 1.000002)",
        ],
    )
    result = compare_results(
        "old",
        "SELECT f, d FROM old_t",
        "new",
        "SELECT f, d FROM new_t",
        {"absolute_tolerance": 0.001, "relative_tolerance": 0.001},
        context=context,
    )
    assert not result["equivalent"]
    assert result["comparison"]["missing_count"] == 1


def test_float_values_compare_with_tolerance(tmp_path: Path) -> None:
    context = _context(
        tmp_path,
        ["CREATE TABLE old_t(v DOUBLE)", "INSERT INTO old_t VALUES (1.0)"],
        ["CREATE TABLE new_t(v DOUBLE)", "INSERT INTO new_t VALUES (1.0000005)"],
    )
    result = compare_results(
        "old", "SELECT v FROM old_t", "new", "SELECT v FROM new_t", context=context
    )
    assert result["equivalent"]


def test_unordered_float_matching_finds_non_greedy_feasible_assignment(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        ["CREATE TABLE old_t(v DOUBLE)", "INSERT INTO old_t VALUES (1.0), (0.0)"],
        ["CREATE TABLE new_t(v DOUBLE)", "INSERT INTO new_t VALUES (0.5), (2.0)"],
    )

    result = compare_results(
        "old",
        "SELECT v FROM old_t",
        "new",
        "SELECT v FROM new_t",
        {"absolute_tolerance": 1.0, "relative_tolerance": 0.0},
        context=context,
    )

    assert result["equivalent"]
    assert result["comparison"]["missing_count"] == 0
    assert result["comparison"]["extra_count"] == 0


def test_unordered_float_matching_scales_to_result_cap() -> None:
    old_rows = [(float(index),) for index in range(10_000)]
    new_rows = [(float(index) + 0.0000005,) for index in reversed(range(10_000))]

    started = time.monotonic()
    missing, extra = _unordered_diff(
        old_rows,
        new_rows,
        ("float",),
        ComparisonPolicy(),
        deadline=started + 5.0,
    )

    assert not missing
    assert not extra
    assert time.monotonic() - started < 5.0


def test_comparison_timeout_is_a_structured_observation(tmp_path: Path, monkeypatch) -> None:
    context = _context(
        tmp_path,
        ["CREATE TABLE old_t(v DOUBLE)", "INSERT INTO old_t VALUES (1.0)"],
        ["CREATE TABLE new_t(v DOUBLE)", "INSERT INTO new_t VALUES (1.0)"],
    )
    compare_module = importlib.import_module("schemashift.mcp.tools.compare")
    clock = iter((100.0, 111.0))
    monkeypatch.setattr(compare_module, "monotonic", lambda: next(clock))

    result = compare_results(
        "old", "SELECT v FROM old_t", "new", "SELECT v FROM new_t", context=context
    )

    assert not result["ok"]
    assert not result["equivalent"]
    assert result["error"]["code"] == "comparison_timeout"
    assert result["execution_ok"]


def test_null_delta_and_schema_mismatch_are_reported(tmp_path: Path) -> None:
    context = _context(
        tmp_path,
        ["CREATE TABLE old_t(v INTEGER)", "INSERT INTO old_t VALUES (NULL), (1)"],
        ["CREATE TABLE new_t(other INTEGER)", "INSERT INTO new_t VALUES (0), (1)"],
    )
    result = compare_results(
        "old", "SELECT v FROM old_t", "new", "SELECT other FROM new_t", context=context
    )
    assert result["ok"]
    assert not result["equivalent"]
    assert result["schema"]["matches"] is False
    assert result["error"]["code"] == "schema_mismatch"


def test_truncation_can_never_pass(tmp_path: Path) -> None:
    context = _context(
        tmp_path,
        ["CREATE TABLE old_t(v INTEGER)", "INSERT INTO old_t VALUES (1), (2), (3)"],
        ["CREATE TABLE new_t(v INTEGER)", "INSERT INTO new_t VALUES (1), (2), (3)"],
        cap=2,
    )
    result = compare_results(
        "old", "SELECT v FROM old_t", "new", "SELECT v FROM new_t", context=context
    )
    assert result["truncated"]
    assert not result["equivalent"]
    assert result["verdict"] == "inconclusive"
