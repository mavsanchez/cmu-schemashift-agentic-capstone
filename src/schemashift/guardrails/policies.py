"""Shared bounded policies used by deterministic tools and graph routing."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_ABSOLUTE_TOLERANCE = 1e-6
DEFAULT_RELATIVE_TOLERANCE = 1e-6
DEFAULT_QUERY_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RESULT_ROWS = 10_000
DEFAULT_MISMATCH_SAMPLE_LIMIT = 20
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.80
DEFAULT_AMBIGUITY_MARGIN = 0.10


@dataclass(frozen=True, slots=True)
class TreeOfThoughtLimits:
    branching_factor: int = 3
    beam_width: int = 2
    depth: int = 3

    def __post_init__(self) -> None:
        if not 1 <= self.branching_factor <= 3:
            raise ValueError("branching_factor must be between 1 and 3")
        if not 1 <= self.beam_width <= 2:
            raise ValueError("beam_width must be between 1 and 2")
        if not 1 <= self.depth <= 3:
            raise ValueError("depth must be between 1 and 3")


@dataclass(frozen=True, slots=True)
class RetryLimits:
    model_attempts: int = 3
    migration_revisions: int = 10

    @property
    def migration_attempts(self) -> int:
        return self.migration_revisions + 1

    def __post_init__(self) -> None:
        if self.model_attempts < 1:
            raise ValueError("model_attempts must be positive")
        if not 0 <= self.migration_revisions <= 10:
            raise ValueError("migration_revisions must be between 0 and 10")


__all__ = [
    "DEFAULT_ABSOLUTE_TOLERANCE",
    "DEFAULT_AMBIGUITY_MARGIN",
    "DEFAULT_LOW_CONFIDENCE_THRESHOLD",
    "DEFAULT_MAX_RESULT_ROWS",
    "DEFAULT_MISMATCH_SAMPLE_LIMIT",
    "DEFAULT_QUERY_TIMEOUT_SECONDS",
    "DEFAULT_RELATIVE_TOLERANCE",
    "RetryLimits",
    "TreeOfThoughtLimits",
]
