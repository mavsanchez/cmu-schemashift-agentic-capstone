"""Independent validation-subgraph result contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ValidationVerdict(StrEnum):
    PASS = "pass"
    REVISE = "revise"
    HUMAN_REVIEW = "human_review"


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: IssueSeverity = IssueSeverity.ERROR
    details: dict[str, Any] = Field(default_factory=dict)


class StructuralChecks(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parse_success: bool
    readonly_safe: bool
    schema_valid: bool
    tables_exist: bool
    columns_exist: bool
    diagnostics: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(
            (
                self.parse_success,
                self.readonly_safe,
                self.schema_valid,
                self.tables_exist,
                self.columns_exist,
            )
        )


class ExecutionComparison(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    execution_success: bool
    equivalent: bool
    old_row_count: int | None = Field(default=None, ge=0)
    new_row_count: int | None = Field(default=None, ge=0)
    column_names: list[str] = Field(default_factory=list)
    canonical_types: list[str] = Field(default_factory=list)
    ordered: bool = False
    truncated: bool = False
    missing_row_count: int = Field(default=0, ge=0)
    extra_row_count: int = Field(default=0, ge=0)
    duplicate_keys: list[dict[str, Any]] = Field(default_factory=list)
    null_deltas: dict[str, int] = Field(default_factory=dict)
    numeric_aggregates: dict[str, Any] = Field(default_factory=dict)
    mismatch_samples: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: ValidationVerdict
    structural_checks: StructuralChecks
    execution_comparison: ExecutionComparison
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.verdict is ValidationVerdict.PASS
            and self.structural_checks.passed
            and self.execution_comparison.execution_success
            and self.execution_comparison.equivalent
            and not self.execution_comparison.truncated
        )
