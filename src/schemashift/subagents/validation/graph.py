"""Independent, tool-heavy Validation Subagent StateGraph."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from schemashift.mcp.context import ToolContext
from schemashift.mcp.tools import compare_results, inspect_schema, parse_sql
from schemashift.subagents.validation.schemas import build_validation_report


class ValidationState(TypedDict, total=False):
    original_sql: str
    candidate_sql: str
    old_database_id: str
    new_database_id: str
    new_schema_source_id: str
    comparison_policy: dict[str, Any]
    parsed_candidate: dict[str, Any]
    target_schema: dict[str, Any]
    comparison: dict[str, Any]
    report: dict[str, Any]


ToolCaller = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


def build_validation_graph(
    tool_context: ToolContext,
    *,
    tool_caller: ToolCaller | None = None,
):
    """Compile a stateless specialist that independently rechecks a candidate."""

    def call(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        try:
            if tool_caller is not None:
                result = dict(tool_caller(name, arguments))
            else:
                direct = {
                    "parse_sql": lambda values: parse_sql(**values),
                    "inspect_schema": lambda values: inspect_schema(**values, context=tool_context),
                    "compare_results": lambda values: compare_results(
                        **values, context=tool_context
                    ),
                }
                result = dict(direct[name](dict(arguments)))
        except Exception as exc:
            result = {
                "ok": False,
                "error": {
                    "code": "tool_failure",
                    "message": f"{name} failed: {type(exc).__name__}: {exc}",
                },
            }
        if result.get("ok", True):
            return result

        raw_error = result.get("error")
        message = (
            str(raw_error.get("message", raw_error))
            if isinstance(raw_error, Mapping)
            else str(raw_error or f"{name} failed")
        )
        issue = {"code": "tool_failure", "message": message}
        if name == "parse_sql":
            result.setdefault("read_only", False)
            result.setdefault("tables", [])
            result.setdefault("diagnostics", [issue])
        elif name == "inspect_schema":
            result.setdefault("tables", [])
            result.setdefault("issues", [issue])
        elif name == "compare_results":
            result.setdefault("issues", [issue])
            result.setdefault("old_execution", {"ok": False})
            result.setdefault("new_execution", {"ok": False})
        return result

    def parse_candidate(state: ValidationState) -> dict[str, Any]:
        return {"parsed_candidate": call("parse_sql", {"sql": state.get("candidate_sql", "")})}

    def inspect_target(state: ValidationState) -> dict[str, Any]:
        source_id = state.get("new_schema_source_id", "")
        if not source_id:
            return {
                "target_schema": {
                    "ok": False,
                    "tables": [],
                    "error": "Missing target schema source",
                }
            }
        return {"target_schema": call("inspect_schema", {"source_id": source_id})}

    def compare(state: ValidationState) -> dict[str, Any]:
        result = call(
            "compare_results",
            {
                "old_database_id": state.get("old_database_id", ""),
                "old_sql": state.get("original_sql", ""),
                "new_database_id": state.get("new_database_id", ""),
                "new_sql": state.get("candidate_sql", ""),
                "policy": state.get("comparison_policy") or {},
            },
        )
        return {"comparison": result}

    def report(state: ValidationState) -> dict[str, Any]:
        value = build_validation_report(
            state.get("parsed_candidate") or {},
            state.get("target_schema") or {},
            state.get("comparison") or {},
        )
        return {"report": value.model_dump(mode="json")}

    graph = StateGraph(ValidationState)
    graph.add_node("parse", parse_candidate)
    graph.add_node("inspect_schema", inspect_target)
    graph.add_node("compare_results", compare)
    graph.add_node("verdict", report)
    graph.add_edge(START, "parse")
    graph.add_edge("parse", "inspect_schema")
    graph.add_edge("inspect_schema", "compare_results")
    graph.add_edge("compare_results", "verdict")
    graph.add_edge("verdict", END)
    return graph.compile()
