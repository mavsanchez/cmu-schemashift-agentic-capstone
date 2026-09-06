"""Conservative policy for promoting observations into durable memory."""

from __future__ import annotations

import re

from .schemas import (
    MemoryCandidate,
    MemoryPolicyDecision,
    MemorySourceType,
    MemoryType,
)

_TRANSIENT_REQUEST = re.compile(
    r"\b(?:please|right now|for this turn|show me|tell me|generate|write|create)\b",
    re.IGNORECASE,
)


class MemoryWritePolicy:
    """Allow only durable, attributable migration facts.

    Model or tool output is evidence, not memory by itself.  It becomes eligible
    only after an explicit human approval is represented in provenance.
    """

    durable_types = frozenset(MemoryType)

    def __init__(self, *, min_confidence: float = 0.8, min_text_length: int = 12) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        if min_text_length < 1:
            raise ValueError("min_text_length must be positive")
        self.min_confidence = min_confidence
        self.min_text_length = min_text_length

    def evaluate(self, candidate: MemoryCandidate) -> MemoryPolicyDecision:
        text = " ".join(candidate.text.split())
        source = candidate.provenance.source_type

        if len(text) < self.min_text_length:
            return MemoryPolicyDecision(
                allowed=False,
                reason="candidate is too short to be durable",
            )
        if candidate.memory_type not in self.durable_types:
            return MemoryPolicyDecision(allowed=False, reason="memory type is not durable")
        if source is MemorySourceType.TOOL_OUTPUT and not candidate.provenance.human_approved:
            return MemoryPolicyDecision(
                allowed=False,
                reason="tool output is evidence and cannot become memory without human approval",
            )
        if source is MemorySourceType.AGENT_REFLECTION and not candidate.provenance.human_approved:
            return MemoryPolicyDecision(
                allowed=False,
                reason="agent reflection requires human approval before durable storage",
                requires_human_review=True,
            )
        if candidate.confidence < self.min_confidence:
            return MemoryPolicyDecision(
                allowed=False,
                reason=(
                    f"confidence {candidate.confidence:.2f} is below "
                    f"the {self.min_confidence:.2f} memory threshold"
                ),
                requires_human_review=True,
            )
        if (
            _TRANSIENT_REQUEST.search(text)
            and candidate.memory_type is not MemoryType.USER_PREFERENCE
        ):
            return MemoryPolicyDecision(
                allowed=False,
                reason=(
                    "candidate appears to describe a transient request rather than a durable fact"
                ),
            )

        if source is MemorySourceType.USER_STATEMENT and candidate.memory_type not in {
            MemoryType.USER_PREFERENCE,
            MemoryType.NAMING_CONVENTION,
            MemoryType.UNRESOLVED_CONSTRAINT,
            MemoryType.BUSINESS_RULE,
        }:
            return MemoryPolicyDecision(
                allowed=False,
                reason="a migration decision must be approved or independently validated",
                requires_human_review=True,
            )

        return MemoryPolicyDecision(allowed=True, reason="durable fact has acceptable provenance")


def evaluate_memory_candidate(
    candidate: MemoryCandidate,
    *,
    min_confidence: float = 0.8,
) -> MemoryPolicyDecision:
    return MemoryWritePolicy(min_confidence=min_confidence).evaluate(candidate)


def should_store_memory(candidate: MemoryCandidate, *, min_confidence: float = 0.8) -> bool:
    return evaluate_memory_candidate(candidate, min_confidence=min_confidence).allowed
