"""Isolated Migration Subagent StateGraph with selective bounded ToT."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from schemashift.domain import MigrationProposal
from schemashift.model import ModelProvider
from schemashift.subagents.migration.prompts import (
    build_branch_messages,
    build_migration_messages,
)
from schemashift.subagents.migration.schemas import BranchExpansion

CandidateEvaluator = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class MigrationState(TypedDict, total=False):
    conversation_id: str
    session_id: str
    run_id: str
    request: str
    original_sql: str
    old_schema: dict[str, Any]
    new_schema: dict[str, Any]
    evidence: list[dict[str, Any]]
    validation_feedback: list[dict[str, Any]]
    reviewer_feedback: str
    old_database_id: str
    new_database_id: str
    comparison_policy: dict[str, Any]
    proposal: dict[str, Any]
    branches: list[dict[str, Any]]
    tot_depth: int
    tot_used: bool
    tot_evaluation_count: int


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value)


def _candidate_score(candidate: Mapping[str, Any]) -> tuple[int, int, int, int, int, int]:
    evaluation = candidate.get("evaluation")
    evaluation = evaluation if isinstance(evaluation, Mapping) else {}
    sql = str(candidate.get("candidate_sql", ""))
    return (
        int(bool(evaluation.get("guardrail_ok", False))),
        int(bool(evaluation.get("schema_valid", False))),
        int(bool(evaluation.get("execution_ok", False))),
        int(bool(evaluation.get("equivalent", False))),
        int(evaluation.get("evidence_support", len(candidate.get("evidence_ids", []))) or 0),
        -int(evaluation.get("complexity", len(sql)) or len(sql)),
    )


def _cited_evidence_support(candidate: Mapping[str, Any], state: MigrationState) -> int:
    """Count candidate citations that resolve to evidence in the bounded briefing."""

    available: set[str] = set()
    for item in state.get("evidence") or []:
        if not isinstance(item, Mapping):
            continue
        nested = [item]
        nested.extend(
            value
            for value in (item.get("chunk"), item.get("record"))
            if isinstance(value, Mapping)
        )
        for record in nested:
            for key in (
                "evidence_id",
                "chunk_id",
                "document_id",
                "memory_id",
                "id",
                "source_id",
            ):
                value = record.get(key)
                if value:
                    available.add(str(value))
    cited = {str(value) for value in candidate.get("evidence_ids", []) if value}
    return len(cited & available)


def _needs_tot(state: MigrationState, confidence_threshold: float, ambiguity_margin: float) -> str:
    proposal = state.get("proposal") or {}
    alternatives = list(proposal.get("alternatives") or [])
    confidence = float(proposal.get("overall_confidence", 0.0) or 0.0)
    confidences = [confidence]
    confidences.extend(
        float(alternative.get("confidence", 0.0) or 0.0)
        for alternative in alternatives
        if isinstance(alternative, Mapping)
    )
    confidences.sort(reverse=True)
    narrow_margin = len(confidences) > 1 and confidences[0] - confidences[1] < ambiguity_margin
    ambiguous = len(alternatives) > 0 and (
        confidence < confidence_threshold
        or narrow_margin
        or bool(proposal.get("evidence_conflict"))
        or bool(proposal.get("requires_human_review"))
    )
    return "explore" if ambiguous else "finish"


def build_migration_graph(
    provider: ModelProvider,
    *,
    evaluator: CandidateEvaluator | None = None,
    confidence_threshold: float = 0.80,
    ambiguity_margin: float = 0.10,
    branching_factor: int = 3,
    beam_width: int = 2,
    max_depth: int = 3,
):
    """Compile an isolated specialist; it receives no parent history/checkpointer."""

    branching_factor = min(max(branching_factor, 1), 3)
    beam_width = min(max(beam_width, 1), 2)
    max_depth = min(max(max_depth, 1), 3)

    def propose(state: MigrationState) -> dict[str, Any]:
        result = provider.structured(build_migration_messages(state), MigrationProposal)
        proposal = _dump(result)
        return {
            "proposal": proposal,
            "tot_depth": 0,
            "tot_used": False,
            "tot_evaluation_count": 0,
            "branches": [],
        }

    def explore(state: MigrationState) -> dict[str, Any]:
        depth = int(state.get("tot_depth", 0)) + 1
        existing = list(state.get("branches") or [])
        if not existing:
            proposal = state.get("proposal") or {}
            existing.append(
                {
                    "candidate_id": proposal.get("candidate_id"),
                    "candidate_sql": proposal.get("candidate_sql", ""),
                    "mappings": proposal.get("mappings", []),
                    "rationale": proposal.get("rationale", ""),
                    "evidence_ids": proposal.get("evidence_ids", []),
                    "confidence": proposal.get("overall_confidence", 0.0),
                    "requires_human_review": proposal.get("requires_human_review", False),
                    "evidence_conflict": proposal.get("evidence_conflict", False),
                    "ambiguity_flags": proposal.get("ambiguity_flags", []),
                }
            )
            for alternative in list(proposal.get("alternatives") or [])[: branching_factor - 1]:
                if isinstance(alternative, Mapping):
                    item = dict(alternative)
                    if "candidate_sql" not in item and "sql" in item:
                        item["candidate_sql"] = item["sql"]
                    existing.append(item)

        expansion = provider.structured(
            build_branch_messages(state, existing[:beam_width], depth), BranchExpansion
        )
        expanded = [_dump(item) for item in expansion.branches]
        candidates: dict[str, dict[str, Any]] = {}
        # One round explores at most ``branching_factor`` candidates. Existing
        # beam survivors condition the next prompt; they are not re-evaluated
        # alongside the newly expanded branches.
        round_candidates = expanded[:branching_factor] or existing[:branching_factor]
        for candidate in round_candidates:
            sql = str(candidate.get("candidate_sql", "")).strip()
            if sql:
                candidates.setdefault(sql, dict(candidate))

        for sql, candidate in candidates.items():
            assessment = dict(evaluator(sql, state)) if evaluator is not None else {}
            assessment.setdefault("evidence_support", _cited_evidence_support(candidate, state))
            assessment.setdefault("complexity", len(sql))
            candidate["evaluation"] = assessment

        ranked = sorted(candidates.values(), key=_candidate_score, reverse=True)[:beam_width]
        return {
            "branches": ranked,
            "tot_depth": depth,
            "tot_used": True,
            "tot_evaluation_count": int(state.get("tot_evaluation_count", 0)) + len(candidates),
        }

    def after_explore(state: MigrationState) -> str:
        branches = state.get("branches") or []
        best = _candidate_score(branches[0]) if branches else (0, 0, 0, 0, 0, 0)
        if best[:4] == (1, 1, 1, 1) or int(state.get("tot_depth", 0)) >= max_depth:
            return "select"
        return "explore"

    def select(state: MigrationState) -> dict[str, Any]:
        branches = state.get("branches") or []
        if not branches:
            return {}
        proposal = dict(state.get("proposal") or {})
        best = branches[0]
        proposal.update(
            {
                "candidate_id": best.get("candidate_id", proposal.get("candidate_id")),
                "candidate_sql": best["candidate_sql"],
                "mappings": best.get("mappings", []),
                "rationale": best.get("rationale") or proposal.get("rationale", ""),
                "evidence_ids": best.get("evidence_ids") or proposal.get("evidence_ids", []),
                "overall_confidence": max(
                    float(proposal.get("overall_confidence", 0.0) or 0.0),
                    float(best.get("confidence", 0.0) or 0.0),
                ),
                "requires_human_review": bool(best.get("requires_human_review", False)),
                "evidence_conflict": bool(
                    proposal.get("evidence_conflict") or best.get("evidence_conflict", False)
                ),
                "ambiguity_flags": list(best.get("ambiguity_flags", [])),
                "alternatives": [
                    {
                        "candidate_id": branch.get("candidate_id"),
                        "candidate_sql": branch["candidate_sql"],
                        "rationale": branch.get("rationale", ""),
                        "confidence": branch.get("confidence", 0.5),
                    }
                    for branch in branches[1:]
                ],
            }
        )
        validated = MigrationProposal.model_validate(proposal)
        return {"proposal": validated.model_dump(mode="json")}

    graph = StateGraph(MigrationState)
    graph.add_node("propose", propose)
    graph.add_node("explore", explore)
    graph.add_node("select", select)
    graph.add_node("finish", lambda _state: {})
    graph.add_edge(START, "propose")
    graph.add_conditional_edges(
        "propose",
        lambda state: _needs_tot(state, confidence_threshold, ambiguity_margin),
        {"explore": "explore", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "explore",
        after_explore,
        {"explore": "explore", "select": "select"},
    )
    graph.add_edge("select", END)
    graph.add_edge("finish", END)
    return graph.compile()
