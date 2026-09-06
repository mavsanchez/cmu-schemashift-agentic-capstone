"""Internal Tree-of-Thought schemas for migration exploration."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from schemashift.domain.migration import MappingCandidate


class MigrationBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID = Field(default_factory=uuid4)
    candidate_sql: str = Field(min_length=1)
    mappings: list[MappingCandidate] = Field(default_factory=list)
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    requires_human_review: bool = False
    evidence_conflict: bool = False
    ambiguity_flags: list[str] = Field(default_factory=list)


class BranchExpansion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branches: list[MigrationBranch] = Field(default_factory=list, max_length=3)
