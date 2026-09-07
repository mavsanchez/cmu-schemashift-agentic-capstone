from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from schemashift.domain import MigrationProposal
from schemashift.model import MockProvider
from schemashift.subagents.migration.graph import build_migration_graph


def _proposal(
    sql: str,
    *,
    confidence: float = 0.95,
    alternatives: list[dict[str, Any]] | None = None,
    conflict: bool = False,
) -> dict[str, Any]:
    return {
        "candidate_id": str(uuid4()),
        "candidate_sql": sql,
        "mappings": [],
        "rationale": "fixture",
        "evidence_ids": ["evidence-1"],
        "alternatives": alternatives or [],
        "overall_confidence": confidence,
        "requires_human_review": conflict,
        "evidence_conflict": conflict,
        "ambiguity_flags": ["conflict"] if conflict else [],
    }


def test_no_op_ambiguity_sentinel_is_normalized_at_contract_boundary() -> None:
    proposal = MigrationProposal.model_validate(
        _proposal("SELECT 1") | {"ambiguity_flags": ["none"]}
    )

    assert proposal.ambiguity_flags == []
    assert proposal.is_ambiguous is False


def _alternative(sql: str, confidence: float) -> dict[str, Any]:
    return {
        "candidate_id": str(uuid4()),
        "candidate_sql": sql,
        "rationale": "alternative",
        "confidence": confidence,
    }


def _state(**extra: Any) -> dict[str, Any]:
    return {
        "request": "Preserve the original output contract.",
        "original_sql": "SELECT legacy_name FROM old_customers",
        "old_schema": {"tables": [{"name": "old_customers"}]},
        "new_schema": {"tables": [{"name": "customers"}]},
        "impact": {"impacted_tables": ["old_customers"]},
        "evidence": [{"chunk_id": "evidence-1", "text": "renamed to customers"}],
        "validation_feedback": [],
        "reviewer_feedback": "",
        **extra,
    }


def test_unambiguous_proposal_skips_tree_search() -> None:
    provider = MockProvider(structured_responses=[_proposal("SELECT name FROM customers")])
    graph = build_migration_graph(provider)

    result = graph.invoke(_state())

    assert result["proposal"]["candidate_sql"] == "SELECT name FROM customers"
    assert result["tot_used"] is False
    assert result["tot_depth"] == 0
    assert [call.kind for call in provider.calls] == ["structured"]


def test_selective_tree_search_ranks_deterministic_checks_lexicographically() -> None:
    primary = "SELECT display_name FROM customers"
    alternative = "SELECT name FROM customers"
    winning = "SELECT full_name AS legacy_name FROM customers"
    provider = MockProvider(
        structured_responses=[
            _proposal(
                primary,
                confidence=0.72,
                alternatives=[_alternative(alternative, 0.70)],
            ),
            {
                "branches": [
                    {
                        "candidate_sql": winning,
                        "rationale": "preserves the output alias",
                        "evidence_ids": ["evidence-1"],
                        "confidence": 0.74,
                    }
                ]
            },
        ]
    )

    def evaluate(sql: str, _briefing: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "guardrail_ok": True,
            "schema_valid": True,
            "execution_ok": True,
            "equivalent": sql == winning,
            "evidence_support": 1,
            "complexity": len(sql),
        }

    result = build_migration_graph(provider, evaluator=evaluate).invoke(_state())

    assert result["tot_used"] is True
    assert result["tot_depth"] == 1
    assert result["proposal"]["candidate_sql"] == winning
    assert len(result["branches"]) <= 2


def test_tree_search_prefers_citations_that_resolve_to_briefing_evidence() -> None:
    cited = "SELECT full_name AS legacy_name FROM customers"
    uncited = "SELECT name FROM customers"
    provider = MockProvider(
        structured_responses=[
            _proposal(
                "SELECT display_name FROM customers",
                confidence=0.70,
                alternatives=[_alternative(uncited, 0.69)],
            ),
            {
                "branches": [
                    {
                        "candidate_sql": uncited,
                        "rationale": "short but unsupported",
                        "evidence_ids": ["missing-evidence"],
                        "confidence": 0.72,
                    },
                    {
                        "candidate_sql": cited,
                        "rationale": "supported by the migration note",
                        "evidence_ids": ["evidence-1"],
                        "confidence": 0.71,
                    },
                ]
            },
        ]
    )

    def equal_checks(_sql: str, _briefing: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "guardrail_ok": True,
            "schema_valid": True,
            "execution_ok": True,
            "equivalent": False,
        }

    result = build_migration_graph(provider, evaluator=equal_checks, max_depth=1).invoke(
        _state(
            evidence=[
                {
                    "chunk": {
                        "chunk_id": "chunk-1",
                        "document_id": "evidence-1",
                        "text": "renamed to customers",
                    },
                    "score": 0.99,
                }
            ]
        )
    )

    assert result["proposal"]["candidate_sql"] == cited
    assert result["branches"][0]["evaluation"]["evidence_support"] == 1
    assert result["branches"][1]["evaluation"]["evidence_support"] == 0


def test_tree_search_is_bounded_to_three_rounds_two_survivors_and_three_evaluations_per_round() -> (
    None
):
    evaluated: list[str] = []
    provider = MockProvider(
        structured_responses=[
            _proposal(
                "SELECT a FROM customers",
                confidence=0.60,
                alternatives=[
                    _alternative("SELECT b FROM customers", 0.59),
                    _alternative("SELECT c FROM customers", 0.58),
                ],
            ),
            *[
                {
                    "branches": [
                        {
                            "candidate_sql": f"SELECT round_{depth}_{branch} FROM customers",
                            "rationale": "bounded branch",
                            "evidence_ids": [],
                            "confidence": 0.50,
                        }
                        for branch in range(3)
                    ]
                }
                for depth in range(3)
            ],
        ]
    )

    def never_passes(sql: str, _briefing: Mapping[str, Any]) -> Mapping[str, Any]:
        evaluated.append(sql)
        return {
            "guardrail_ok": True,
            "schema_valid": True,
            "execution_ok": True,
            "equivalent": False,
            "evidence_support": 0,
            "complexity": len(sql),
        }

    graph = build_migration_graph(
        provider,
        evaluator=never_passes,
        branching_factor=99,
        beam_width=99,
        max_depth=99,
    )
    result = graph.invoke(_state())

    assert result["tot_depth"] == 3
    assert len(result["branches"]) <= 2
    assert len(evaluated) <= 3 * result["tot_depth"]
    assert len(provider.calls) == 4  # initial proposal plus three expansion rounds


def test_subagent_prompt_excludes_unbriefed_parent_history_and_memory() -> None:
    provider = MockProvider(structured_responses=[_proposal("SELECT name FROM customers")])
    graph = build_migration_graph(provider)

    graph.invoke(
        _state(
            history=[{"content": "PARENT_HISTORY_SENTINEL"}],
            private_memory=[{"text": "PRIVATE_MEMORY_SENTINEL"}],
            memories=[{"text": "UNBRIEFED_MEMORY_SENTINEL"}],
            impact={"summary": "UNNEEDED_IMPACT_SENTINEL"},
            comparison_policy={"ordered": True},
            evidence=[
                {
                    "kind": "approved_memory",
                    "record": {"text": "EXPLICIT_BRIEFING_MEMORY"},
                }
            ],
        )
    )

    messages = provider.calls[0].input
    human_payload = json.loads(messages[-1][1])
    serialized = json.dumps(human_payload)
    assert "PARENT_HISTORY_SENTINEL" not in serialized
    assert "PRIVATE_MEMORY_SENTINEL" not in serialized
    assert "UNBRIEFED_MEMORY_SENTINEL" not in serialized
    assert "UNNEEDED_IMPACT_SENTINEL" not in serialized
    assert "EXPLICIT_BRIEFING_MEMORY" in serialized
    assert human_payload["request"] == "Preserve the original output contract."
    assert human_payload["comparison_policy"] == {"ordered": True}
    assert set(human_payload) == {
        "request",
        "original_sql",
        "old_schema",
        "new_schema",
        "evidence",
        "comparison_policy",
        "validation_feedback",
        "reviewer_feedback",
    }
