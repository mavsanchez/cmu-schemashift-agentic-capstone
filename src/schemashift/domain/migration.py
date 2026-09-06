"""Migration proposal and uploaded-source contracts."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class SourceRole(StrEnum):
    OLD_SCHEMA = "old_schema"
    NEW_SCHEMA = "new_schema"
    SOURCE_SQL = "source_sql"
    MIGRATION_KNOWLEDGE = "migration_knowledge"
    OLD_DATA = "old_data"
    NEW_DATA = "new_data"


class MappingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    old_reference: str = Field(
        min_length=1,
        validation_alias=AliasChoices("old_reference", "old_expression"),
    )
    new_expression: str = Field(min_length=1)
    change_type: str = Field(default="mapping", min_length=1)
    rationale: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)

    @property
    def old_expression(self) -> str:
        """Backward-compatible name for callers that describe an expression."""

        return self.old_reference


class MigrationAlternative(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: UUID = Field(default_factory=uuid4)
    candidate_sql: str = Field(min_length=1)
    rationale: str = ""
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("candidate_sql")
    @classmethod
    def candidate_sql_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("candidate_sql must not be blank")
        return value


class MigrationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: UUID = Field(default_factory=uuid4)
    candidate_sql: str = Field(min_length=1)
    mappings: list[MappingCandidate] = Field(default_factory=list)
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    alternatives: list[MigrationAlternative] = Field(default_factory=list)
    overall_confidence: float = Field(
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices("overall_confidence", "confidence"),
    )
    requires_human_review: bool = False
    evidence_conflict: bool = False
    ambiguity_flags: list[str] = Field(default_factory=list)

    @field_validator("candidate_sql")
    @classmethod
    def candidate_sql_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("candidate_sql must not be blank")
        return value

    @field_validator("ambiguity_flags", mode="before")
    @classmethod
    def normalize_ambiguity_flags(cls, value: object) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, (list, tuple, set)) else [value]
        no_op = {"none", "no ambiguity", "not ambiguous", "n/a", "na", "no issues"}
        return [
            text for item in values if (text := str(item).strip()) and text.casefold() not in no_op
        ]

    @property
    def is_ambiguous(self) -> bool:
        return self.requires_human_review or self.evidence_conflict or bool(self.ambiguity_flags)

    @property
    def confidence(self) -> float:
        """Compatibility shorthand for confidence-threshold routing."""

        return self.overall_confidence
