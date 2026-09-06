"""Runtime event and human-review contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class Component(StrEnum):
    MCP = "mcp"
    MEMORY = "memory"
    VECTOR = "vector"
    AGENT = "agent"
    SUBAGENT = "subagent"
    MODEL = "model"
    GUARDRAIL = "guardrail"


class ComponentStatus(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"
    DONE = "done"
    ERROR = "error"


class ActivityLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ReviewRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class HumanDecisionChoice(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class _CorrelatedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    session_id: UUID
    run_id: UUID
    timestamp: datetime = Field(default_factory=utc_now)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include timezone information")
        return value


class ComponentStatusEvent(_CorrelatedEvent):
    type: Literal["component_status"] = "component_status"
    component: Component
    status: ComponentStatus
    detail: str = ""


class ActivityEvent(_CorrelatedEvent):
    type: Literal["activity"] = "activity"
    level: ActivityLevel = ActivityLevel.INFO
    label: str = Field(min_length=1)
    detail: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanReviewRequiredEvent(_CorrelatedEvent):
    type: Literal["human_review_required"] = "human_review_required"
    review_id: UUID
    candidate_id: UUID
    title: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    risk: ReviewRisk
    payload: dict[str, Any] = Field(default_factory=dict)


RuntimeEvent: TypeAlias = Annotated[
    ComponentStatusEvent | ActivityEvent | HumanReviewRequiredEvent,
    Field(discriminator="type"),
]


class HumanDecision(BaseModel):
    """A decision made in the current session for a durable interrupted run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: HumanDecisionChoice
    review_id: UUID
    run_id: UUID
    session_id: UUID
    reviewer_feedback: str | None = None
    decided_at: datetime = Field(default_factory=utc_now)

    @field_validator("reviewer_feedback")
    @classmethod
    def normalize_feedback(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("decided_at")
    @classmethod
    def decided_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decided_at must include timezone information")
        return value
