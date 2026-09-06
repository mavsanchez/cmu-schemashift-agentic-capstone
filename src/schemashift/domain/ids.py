"""Correlation identifiers and invariants."""

from __future__ import annotations

from typing import TypeAlias
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, model_validator

ConversationId: TypeAlias = UUID
SessionId: TypeAlias = UUID
RunId: TypeAlias = UUID
TurnId: TypeAlias = UUID


def new_uuid() -> UUID:
    """Generate a random identifier for a SchemaShift entity."""

    return uuid4()


def parse_uuid(value: UUID | str, *, field_name: str = "identifier") -> UUID:
    """Parse an identifier and raise a descriptive ``ValueError`` when invalid."""

    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid UUID") from exc


class RunContext(BaseModel):
    """Immutable correlation context for one graph invocation.

    SchemaShift v1 intentionally treats a turn and a run as the same unit. The
    invariant is validated here and reinforced by PostgreSQL constraints.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: ConversationId
    session_id: SessionId
    run_id: RunId
    turn_id: TurnId | None = None

    @model_validator(mode="after")
    def set_and_validate_turn_id(self) -> RunContext:
        if self.turn_id is None:
            object.__setattr__(self, "turn_id", self.run_id)
        elif self.turn_id != self.run_id:
            raise ValueError("turn_id must equal run_id")
        return self

    @classmethod
    def create(
        cls,
        *,
        conversation_id: UUID | None = None,
        session_id: UUID | None = None,
        run_id: UUID | None = None,
    ) -> RunContext:
        """Create a context while allowing callers to reuse a conversation/session."""

        generated_run_id = run_id or new_uuid()
        return cls(
            conversation_id=conversation_id or new_uuid(),
            session_id=session_id or new_uuid(),
            run_id=generated_run_id,
            turn_id=generated_run_id,
        )

    def with_session(self, session_id: UUID) -> RunContext:
        """Create the correlation context used when a later UI session resumes a run."""

        return self.model_copy(update={"session_id": session_id})

    def correlation(self) -> dict[str, str]:
        """Return JSON-friendly correlation fields for logs and wire events."""

        return {
            "conversation_id": str(self.conversation_id),
            "session_id": str(self.session_id),
            "run_id": str(self.run_id),
            "turn_id": str(self.turn_id),
        }
