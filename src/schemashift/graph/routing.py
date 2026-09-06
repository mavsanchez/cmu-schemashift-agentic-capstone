"""Pure route functions for the parent graph."""

from __future__ import annotations

from schemashift.graph.state import SchemaShiftState


def after_validation(state: SchemaShiftState) -> str:
    report = state.get("validation") or {}
    verdict = str(report.get("verdict", "revise"))
    proposal = state.get("proposal") or {}
    confidence = float(proposal.get("overall_confidence", proposal.get("confidence", 0.0)) or 0.0)
    evidence_conflict = bool(proposal.get("evidence_conflict", False))
    ambiguity_requires_review = bool(
        proposal.get("requires_human_review")
        or proposal.get("ambiguity_flags")
        or (state.get("impact") or {}).get("requires_human_review")
    )

    threshold = float(state.get("confidence_threshold", 0.80))
    if not bool(state.get("guardrail_passed", True)):
        if state.get("revision_count", 0) < state.get("max_revisions", 10):
            return "revise"
        return "human_review"
    if (
        verdict == "pass"
        and confidence >= threshold
        and not evidence_conflict
        and not ambiguity_requires_review
    ):
        return "answer"
    if verdict == "revise" and state.get("revision_count", 0) < state.get("max_revisions", 10):
        return "revise"
    return "human_review"


def after_human_review(state: SchemaShiftState) -> str:
    decision = state.get("human_decision") or {}
    if decision.get("decision") == "approve":
        return "answer"
    feedback = str(decision.get("reviewer_feedback") or decision.get("feedback") or "").strip()
    if feedback and state.get("revision_count", 0) < state.get("max_revisions", 10):
        return "revise"
    return "unresolved"
