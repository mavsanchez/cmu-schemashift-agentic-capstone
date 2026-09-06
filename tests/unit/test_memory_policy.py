from __future__ import annotations

from schemashift.memory import (
    MemoryCandidate,
    MemoryProvenance,
    MemorySourceType,
    MemoryType,
    MemoryWritePolicy,
)


def candidate(
    *,
    source: MemorySourceType = MemorySourceType.HUMAN_APPROVAL,
    memory_type: MemoryType = MemoryType.MIGRATION_DECISION,
    confidence: float = 1.0,
    approved: bool = False,
    text: str = "Use status_lookup.status_description for customer_status in schema v2.",
) -> MemoryCandidate:
    return MemoryCandidate(
        text=text,
        memory_type=memory_type,
        confidence=confidence,
        provenance=MemoryProvenance(
            source_type=source,
            source_id="run-123",
            run_id="run-123",
            human_approved=approved,
        ),
    )


def test_human_approved_migration_decision_is_durable() -> None:
    decision = MemoryWritePolicy().evaluate(candidate())

    assert decision.allowed
    assert not decision.requires_human_review


def test_tool_output_is_not_automatically_memory() -> None:
    decision = MemoryWritePolicy().evaluate(candidate(source=MemorySourceType.TOOL_OUTPUT))

    assert not decision.allowed
    assert "tool output" in decision.reason


def test_explicit_approval_can_promote_tool_evidence() -> None:
    decision = MemoryWritePolicy().evaluate(
        candidate(source=MemorySourceType.TOOL_OUTPUT, approved=True)
    )

    assert decision.allowed


def test_low_confidence_fact_routes_to_review() -> None:
    decision = MemoryWritePolicy(min_confidence=0.8).evaluate(candidate(confidence=0.6))

    assert not decision.allowed
    assert decision.requires_human_review


def test_unapproved_agent_reflection_routes_to_review() -> None:
    decision = MemoryWritePolicy().evaluate(candidate(source=MemorySourceType.AGENT_REFLECTION))

    assert not decision.allowed
    assert decision.requires_human_review
