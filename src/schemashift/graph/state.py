"""Declared parent-graph state."""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class SchemaShiftState(TypedDict, total=False):
    # Durable conversational identity.
    conversation_id: str
    session_id: str
    run_id: str
    turn_id: str

    # Fresh input and source selections.
    request: str
    original_sql: str
    old_schema_source_id: str
    new_schema_source_id: str
    old_database_id: str
    new_database_id: str
    comparison_policy: dict[str, Any]
    knowledge_document_ids: list[str]

    # Context assembled by the parent.
    history: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    memories: list[dict[str, Any]]
    parsed_sql: dict[str, Any]
    old_schema: dict[str, Any]
    new_schema: dict[str, Any]
    impact: dict[str, Any]
    evidence: list[dict[str, Any]]
    redis_degraded: bool
    tool_observations: list[dict[str, Any]]
    preflight_failed: bool

    # Specialist output and loop state.
    proposal: dict[str, Any]
    validation: dict[str, Any]
    guardrail_passed: bool
    guardrail_diagnostics: list[dict[str, Any]]
    validation_feedback: list[dict[str, Any]]
    reviewer_feedback: str
    revision_count: int
    max_revisions: int
    confidence_threshold: float
    model_call_count: int
    tool_call_count: int
    migration_invocation_count: int
    validation_invocation_count: int
    tot_evaluation_count: int

    # Human gate and finalization.
    needs_human_review: bool
    review_reason: str
    review: dict[str, Any]
    human_decision: dict[str, Any]
    human_approved: bool
    status: str
    artifact_path: str
    answer: str
    memory_candidate: dict[str, Any]
    error: str


FRESH_RUN_DEFAULTS: dict[str, Any] = {
    "history": [],
    "sources": [],
    "memories": [],
    "parsed_sql": {},
    "old_schema": {},
    "new_schema": {},
    "impact": {},
    "evidence": [],
    "redis_degraded": False,
    "tool_observations": [],
    "preflight_failed": False,
    "proposal": {},
    "validation": {},
    "guardrail_passed": True,
    "guardrail_diagnostics": [],
    "validation_feedback": [],
    "reviewer_feedback": "",
    "revision_count": 0,
    "confidence_threshold": 0.80,
    "model_call_count": 0,
    "tool_call_count": 0,
    "migration_invocation_count": 0,
    "validation_invocation_count": 0,
    "tot_evaluation_count": 0,
    "needs_human_review": False,
    "review_reason": "",
    "review": {},
    "human_decision": {},
    "human_approved": False,
    "status": "running",
    "artifact_path": "",
    "answer": "",
    "memory_candidate": {},
    "error": "",
}
