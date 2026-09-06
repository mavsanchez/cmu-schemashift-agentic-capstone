"""Stable JSONL, CSV, JSON, and Markdown benchmark reporting."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ArmSummary, BenchmarkArm, BenchmarkReport, BenchmarkResult

CSV_FIELDS = (
    "case_id",
    "motif_id",
    "motif",
    "sql_form",
    "arm",
    "mode",
    "parse_success",
    "schema_valid",
    "execution_success",
    "equivalent",
    "retrieval_recall",
    "recovery",
    "intervention",
    "tool_calls",
    "model_calls",
    "embedding_calls",
    "latency_ms",
    "memory_used",
    "migration_specialist_invocations",
    "validation_specialist_invocations",
    "verdict",
    "route",
    "terminal_status",
    "revision_count",
    "expected_verdict",
    "expected_human_review",
    "expected_equivalent",
    "expected_route_matched",
    "retrieval_top_k",
    "candidate_sql",
    "error",
)


def _rate(results: list[BenchmarkResult], attribute: str) -> float:
    if not results:
        return 0.0
    return sum(bool(getattr(item, attribute)) for item in results) / len(results)


def summarize(results: Iterable[BenchmarkResult]) -> dict[str, ArmSummary]:
    grouped: dict[BenchmarkArm, list[BenchmarkResult]] = {
        BenchmarkArm.BASELINE: [],
        BenchmarkArm.WORKFLOW: [],
    }
    for result in results:
        grouped[result.arm].append(result)

    summaries: dict[str, ArmSummary] = {}
    for arm, items in grouped.items():
        if not items:
            continue
        summaries[arm.value] = ArmSummary(
            cases=len(items),
            parse_rate=_rate(items, "parse_success"),
            schema_rate=_rate(items, "schema_valid"),
            execution_rate=_rate(items, "execution_success"),
            equivalence_rate=_rate(items, "equivalent"),
            expected_route_accuracy=_rate(items, "expected_route_matched"),
            retrieval_recall=(sum(item.retrieval_recall for item in items) / len(items)),
            recoveries=sum(item.recovery for item in items),
            interventions=sum(item.intervention for item in items),
            model_calls=sum(item.model_calls for item in items),
            embedding_calls=sum(item.embedding_calls for item in items),
            tool_calls=sum(item.tool_calls for item in items),
            migration_specialist_invocations=sum(
                item.migration_specialist_invocations for item in items
            ),
            validation_specialist_invocations=sum(
                item.validation_specialist_invocations for item in items
            ),
            total_latency_ms=sum(item.latency_ms for item in items),
        )
    return summaries


def comparison(summaries: dict[str, ArmSummary]) -> dict[str, float]:
    baseline = summaries.get(BenchmarkArm.BASELINE.value)
    workflow = summaries.get(BenchmarkArm.WORKFLOW.value)
    if baseline is None or workflow is None:
        return {}
    return {
        "equivalence_rate_delta": workflow.equivalence_rate - baseline.equivalence_rate,
        "schema_rate_delta": workflow.schema_rate - baseline.schema_rate,
        "execution_rate_delta": workflow.execution_rate - baseline.execution_rate,
        "expected_route_accuracy_delta": (
            workflow.expected_route_accuracy - baseline.expected_route_accuracy
        ),
    }


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, default=str)


def render_markdown(report: BenchmarkReport) -> str:
    lines = [
        "# SchemaShift benchmark result",
        "",
        f"- Mode: `{report.mode.value}`",
        f"- Cases per arm: `{report.case_count}`",
        f"- Chat model: `{report.model}`",
        f"- Embedding model: `{report.embedding_model}`",
        f"- Temperature: `{report.temperature}`",
        f"- Seed: `{report.benchmark_seed}`",
        f"- Completed: `{report.completed_at.isoformat()}`",
        "",
        "| Arm | Equivalent | Schema-valid | Executed | Route accuracy | "
        "Retrieval recall | Reviews | Recovered | Model calls | Embeddings | Tool calls |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in (BenchmarkArm.BASELINE, BenchmarkArm.WORKFLOW):
        summary = report.summaries.get(arm.value)
        if summary is None:
            continue
        lines.append(
            f"| {arm.value} | {summary.equivalence_rate:.1%} | "
            f"{summary.schema_rate:.1%} | {summary.execution_rate:.1%} | "
            f"{summary.expected_route_accuracy:.1%} | {summary.retrieval_recall:.1%} | "
            f"{summary.interventions} | {summary.recoveries} | {summary.model_calls} | "
            f"{summary.embedding_calls} | {summary.tool_calls} |"
        )
    if report.comparison:
        lines.extend(
            [
                "",
                "## Workflow minus baseline",
                "",
                f"- Equivalence: {report.comparison['equivalence_rate_delta']:+.1%}",
                f"- Schema validity: {report.comparison['schema_rate_delta']:+.1%}",
                f"- Execution: {report.comparison['execution_rate_delta']:+.1%}",
                f"- Expected routing: {report.comparison['expected_route_accuracy_delta']:+.1%}",
            ]
        )
    lines.extend(
        [
            "",
            "Known result differences remain represented by `equivalent=false`; "
            "a human approval never changes that measurement.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(
    report: BenchmarkReport,
    output_root: str | Path,
    *,
    update_latest: bool = False,
) -> Path:
    """Write one timestamped result set and optionally the checked-in live pointers."""

    root = Path(output_root).resolve()
    timestamp = report.completed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = root / timestamp
    destination.mkdir(parents=True, exist_ok=False)

    jsonl = "".join(_json(item) + "\n" for item in report.results)
    _atomic_text(destination / "results.jsonl", jsonl)

    csv_path = destination / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in report.results:
            row = result.model_dump(mode="json")
            row["retrieval_top_k"] = json.dumps(row["retrieval_top_k"])
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})

    report_json = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    report_markdown = render_markdown(report)
    _atomic_text(destination / "report.json", report_json)
    _atomic_text(destination / "report.md", report_markdown)
    if update_latest:
        _atomic_text(root / "latest.json", report_json)
        _atomic_text(root / "latest.md", report_markdown)
    return destination


def build_report(
    *,
    benchmark_seed: int,
    mode: str,
    started_at: datetime,
    completed_at: datetime,
    model: str,
    embedding_model: str,
    temperature: float,
    settings: dict[str, Any],
    case_count: int,
    arms: list[str],
    results: list[BenchmarkResult],
) -> BenchmarkReport:
    summaries = summarize(results)
    return BenchmarkReport(
        benchmark_seed=benchmark_seed,
        mode=mode,
        started_at=started_at,
        completed_at=completed_at,
        model=model,
        embedding_model=embedding_model,
        temperature=temperature,
        settings=settings,
        case_count=case_count,
        arms=arms,
        summaries=summaries,
        comparison=comparison(summaries),
        results=results,
    )


__all__ = ["build_report", "comparison", "render_markdown", "summarize", "write_report"]
