from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from schemashift.mcp import InMemorySourceRegistry, ToolContext
from schemashift.subagents.validation.graph import build_validation_graph


class RecordingToolCaller:
    def __init__(self, *, equivalent: bool = True, truncated: bool = False) -> None:
        self.equivalent = equivalent
        self.truncated = truncated
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name == "parse_sql":
            return {
                "ok": True,
                "read_only": True,
                "tables": ["customers"],
                "diagnostics": [],
            }
        if name == "inspect_schema":
            return {"ok": True, "tables": [{"name": "customers"}]}
        if name == "compare_results":
            return {
                "ok": True,
                "equivalent": self.equivalent and not self.truncated,
                "ordered": False,
                "old_execution": {
                    "ok": True,
                    "row_count": 2,
                    "truncated": self.truncated,
                },
                "new_execution": {
                    "ok": True,
                    "row_count": 2,
                    "truncated": self.truncated,
                },
                "schema": {
                    "old_columns": [
                        {"name": "name", "canonical_type": "text"},
                    ],
                },
                "comparison": {"missing_count": 0, "extra_count": 0},
                "duplicates": {"new": {"samples": []}},
                "nulls": {"delta": {}},
                "numeric_aggregates": {},
                "issues": [],
            }
        raise AssertionError(f"Unexpected tool: {name}")


def _context(tmp_path: Path) -> ToolContext:
    return ToolContext(InMemorySourceRegistry((tmp_path,)))


def _input(**extra: Any) -> dict[str, Any]:
    return {
        "original_sql": "SELECT legacy_name AS name FROM old_customers",
        "candidate_sql": "SELECT name FROM customers",
        "old_database_id": "old-db-id",
        "new_database_id": "new-db-id",
        "new_schema_source_id": "new-schema-id",
        "comparison_policy": {"ordered": False},
        **extra,
    }


def test_validation_subgraph_uses_only_three_restricted_tool_calls(tmp_path: Path) -> None:
    caller = RecordingToolCaller()
    graph = build_validation_graph(_context(tmp_path), tool_caller=caller)

    result = graph.invoke(_input(parent_history=["must not be observed"]))

    assert result["report"]["verdict"] == "pass"
    assert [name for name, _ in caller.calls] == [
        "parse_sql",
        "inspect_schema",
        "compare_results",
    ]
    assert caller.calls[0][1] == {"sql": "SELECT name FROM customers"}
    assert caller.calls[1][1] == {"source_id": "new-schema-id"}
    assert caller.calls[2][1] == {
        "old_database_id": "old-db-id",
        "old_sql": "SELECT legacy_name AS name FROM old_customers",
        "new_database_id": "new-db-id",
        "new_sql": "SELECT name FROM customers",
        "policy": {"ordered": False},
    }
    assert all("path" not in arguments for _, arguments in caller.calls)


def test_truncated_comparison_requires_human_review(tmp_path: Path) -> None:
    caller = RecordingToolCaller(truncated=True)

    result = build_validation_graph(_context(tmp_path), tool_caller=caller).invoke(_input())

    report = result["report"]
    assert report["verdict"] == "human_review"
    assert report["execution_comparison"]["truncated"] is True


def test_schema_or_execution_failure_requests_revision(tmp_path: Path) -> None:
    caller = RecordingToolCaller(equivalent=False)

    result = build_validation_graph(_context(tmp_path), tool_caller=caller).invoke(_input())

    assert result["report"]["verdict"] == "revise"


def test_tool_transport_failure_is_returned_as_a_validation_observation(tmp_path: Path) -> None:
    context = _context(tmp_path)

    def failing_tool(_name: str, _arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        raise RuntimeError("transport unavailable")

    result = build_validation_graph(context, tool_caller=failing_tool).invoke(
        {
            "original_sql": "SELECT legacy_id FROM legacy_customer",
            "candidate_sql": "SELECT customer_id FROM customer",
            "old_database_id": "old",
            "new_database_id": "new",
            "new_schema_source_id": "schema",
            "comparison_policy": {},
        }
    )

    assert result["report"]["verdict"] == "revise"
    assert any(issue["code"] == "tool_failure" for issue in result["report"]["issues"])


def test_schema_failure_is_actionable_even_when_other_tools_succeed(tmp_path: Path) -> None:
    caller = RecordingToolCaller()
    original_call = caller.__call__

    def schema_failure(name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if name == "inspect_schema":
            return {
                "ok": False,
                "tables": [],
                "error": {"code": "source_missing", "message": "Schema source unavailable"},
            }
        return original_call(name, arguments)

    graph = build_validation_graph(_context(tmp_path), tool_caller=schema_failure)
    report = graph.invoke(_input())["report"]

    assert report["verdict"] == "revise"
    assert "Schema source unavailable" in report["structural_checks"]["diagnostics"]
    assert any(issue["message"] == "Schema source unavailable" for issue in report["issues"])


def test_row_difference_samples_are_typed_and_bounded(tmp_path: Path) -> None:
    caller = RecordingToolCaller(equivalent=False)
    original_call = caller.__call__

    def mismatching(name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        result = dict(original_call(name, arguments))
        if name == "compare_results":
            result["comparison"] = {
                "missing_count": 30,
                "extra_count": 30,
                "missing_rows": [[value] for value in range(30)],
                "extra_rows": [[value] for value in range(30, 60)],
            }
            result["issues"] = [{"code": "row_mismatch", "message": "Rows differ"}]
        return result

    report = build_validation_graph(_context(tmp_path), tool_caller=mismatching).invoke(_input())[
        "report"
    ]
    samples = report["execution_comparison"]["mismatch_samples"]

    assert report["verdict"] == "revise"
    assert len(samples) == 20
    assert samples[0] == {"kind": "missing", "row": [0]}
