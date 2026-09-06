"""Deterministic ambiguity gates for selective Tree-of-Thought routing."""

from __future__ import annotations

from .policies import DEFAULT_AMBIGUITY_MARGIN, DEFAULT_LOW_CONFIDENCE_THRESHOLD


def should_use_tree_of_thought(
    plausible_mapping_count: int,
    top_confidence: float,
    runner_up_confidence: float | None = None,
    *,
    evidence_conflict: bool = False,
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
) -> bool:
    """Return whether bounded branch exploration is warranted.

    A single mapping never enters ToT.  With multiple mappings, any one of low
    confidence, a narrow top-two margin, or conflicting evidence activates it.
    """

    if plausible_mapping_count < 2:
        return False
    if not 0 <= top_confidence <= 1:
        raise ValueError("top_confidence must be between 0 and 1")
    if runner_up_confidence is not None and not 0 <= runner_up_confidence <= 1:
        raise ValueError("runner_up_confidence must be between 0 and 1")
    if evidence_conflict or top_confidence < low_confidence_threshold:
        return True
    return (
        runner_up_confidence is not None
        and top_confidence - runner_up_confidence < ambiguity_margin
    )


def requires_human_review(
    confidence: float,
    *,
    evidence_conflict: bool = False,
    retries_exhausted: bool = False,
    threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> bool:
    """Return the fixed human-review gate decision."""

    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return evidence_conflict or retries_exhausted or confidence < threshold


__all__ = ["requires_human_review", "should_use_tree_of_thought"]
