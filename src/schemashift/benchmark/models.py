"""Typed records written by the benchmark harness."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BenchmarkMode(StrEnum):
    MOCK = "mock"
    LIVE = "live"


class BenchmarkArm(StrEnum):
    BASELINE = "baseline"
    WORKFLOW = "workflow"


class BenchmarkResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    motif_id: int
    motif: str
    sql_form: str
    arm: BenchmarkArm
    mode: BenchmarkMode
    candidate_sql: str = ""
    parse_success: bool = False
    schema_valid: bool = False
    execution_success: bool = False
    equivalent: bool = False
    retrieval_top_k: list[str] = Field(default_factory=list)
    retrieval_recall: float = Field(default=0.0, ge=0.0, le=1.0)
    recovery: bool = False
    intervention: bool = False
    tool_calls: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    embedding_calls: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    memory_used: bool = False
    migration_specialist_invocations: int = Field(default=0, ge=0)
    validation_specialist_invocations: int = Field(default=0, ge=0)
    verdict: str = "error"
    route: str = "error"
    terminal_status: str = "failed"
    revision_count: int = Field(default=0, ge=0)
    expected_verdict: str
    expected_human_review: bool
    expected_equivalent: bool
    expected_route_matched: bool = False
    error: str = ""


class ArmSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: int = 0
    parse_rate: float = 0.0
    schema_rate: float = 0.0
    execution_rate: float = 0.0
    equivalence_rate: float = 0.0
    expected_route_accuracy: float = 0.0
    retrieval_recall: float = 0.0
    recoveries: int = 0
    interventions: int = 0
    model_calls: int = 0
    embedding_calls: int = 0
    tool_calls: int = 0
    migration_specialist_invocations: int = 0
    validation_specialist_invocations: int = 0
    total_latency_ms: float = 0.0


class BenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    benchmark_seed: int
    mode: BenchmarkMode
    started_at: datetime
    completed_at: datetime
    model: str
    embedding_model: str
    temperature: float
    settings: dict[str, Any]
    case_count: int
    arms: list[BenchmarkArm]
    summaries: dict[str, ArmSummary]
    comparison: dict[str, float]
    results: list[BenchmarkResult]


__all__ = [
    "ArmSummary",
    "BenchmarkArm",
    "BenchmarkMode",
    "BenchmarkReport",
    "BenchmarkResult",
]
