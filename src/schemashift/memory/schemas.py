"""Typed long-term-memory records and provenance."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryType(StrEnum):
    MIGRATION_DECISION = "migration_decision"
    NAMING_CONVENTION = "naming_convention"
    UNRESOLVED_CONSTRAINT = "unresolved_constraint"
    USER_PREFERENCE = "user_preference"
    BUSINESS_RULE = "business_rule"


class MemorySourceType(StrEnum):
    HUMAN_APPROVAL = "human_approval"
    USER_STATEMENT = "user_statement"
    VALIDATED_MIGRATION = "validated_migration"
    AGENT_REFLECTION = "agent_reflection"
    TOOL_OUTPUT = "tool_output"


class MemoryProvenance(BaseModel):
    """Where a proposed durable fact came from.

    Correlation IDs are strings here so the memory package accepts UUID value
    objects or existing serialized IDs without importing graph/domain classes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: MemorySourceType
    source_id: str = Field(min_length=1)
    conversation_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    actor: str | None = None
    human_approved: bool = False
    detail: str | None = None

    @field_validator("source_id", "conversation_id", "session_id", "run_id", mode="before")
    @classmethod
    def serialize_identifiers(cls, value: Any) -> Any:
        return None if value is None else str(value)


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    memory_type: MemoryType
    provenance: MemoryProvenance
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    conversation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("conversation_id", mode="before")
    @classmethod
    def serialize_conversation_id(cls, value: Any) -> Any:
        return None if value is None else str(value)


class MemoryPolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str
    requires_human_review: bool = False


class MemoryRecord(MemoryCandidate):
    memory_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Redis holds this as packed FLOAT32 bytes.  It remains available on a
    # freshly written record but is excluded from ordinary JSON/log output.
    embedding: list[float] = Field(default_factory=list, exclude=True)

    @field_validator("memory_id", mode="before")
    @classmethod
    def serialize_memory_id(cls, value: Any) -> str:
        return str(value)


class MemorySearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory: MemoryRecord
    score: float = Field(ge=-1.0, le=1.0)
    distance: float = Field(ge=0.0)


class MemoryWriteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    stored: bool
    reason: str
    memory: MemoryRecord | None = None
