"""Dependency context shared by the pure functions and MCP wrappers."""

from __future__ import annotations

from dataclasses import dataclass

from schemashift.guardrails.policies import (
    DEFAULT_MAX_RESULT_ROWS,
    DEFAULT_MISMATCH_SAMPLE_LIMIT,
    DEFAULT_QUERY_TIMEOUT_SECONDS,
)

from .registry import SourceRegistry


@dataclass(frozen=True, slots=True)
class ToolContext:
    registry: SourceRegistry
    query_timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS
    max_result_rows: int = DEFAULT_MAX_RESULT_ROWS
    mismatch_sample_limit: int = DEFAULT_MISMATCH_SAMPLE_LIMIT
    read_source_char_limit: int = 100_000

    @classmethod
    def from_settings(cls, registry: SourceRegistry, settings: object) -> ToolContext:
        """Adapt the central Settings object without coupling this package to it."""

        return cls(
            registry=registry,
            query_timeout_seconds=float(
                getattr(settings, "query_timeout_seconds", DEFAULT_QUERY_TIMEOUT_SECONDS)
            ),
            max_result_rows=int(getattr(settings, "result_row_limit", DEFAULT_MAX_RESULT_ROWS)),
            mismatch_sample_limit=int(
                getattr(
                    settings,
                    "mismatch_sample_limit",
                    DEFAULT_MISMATCH_SAMPLE_LIMIT,
                )
            ),
        )

    def __post_init__(self) -> None:
        if self.query_timeout_seconds <= 0:
            raise ValueError("query_timeout_seconds must be positive")
        if self.max_result_rows < 1:
            raise ValueError("max_result_rows must be positive")
        if self.mismatch_sample_limit < 0:
            raise ValueError("mismatch_sample_limit must be non-negative")
        if self.read_source_char_limit < 1:
            raise ValueError("read_source_char_limit must be positive")


__all__ = ["ToolContext"]
