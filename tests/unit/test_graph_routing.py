from __future__ import annotations

import pytest

from schemashift.graph.routing import after_human_review, after_validation


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            {
                "proposal": {"overall_confidence": 0.80, "evidence_conflict": False},
                "validation": {"verdict": "pass"},
                "revision_count": 0,
                "max_revisions": 10,
            },
            "answer",
        ),
        (
            {
                "proposal": {"overall_confidence": 0.799, "evidence_conflict": False},
                "validation": {"verdict": "pass"},
                "revision_count": 0,
                "max_revisions": 10,
            },
            "human_review",
        ),
        (
            {
                "proposal": {"overall_confidence": 1.0, "evidence_conflict": True},
                "validation": {"verdict": "pass"},
                "revision_count": 0,
                "max_revisions": 10,
            },
            "human_review",
        ),
        (
            {
                "proposal": {"overall_confidence": 1.0, "requires_human_review": True},
                "validation": {"verdict": "pass"},
                "revision_count": 0,
                "max_revisions": 10,
            },
            "human_review",
        ),
        (
            {
                "proposal": {"overall_confidence": 1.0, "ambiguity_flags": ["two mappings"]},
                "validation": {"verdict": "pass"},
                "revision_count": 0,
                "max_revisions": 10,
            },
            "human_review",
        ),
        (
            {
                "proposal": {"overall_confidence": 1.0},
                "impact": {"requires_human_review": True},
                "validation": {"verdict": "pass"},
                "revision_count": 0,
                "max_revisions": 10,
            },
            "human_review",
        ),
        (
            {
                "proposal": {"overall_confidence": 1.0},
                "guardrail_passed": False,
                "validation": {"verdict": "pass"},
                "revision_count": 0,
                "max_revisions": 10,
            },
            "revise",
        ),
        (
            {
                "proposal": {"overall_confidence": 1.0},
                "guardrail_passed": False,
                "validation": {"verdict": "pass"},
                "revision_count": 10,
                "max_revisions": 10,
            },
            "human_review",
        ),
        (
            {
                "proposal": {"overall_confidence": 1.0},
                "validation": {"verdict": "revise"},
                "revision_count": 9,
                "max_revisions": 10,
            },
            "revise",
        ),
        (
            {
                "proposal": {"overall_confidence": 1.0},
                "validation": {"verdict": "revise"},
                "revision_count": 10,
                "max_revisions": 10,
            },
            "human_review",
        ),
        (
            {
                "proposal": {"overall_confidence": 1.0},
                "validation": {"verdict": "human_review"},
                "revision_count": 0,
                "max_revisions": 10,
            },
            "human_review",
        ),
    ],
)
def test_validation_routes_at_exact_policy_boundaries(
    state: dict[str, object], expected: str
) -> None:
    assert after_validation(state) == expected


@pytest.mark.parametrize(
    ("decision", "revision_count", "expected"),
    [
        ({"decision": "approve"}, 10, "answer"),
        ({"decision": "reject", "reviewer_feedback": "Use the canonical lookup."}, 0, "revise"),
        ({"decision": "reject", "feedback": "Use the canonical lookup."}, 9, "revise"),
        ({"decision": "reject", "reviewer_feedback": "   "}, 0, "unresolved"),
        ({"decision": "reject", "reviewer_feedback": "Fix it"}, 10, "unresolved"),
    ],
)
def test_human_decision_routes_by_feedback_and_remaining_budget(
    decision: dict[str, str], revision_count: int, expected: str
) -> None:
    state = {
        "human_decision": decision,
        "revision_count": revision_count,
        "max_revisions": 10,
    }
    assert after_human_review(state) == expected
