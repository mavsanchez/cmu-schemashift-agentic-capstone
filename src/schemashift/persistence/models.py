"""Typed records returned by PostgreSQL repositories."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from schemashift.domain.events import (
    Component,
    ComponentStatus,
    HumanDecisionChoice,
    ReviewRisk,
)
from schemashift.domain.ids import RunContext
from schemashift.domain.migration import SourceRole


class RunStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    UNRESOLVED = "unresolved"
    FAILED = "failed"


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SUBAGENT = "subagent"
    TOOL = "tool"
    SYSTEM = "system"


class SourceStatus(StrEnum):
    STAGED = "staged"
    CONFIRMED = "confirmed"
    INDEXED = "indexed"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ArtifactStatus(StrEnum):
    VALIDATED = "validated"
    HUMAN_APPROVED = "human_approved"


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class ConversationRecord(_Record):
    conversation_id: UUID
    title: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SessionRecord(_Record):
    session_id: UUID
    conversation_id: UUID
    started_at: datetime
    ended_at: datetime | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunRecord(_Record):
    run_id: UUID
    conversation_id: UUID
    origin_session_id: UUID
    turn_id: UUID
    status: RunStatus
    revision_count: int
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_detail: str | None
    started_at: datetime
    waiting_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime

    def as_context(self, *, session_id: UUID | None = None) -> RunContext:
        return RunContext(
            conversation_id=self.conversation_id,
            session_id=session_id or self.origin_session_id,
            run_id=self.run_id,
            turn_id=self.turn_id,
        )


class MessageRecord(_Record):
    message_id: int
    conversation_id: UUID
    session_id: UUID
    run_id: UUID
    turn_id: UUID
    actor_type: ActorType
    actor_name: str | None
    role: str | None
    content: str
    tool_name: str | None
    tool_call_id: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AgentEventRecord(_Record):
    event_id: int
    conversation_id: UUID
    session_id: UUID
    run_id: UUID
    event_type: str
    component: Component | None
    status: ComponentStatus | None
    label: str | None
    detail: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class UploadedSourceRecord(_Record):
    source_id: UUID
    conversation_id: UUID
    session_id: UUID
    original_name: str
    local_path: str
    role: SourceRole | None
    mime_type: str
    sha256: str
    size_bytes: int
    status: SourceStatus
    table_name: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @property
    def content_type(self) -> str:
        return self.mime_type


class HumanReviewRecord(_Record):
    review_id: UUID
    candidate_id: UUID
    conversation_id: UUID
    run_id: UUID
    requested_session_id: UUID
    decision_session_id: UUID | None
    title: str
    reason: str
    risk: ReviewRisk
    payload: dict[str, Any] = Field(default_factory=dict)
    status: ReviewStatus
    decision: HumanDecisionChoice | None
    reviewer_feedback: str | None
    requested_at: datetime
    decided_at: datetime | None


class ArtifactRecord(_Record):
    artifact_id: UUID
    conversation_id: UUID
    session_id: UUID
    run_id: UUID
    file_name: str
    local_path: str
    artifact_type: str
    status: ArtifactStatus
    sha256: str
    known_differences: list[Any] | dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
