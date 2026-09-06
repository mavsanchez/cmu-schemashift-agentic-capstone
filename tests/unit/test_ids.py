from uuid import uuid4

import pytest
from pydantic import ValidationError

from schemashift.domain import RunContext, parse_uuid


def test_run_context_defaults_turn_id_to_run_id() -> None:
    context = RunContext(
        conversation_id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
    )

    assert context.turn_id == context.run_id
    assert context.correlation() == {
        "conversation_id": str(context.conversation_id),
        "session_id": str(context.session_id),
        "run_id": str(context.run_id),
        "turn_id": str(context.run_id),
    }


def test_run_context_rejects_distinct_turn_id() -> None:
    with pytest.raises(ValidationError, match="turn_id must equal run_id"):
        RunContext(
            conversation_id=uuid4(),
            session_id=uuid4(),
            run_id=uuid4(),
            turn_id=uuid4(),
        )


def test_resume_context_changes_only_session() -> None:
    context = RunContext.create()
    resumed = context.with_session(uuid4())

    assert resumed.session_id != context.session_id
    assert resumed.conversation_id == context.conversation_id
    assert resumed.run_id == context.run_id
    assert resumed.turn_id == context.turn_id


def test_parse_uuid_has_descriptive_error() -> None:
    with pytest.raises(ValueError, match="conversation_id must be a valid UUID"):
        parse_uuid("not-a-uuid", field_name="conversation_id")
