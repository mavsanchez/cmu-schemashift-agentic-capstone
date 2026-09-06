from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from schemashift.domain import (
    ActivityEvent,
    ComponentStatusEvent,
    HumanDecision,
    HumanReviewRequiredEvent,
    RunContext,
    RuntimeEvent,
)


def context_values() -> dict[str, object]:
    context = RunContext.create()
    return {
        "conversation_id": context.conversation_id,
        "session_id": context.session_id,
        "run_id": context.run_id,
    }


def test_component_and_status_enums_are_closed() -> None:
    values = context_values()
    event = ComponentStatusEvent(
        **values,
        component="vector",
        status="active",
        detail="Searching migration knowledge",
    )
    assert event.component.value == "vector"
    assert event.status.value == "active"

    with pytest.raises(ValidationError):
        ComponentStatusEvent(**values, component="database", status="active")
    with pytest.raises(ValidationError):
        ComponentStatusEvent(**values, component="vector", status="running")


def test_events_require_timezone_aware_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        ActivityEvent(
            **context_values(),
            label="test",
            timestamp=datetime(2026, 9, 5),
        )


def test_runtime_event_discriminator_round_trip() -> None:
    values = context_values()
    raw = HumanReviewRequiredEvent(
        **values,
        review_id=uuid4(),
        candidate_id=uuid4(),
        title="Approve migration",
        reason="Two mappings remain plausible",
        risk="medium",
        payload={"candidate_sql": "SELECT 1"},
    ).model_dump(mode="json")

    parsed = TypeAdapter(RuntimeEvent).validate_python(raw)
    assert isinstance(parsed, HumanReviewRequiredEvent)
    assert parsed.payload["candidate_sql"] == "SELECT 1"


def test_human_decision_normalizes_blank_feedback() -> None:
    decision = HumanDecision(
        decision="reject",
        review_id=uuid4(),
        run_id=uuid4(),
        session_id=uuid4(),
        reviewer_feedback="   ",
    )
    assert decision.reviewer_feedback is None
