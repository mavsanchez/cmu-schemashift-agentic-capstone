from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from schemashift.agents import OrchestratorRuntime
from schemashift.config import Settings
from schemashift.graph import build_main_graph
from schemashift.mcp import InMemorySourceRegistry, ToolContext
from schemashift.memory import MemoryWriteResult
from schemashift.model import MockProvider


def _impact() -> dict[str, Any]:
    return {
        "impacted_tables": ["legacy_customers"],
        "impacted_columns": ["legacy_name"],
        "expression_changes": [],
        "summary": "Customer table and name changed.",
        "requires_human_review": False,
    }


def _proposal(
    sql: str,
    *,
    confidence: float = 0.95,
    conflict: bool = False,
) -> dict[str, Any]:
    return {
        "candidate_id": str(uuid4()),
        "candidate_sql": sql,
        "mappings": [
            {
                "old_reference": "legacy_customers.legacy_name",
                "new_expression": "customers.name",
                "change_type": "rename",
                "rationale": "documented rename",
                "confidence": confidence,
                "evidence_ids": ["chunk-1"],
            }
        ],
        "rationale": "Use the documented table and column rename.",
        "evidence_ids": ["chunk-1"],
        "alternatives": [],
        "overall_confidence": confidence,
        "requires_human_review": conflict,
        "evidence_conflict": conflict,
        "ambiguity_flags": ["conflicting evidence"] if conflict else [],
    }


class WorkflowTools:
    """Deterministic MCP-shaped fake; all selectors remain opaque IDs."""

    def __init__(self, *, unsafe_candidates: bool = False) -> None:
        self.unsafe_candidates = unsafe_candidates
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if name == "parse_sql":
            sql = str(arguments["sql"])
            candidate = " from customers" in sql.casefold()
            read_only = not (self.unsafe_candidates and candidate)
            return {
                "ok": read_only,
                "read_only": read_only,
                "tables": ["customers" if candidate else "legacy_customers"],
                "diagnostics": []
                if read_only
                else [{"code": "unsafe_sql", "message": "not read only"}],
                "complexity": {"node_count": len(sql.split())},
            }
        if name == "inspect_schema":
            table = "legacy_customers" if arguments["source_id"] == "old-schema" else "customers"
            return {"ok": True, "tables": [{"name": table}]}
        if name == "compare_results":
            sql = str(arguments["new_sql"])
            equivalent = "bad_" not in sql and not self.unsafe_candidates
            issue = [] if equivalent else [{"code": "row_mismatch", "message": "rows differ"}]
            return {
                "ok": True,
                "equivalent": equivalent,
                "ordered": False,
                "old_execution": {"ok": True, "row_count": 2, "truncated": False},
                "new_execution": {"ok": True, "row_count": 2, "truncated": False},
                "schema": {
                    "old_columns": [{"name": "name", "canonical_type": "text"}],
                },
                "comparison": {
                    "missing_count": 0 if equivalent else 1,
                    "extra_count": 0 if equivalent else 1,
                    "missing_rows": [],
                    "extra_rows": [],
                },
                "duplicates": {"new": {"samples": []}},
                "nulls": {"delta": {}},
                "numeric_aggregates": {},
                "issues": issue,
            }
        raise AssertionError(f"Unexpected tool: {name}")


class TransientGuardrailFailureTools(WorkflowTools):
    """Fail only the first candidate guardrail parse, not independent validation."""

    def __init__(self) -> None:
        super().__init__()
        self.candidate_parses = 0

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "parse_sql" and " from customers" in str(arguments["sql"]).casefold():
            self.candidate_parses += 1
            if self.candidate_parses == 1:
                self.calls.append((name, dict(arguments)))
                return {
                    "ok": False,
                    "read_only": False,
                    "tables": [],
                    "diagnostics": [
                        {"code": "transient_guardrail", "message": "guardrail parse failed"}
                    ],
                }
        return super().call_tool(name, arguments)


class CapturingMemoryStore:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    def recall(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    def try_remember(self, text: str, **kwargs: Any) -> MemoryWriteResult:
        self.writes.append({"text": text, **kwargs})
        return MemoryWriteResult(stored=True, reason="stored")


def _runtime(
    tmp_path: Path,
    provider: MockProvider,
    tools: WorkflowTools,
    *,
    max_revisions: int = 10,
    memory_store: Any | None = None,
):
    settings = Settings(
        model_provider="mock",
        max_migration_revisions=max_revisions,
        data_root=tmp_path,
        upload_root=tmp_path / "uploads",
        artifact_root=tmp_path / "artifacts",
    )
    context = ToolContext(InMemorySourceRegistry((tmp_path,)))

    def write_artifact(payload: Mapping[str, Any]) -> str:
        path = tmp_path / "artifacts" / str(payload["conversation_id"]) / str(payload["run_id"])
        path.mkdir(parents=True, exist_ok=True)
        target = path / "migration.sql"
        target.write_text(str(payload["candidate_sql"]), encoding="utf-8")
        return str(target)

    return OrchestratorRuntime(
        settings=settings,
        provider=provider,
        tool_context=context,
        tool_client=tools,
        memory_store=memory_store,
        artifact_writer=write_artifact,
    )


def _input(*, session_id: str | None = None) -> dict[str, Any]:
    run_id = str(uuid4())
    return {
        "conversation_id": str(uuid4()),
        "session_id": session_id or str(uuid4()),
        "run_id": run_id,
        "turn_id": run_id,
        "request": "Migrate the customer query.",
        "original_sql": "SELECT legacy_name AS name FROM legacy_customers",
        "old_schema_source_id": "old-schema",
        "new_schema_source_id": "new-schema",
        "old_database_id": "old-database",
        "new_database_id": "new-database",
        "comparison_policy": {},
    }


def _interrupt_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    interrupts = result.get("__interrupt__") or []
    assert len(interrupts) == 1
    interrupt_value = getattr(interrupts[0], "value", interrupts[0])
    return dict(interrupt_value)


def test_parent_graph_happy_path_runs_each_isolated_specialist_once(tmp_path: Path) -> None:
    provider = MockProvider(
        structured_responses=[_impact(), _proposal("SELECT name FROM customers")],
        chat_responses=["Validated migration."],
    )
    tools = WorkflowTools()
    result = build_main_graph(_runtime(tmp_path, provider, tools)).invoke(_input())

    assert result["status"] == "completed"
    assert result["answer"] == "Validated migration."
    assert result["revision_count"] == 0
    assert result["migration_invocation_count"] == 1
    assert result["validation_invocation_count"] == 1
    assert Path(result["artifact_path"]).is_file()
    assert [name for name, _ in tools.calls].count("compare_results") == 1
    migration_payload = json.loads(provider.calls[1].input[-1][1])
    assert migration_payload["request"] == "Migrate the customer query."


def test_failed_validation_is_returned_to_a_fresh_migration_invocation(tmp_path: Path) -> None:
    provider = MockProvider(
        structured_responses=[
            _impact(),
            _proposal("SELECT bad_name AS name FROM customers AS bad_0"),
            _proposal("SELECT name FROM customers"),
        ],
        chat_responses=["Recovered migration."],
    )
    result = build_main_graph(_runtime(tmp_path, provider, WorkflowTools())).invoke(_input())

    assert result["status"] == "completed"
    assert result["revision_count"] == 1
    assert result["migration_invocation_count"] == 2
    assert result["validation_invocation_count"] == 2
    second_migration_call = provider.calls[2]
    human_payload = second_migration_call.input[-1][1]
    assert "row_mismatch" in human_payload


def test_guardrail_failure_cannot_be_overridden_by_later_validation_success(
    tmp_path: Path,
) -> None:
    provider = MockProvider(
        structured_responses=[
            _impact(),
            _proposal("SELECT name FROM customers"),
            _proposal("SELECT name FROM customers"),
        ],
        chat_responses=["Recovered after guardrail recheck."],
    )

    result = build_main_graph(
        _runtime(tmp_path, provider, TransientGuardrailFailureTools())
    ).invoke(_input())

    assert result["status"] == "completed"
    assert result["revision_count"] == 1
    assert result["guardrail_passed"] is True
    assert result["migration_invocation_count"] == 2


def test_eleven_candidate_attempts_exhaust_the_revision_budget(tmp_path: Path) -> None:
    provider = MockProvider(
        structured_responses=[
            _impact(),
            *[_proposal(f"SELECT name FROM customers AS bad_{index}") for index in range(11)],
        ]
    )
    graph = build_main_graph(
        _runtime(tmp_path, provider, WorkflowTools()),
        checkpointer=InMemorySaver(),
    )
    graph_input = _input()
    config = {"configurable": {"thread_id": graph_input["conversation_id"]}}

    interrupted = graph.invoke(graph_input, config=config)
    review = _interrupt_payload(interrupted)

    assert interrupted["revision_count"] == 10
    assert interrupted["migration_invocation_count"] == 11
    assert interrupted["validation_invocation_count"] == 11
    assert review["reason"] == "The bounded migration revision budget was exhausted."


def test_approval_resumes_same_run_from_a_later_session(tmp_path: Path) -> None:
    provider = MockProvider(
        structured_responses=[
            _impact(),
            _proposal("SELECT name FROM customers", confidence=0.75),
        ],
        chat_responses=["Human-approved migration."],
    )
    graph = build_main_graph(
        _runtime(tmp_path, provider, WorkflowTools()),
        checkpointer=InMemorySaver(),
    )
    graph_input = _input()
    config = {"configurable": {"thread_id": graph_input["conversation_id"]}}
    interrupted = graph.invoke(graph_input, config=config)
    review = _interrupt_payload(interrupted)
    later_session = str(uuid4())

    result = graph.invoke(
        Command(
            resume={
                "decision": "approve",
                "review_id": review["review_id"],
                "run_id": graph_input["run_id"],
                "session_id": later_session,
            },
            update={"session_id": later_session},
        ),
        config=config,
    )

    assert result["run_id"] == graph_input["run_id"]
    assert result["turn_id"] == graph_input["run_id"]
    assert result["session_id"] == later_session
    assert result["human_approved"] is True
    assert result["status"] == "human_approved"
    assert Path(result["artifact_path"]).is_file()


def test_human_approved_mappings_use_stable_supersession_keys(tmp_path: Path) -> None:
    provider = MockProvider(
        structured_responses=[
            _impact(),
            _proposal("SELECT name FROM customers", confidence=0.75),
        ],
        chat_responses=["Human-approved migration."],
    )
    memory = CapturingMemoryStore()
    graph = build_main_graph(
        _runtime(
            tmp_path,
            provider,
            WorkflowTools(),
            memory_store=memory,
        ),
        checkpointer=InMemorySaver(),
    )
    graph_input = _input()
    config = {"configurable": {"thread_id": graph_input["conversation_id"]}}
    review = _interrupt_payload(graph.invoke(graph_input, config=config))

    graph.invoke(
        Command(
            resume={
                "decision": "approve",
                "review_id": review["review_id"],
                "run_id": graph_input["run_id"],
                "session_id": graph_input["session_id"],
            }
        ),
        config=config,
    )

    assert len(memory.writes) == 1
    write = memory.writes[0]
    assert write["text"] == (
        "Approved migration mapping: legacy_customers.legacy_name -> customers.name"
    )
    assert write["metadata"]["supersession_key"] == (
        "migration_mapping:legacy_customers.legacy_name"
    )
    assert write["provenance"]["human_approved"] is True


def test_explicit_user_fact_uses_a_stable_subject_supersession_key(tmp_path: Path) -> None:
    provider = MockProvider(
        structured_responses=[_impact(), _proposal("SELECT name FROM customers")],
        chat_responses=["Preference remembered."],
    )
    memory = CapturingMemoryStore()
    graph_input = _input()
    graph_input["request"] = "Remember: preferred currency is USD"

    result = build_main_graph(
        _runtime(tmp_path, provider, WorkflowTools(), memory_store=memory)
    ).invoke(graph_input)

    assert result["status"] == "completed"
    assert len(memory.writes) == 1
    assert memory.writes[0]["metadata"] == {
        "supersession_key": "user_fact:preferred currency",
        "fact_subject": "preferred currency",
    }


def test_reject_without_feedback_completes_unresolved_without_artifact(tmp_path: Path) -> None:
    provider = MockProvider(
        structured_responses=[
            _impact(),
            _proposal("SELECT name FROM customers", confidence=0.75),
        ]
    )
    graph = build_main_graph(
        _runtime(tmp_path, provider, WorkflowTools()),
        checkpointer=InMemorySaver(),
    )
    graph_input = _input()
    config = {"configurable": {"thread_id": graph_input["conversation_id"]}}
    review = _interrupt_payload(graph.invoke(graph_input, config=config))

    result = graph.invoke(
        Command(
            resume={
                "decision": "reject",
                "review_id": review["review_id"],
                "run_id": graph_input["run_id"],
                "session_id": graph_input["session_id"],
            }
        ),
        config=config,
    )

    assert result["status"] == "unresolved"
    assert result["artifact_path"] == ""
    assert "No migrated file was written" in result["answer"]


def test_reject_with_actionable_feedback_revises_when_budget_remains(tmp_path: Path) -> None:
    provider = MockProvider(
        structured_responses=[
            _impact(),
            _proposal("SELECT name FROM customers", confidence=0.75),
            _proposal("SELECT name FROM customers", confidence=0.95),
        ],
        chat_responses=["Reviewed and corrected."],
    )
    graph = build_main_graph(
        _runtime(tmp_path, provider, WorkflowTools()),
        checkpointer=InMemorySaver(),
    )
    graph_input = _input()
    config = {"configurable": {"thread_id": graph_input["conversation_id"]}}
    review = _interrupt_payload(graph.invoke(graph_input, config=config))

    result = graph.invoke(
        Command(
            resume={
                "decision": "reject",
                "review_id": review["review_id"],
                "run_id": graph_input["run_id"],
                "session_id": graph_input["session_id"],
                "reviewer_feedback": "Use the documented customer name mapping.",
            }
        ),
        config=config,
    )

    assert result["status"] == "completed"
    assert result["revision_count"] == 1
    assert result["migration_invocation_count"] == 2
    assert result["reviewer_feedback"] == "Use the documented customer name mapping."


def test_guardrail_failure_cannot_be_human_overridden(tmp_path: Path) -> None:
    provider = MockProvider(structured_responses=[_impact(), _proposal("DELETE FROM customers")])
    graph = build_main_graph(
        _runtime(tmp_path, provider, WorkflowTools(unsafe_candidates=True), max_revisions=0),
        checkpointer=InMemorySaver(),
    )
    graph_input = _input()
    config = {"configurable": {"thread_id": graph_input["conversation_id"]}}
    review = _interrupt_payload(graph.invoke(graph_input, config=config))
    assert review["payload"]["approvable"] is False

    result = graph.invoke(
        Command(
            resume={
                "decision": "approve",
                "review_id": review["review_id"],
                "run_id": graph_input["run_id"],
                "session_id": graph_input["session_id"],
            }
        ),
        config=config,
    )

    assert result["status"] == "unresolved"
    assert result["human_approved"] is False
    assert result["artifact_path"] == ""


def test_streamed_meaningful_events_all_carry_run_correlation(tmp_path: Path) -> None:
    provider = MockProvider(
        structured_responses=[_impact(), _proposal("SELECT name FROM customers")],
        chat_responses=["Validated migration."],
    )
    graph_input = _input()
    chunks = list(
        build_main_graph(_runtime(tmp_path, provider, WorkflowTools())).stream(
            graph_input,
            stream_mode="custom",
        )
    )

    meaningful = [
        item
        for item in chunks
        if item.get("type") in {"component_status", "activity", "human_review_required"}
    ]
    assert meaningful
    for event in meaningful:
        assert event["conversation_id"] == graph_input["conversation_id"]
        assert event["session_id"] == graph_input["session_id"]
        assert event["run_id"] == graph_input["run_id"]
