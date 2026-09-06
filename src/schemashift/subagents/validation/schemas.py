"""Helpers for converting deterministic tool output to domain reports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from schemashift.domain import (
    ExecutionComparison,
    StructuralChecks,
    ValidationIssue,
    ValidationReport,
    ValidationVerdict,
)

_MISMATCH_SAMPLE_LIMIT = 20


def _issue_message(raw: Any) -> str:
    return str(raw.get("message", raw)) if isinstance(raw, Mapping) else str(raw)


def _schema_issues(schema: Mapping[str, Any]) -> list[Any]:
    issues = list(schema.get("issues", []) or [])
    issues.extend(list(schema.get("diagnostics", []) or []))
    error = schema.get("error")
    if error and error not in issues:
        issues.append(error)
    return issues


def build_validation_report(
    parsed: Mapping[str, Any],
    schema: Mapping[str, Any],
    comparison: Mapping[str, Any],
) -> ValidationReport:
    target_tables = {
        str(item.get("name"))
        for item in schema.get("tables", [])
        if isinstance(item, Mapping) and item.get("name")
    }
    referenced_tables = {str(name) for name in parsed.get("tables", [])}
    tables_exist = bool(schema.get("ok", True)) and referenced_tables <= target_tables
    new_execution = comparison.get("new_execution")
    new_execution = new_execution if isinstance(new_execution, Mapping) else {}
    columns_exist = bool(new_execution.get("ok", False))
    diagnostics = [_issue_message(item) for item in parsed.get("diagnostics", [])]
    diagnostics.extend(_issue_message(item) for item in _schema_issues(schema))
    if referenced_tables - target_tables:
        diagnostics.append(
            "Unknown target tables: " + ", ".join(sorted(referenced_tables - target_tables))
        )

    structural = StructuralChecks(
        parse_success=bool(parsed.get("ok")),
        readonly_safe=bool(parsed.get("read_only")),
        schema_valid=bool(schema.get("ok", True)),
        tables_exist=tables_exist,
        columns_exist=columns_exist,
        diagnostics=diagnostics,
    )
    old_execution = comparison.get("old_execution")
    old_execution = old_execution if isinstance(old_execution, Mapping) else {}
    schema_report = comparison.get("schema")
    schema_report = schema_report if isinstance(schema_report, Mapping) else {}
    old_columns = schema_report.get("old_columns")
    old_columns = old_columns if isinstance(old_columns, list) else []
    compare_detail = comparison.get("comparison")
    compare_detail = compare_detail if isinstance(compare_detail, Mapping) else {}
    nulls = comparison.get("nulls")
    nulls = nulls if isinstance(nulls, Mapping) else {}
    duplicates = comparison.get("duplicates")
    duplicates = duplicates if isinstance(duplicates, Mapping) else {}
    new_duplicates = duplicates.get("new")
    new_duplicates = new_duplicates if isinstance(new_duplicates, Mapping) else {}
    truncated = bool(old_execution.get("truncated") or new_execution.get("truncated"))

    mismatch_samples = [
        *(
            {"kind": "missing", "row": list(row) if isinstance(row, (list, tuple)) else row}
            for row in list(compare_detail.get("missing_rows", []) or [])
        ),
        *(
            {"kind": "extra", "row": list(row) if isinstance(row, (list, tuple)) else row}
            for row in list(compare_detail.get("extra_rows", []) or [])
        ),
        *(
            dict(item, kind=str(item.get("kind", "position")))
            if isinstance(item, Mapping)
            else {"kind": "position", "detail": item}
            for item in list(compare_detail.get("position_mismatches", []) or [])
        ),
    ][:_MISMATCH_SAMPLE_LIMIT]

    execution = ExecutionComparison(
        execution_success=bool(comparison.get("ok"))
        and bool(old_execution.get("ok", False))
        and bool(new_execution.get("ok", False)),
        equivalent=bool(comparison.get("equivalent")),
        old_row_count=old_execution.get("row_count"),
        new_row_count=new_execution.get("row_count"),
        column_names=[str(item.get("name")) for item in old_columns if isinstance(item, Mapping)],
        canonical_types=[
            str(item.get("canonical_type")) for item in old_columns if isinstance(item, Mapping)
        ],
        ordered=bool(comparison.get("ordered")),
        truncated=truncated,
        missing_row_count=int(compare_detail.get("missing_count", 0) or 0),
        extra_row_count=int(compare_detail.get("extra_count", 0) or 0),
        duplicate_keys=list(new_duplicates.get("samples", []) or []),
        null_deltas=dict(nulls.get("delta", {}) or {}),
        numeric_aggregates=dict(comparison.get("numeric_aggregates", {}) or {}),
        mismatch_samples=mismatch_samples,
        errors=[
            str(issue.get("message", issue)) if isinstance(issue, Mapping) else str(issue)
            for issue in comparison.get("issues", [])
        ],
    )

    raw_issues = [
        *(parsed.get("diagnostics", []) or []),
        *_schema_issues(schema),
        *(comparison.get("issues", []) or []),
    ]
    issues: list[ValidationIssue] = []
    for raw in raw_issues:
        if isinstance(raw, Mapping):
            code = str(raw.get("code", "validation_issue"))
            message = str(raw.get("message", raw))
        else:
            code, message = "validation_issue", str(raw)
        issues.append(ValidationIssue(code=code, message=message))

    if structural.passed and execution.execution_success and execution.equivalent and not truncated:
        verdict = ValidationVerdict.PASS
    elif truncated:
        verdict = ValidationVerdict.HUMAN_REVIEW
    else:
        verdict = ValidationVerdict.REVISE
    return ValidationReport(
        verdict=verdict,
        structural_checks=structural,
        execution_comparison=execution,
        issues=issues,
    )
