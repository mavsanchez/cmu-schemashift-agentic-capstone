from __future__ import annotations

import os
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest

from schemashift.config import Settings
from schemashift.domain import (
    ActivityEvent,
    HumanDecision,
    HumanReviewRequiredEvent,
    RunContext,
)
from schemashift.persistence import (
    ActiveRunExistsError,
    ActorType,
    ArtifactStatus,
    PersistenceConflictError,
    PostgresRepository,
    ReviewStatus,
    RunStatus,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def repository() -> PostgresRepository:
    if os.getenv("SCHEMASHIFT_RUN_INTEGRATION") != "1":
        pytest.skip("set SCHEMASHIFT_RUN_INTEGRATION=1 to run PostgreSQL integration tests")
    settings = Settings()
    repo = PostgresRepository(settings)
    try:
        repo.initialize()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL is unavailable: {exc}")
    return repo


def delete_test_conversation(repository: PostgresRepository, conversation_id: object) -> None:
    """Remove only records created by one UUID-scoped integration test."""

    with repository.db.transaction() as connection:
        for table in (
            "artifacts",
            "human_reviews",
            "agent_events",
            "messages",
            "uploaded_sources",
            "runs",
            "sessions",
            "conversations",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE conversation_id = %s",
                (conversation_id,),
            )


def test_schema_initialization_is_idempotent(repository: PostgresRepository) -> None:
    repository.initialize()
    repository.initialize()
    assert repository.health_check()


def test_history_integrity_review_resume_and_artifact(repository: PostgresRepository) -> None:
    conversation_id = uuid4()
    first_session_id = uuid4()
    resumed_session_id = uuid4()
    run_id = uuid4()
    context = RunContext(
        conversation_id=conversation_id,
        session_id=first_session_id,
        run_id=run_id,
    )
    try:
        repository.create_conversation("Integration test", conversation_id=conversation_id)
        repository.create_session(conversation_id, session_id=first_session_id)
        repository.create_session(conversation_id, session_id=resumed_session_id)
        repository.create_run(context)

        repository.add_message(
            context,
            actor_type=ActorType.USER,
            role="user",
            content="Migrate the query",
        )
        repository.add_event(
            ActivityEvent(
                conversation_id=conversation_id,
                session_id=first_session_id,
                run_id=run_id,
                label="Run started",
            )
        )
        request, result = repository.add_tool_interaction(
            context,
            tool_name="parse_sql",
            tool_call_id="call-1",
            request_content='{"sql":"SELECT 1"}',
            result_content='{"valid":true}',
        )
        assert request.tool_call_id == result.tool_call_id == "call-1"

        review_event = HumanReviewRequiredEvent(
            conversation_id=conversation_id,
            session_id=first_session_id,
            run_id=run_id,
            review_id=uuid4(),
            candidate_id=uuid4(),
            title="Approve candidate",
            reason="Evidence conflicts",
            risk="medium",
            payload={"candidate_sql": "SELECT 1"},
        )
        repository.add_event(review_event)
        repository.add_event(review_event)
        review = repository.create_review(review_event)
        assert repository.create_review(review_event).review_id == review.review_id
        assert repository.get_run(run_id).status is RunStatus.WAITING_HUMAN

        source_content = b"CREATE TABLE old_customer (customer_id INTEGER);\n"
        source = repository.register_source(
            conversation_id=conversation_id,
            session_id=first_session_id,
            original_name="old_schema.sql",
            local_path=f"data/generated/uploads/{conversation_id}/{first_session_id}/old_schema.sql",
            mime_type="application/sql",
            sha256=sha256(source_content).hexdigest(),
            size_bytes=len(source_content),
            inferred_role="old_schema",
        )
        confirmed = repository.confirm_source(source.source_id, "old_schema")
        assert confirmed.status.value == "confirmed"
        assert repository.list_sources(conversation_id) == [confirmed]

        decision = HumanDecision(
            decision="approve",
            review_id=review.review_id,
            run_id=run_id,
            session_id=resumed_session_id,
        )
        decided = repository.decide_review(decision)
        assert decided.decision_session_id == resumed_session_id

        resumed_context = context.with_session(resumed_session_id)
        artifact = repository.create_artifact(
            resumed_context,
            file_name="migration.sql",
            local_path=f"data/generated/migrations/{conversation_id}/{run_id}/migration.sql",
            sha256=sha256(b"SELECT 1\n").hexdigest(),
            status=ArtifactStatus.HUMAN_APPROVED,
            known_differences=[{"reason": "human override"}],
        )
        assert artifact.status is ArtifactStatus.HUMAN_APPROVED
        repository.update_run_status(run_id, RunStatus.COMPLETED)
        assert len(repository.list_messages(conversation_id)) == 3
        assert len(repository.list_events(conversation_id, run_id=run_id)) == 2
    finally:
        delete_test_conversation(repository, conversation_id)


def test_only_one_open_run_per_conversation(repository: PostgresRepository) -> None:
    conversation_id = uuid4()
    session_id = uuid4()
    try:
        repository.create_conversation(conversation_id=conversation_id)
        repository.create_session(conversation_id, session_id=session_id)
        repository.create_run(
            RunContext(conversation_id=conversation_id, session_id=session_id, run_id=uuid4())
        )
        with pytest.raises(ActiveRunExistsError):
            repository.create_run(
                RunContext(
                    conversation_id=conversation_id,
                    session_id=session_id,
                    run_id=uuid4(),
                )
            )
    finally:
        delete_test_conversation(repository, conversation_id)


def test_review_resume_claim_is_exactly_once(repository: PostgresRepository) -> None:
    conversation_id, session_id, resumed_session_id, run_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    try:
        repository.create_conversation(conversation_id=conversation_id)
        repository.create_session(conversation_id, session_id=session_id)
        repository.create_session(conversation_id, session_id=resumed_session_id)
        context = RunContext(
            conversation_id=conversation_id,
            session_id=session_id,
            run_id=run_id,
        )
        repository.create_run(context, status=RunStatus.ACTIVE)
        event = HumanReviewRequiredEvent(
            conversation_id=conversation_id,
            session_id=session_id,
            run_id=run_id,
            review_id=uuid4(),
            candidate_id=uuid4(),
            title="Approve candidate",
            reason="Evidence is ambiguous",
            risk="medium",
            payload={"candidate_sql": "SELECT 1"},
        )
        repository.create_review(event)
        decision = HumanDecision(
            decision="approve",
            review_id=event.review_id,
            run_id=run_id,
            session_id=resumed_session_id,
        )

        claimed = repository.claim_review_decision(decision)

        assert claimed.decision_session_id == resumed_session_id
        assert repository.get_run(run_id).status is RunStatus.ACTIVE
        decisions = [
            message
            for message in repository.list_messages(conversation_id, run_id=run_id)
            if message.role == "human_decision"
        ]
        assert len(decisions) == 1
        assert decisions[0].session_id == resumed_session_id
        with pytest.raises(PersistenceConflictError, match="already been resumed"):
            repository.claim_review_decision(decision)
        assert repository.get_run(run_id).status is RunStatus.ACTIVE
        assert (
            len(
                [
                    message
                    for message in repository.list_messages(conversation_id, run_id=run_id)
                    if message.role == "human_decision"
                ]
            )
            == 1
        )
    finally:
        delete_test_conversation(repository, conversation_id)


def test_failed_resume_claim_can_be_atomically_released_and_retried(
    repository: PostgresRepository,
) -> None:
    conversation_id, session_id, resumed_session_id, run_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    try:
        repository.create_conversation(conversation_id=conversation_id)
        repository.create_session(conversation_id, session_id=session_id)
        repository.create_session(conversation_id, session_id=resumed_session_id)
        context = RunContext(
            conversation_id=conversation_id,
            session_id=session_id,
            run_id=run_id,
        )
        repository.create_run(context, status=RunStatus.ACTIVE)
        event = HumanReviewRequiredEvent(
            conversation_id=conversation_id,
            session_id=session_id,
            run_id=run_id,
            review_id=uuid4(),
            candidate_id=uuid4(),
            title="Approve candidate",
            reason="Evidence is ambiguous",
            risk="medium",
            payload={"candidate_sql": "SELECT 1"},
        )
        repository.create_review(event)
        decision = HumanDecision(
            decision="approve",
            review_id=event.review_id,
            run_id=run_id,
            session_id=resumed_session_id,
        )

        repository.claim_review_decision(decision)
        released = repository.release_review_decision(
            decision, error_detail="checkpoint temporarily unavailable"
        )

        assert released.status is ReviewStatus.PENDING
        assert released.decision is None
        assert repository.get_run(run_id).status is RunStatus.WAITING_HUMAN
        assert repository.list_messages(conversation_id, run_id=run_id) == []
        retried = repository.claim_review_decision(decision)
        assert retried.status is ReviewStatus.APPROVED
        assert repository.get_run(run_id).status is RunStatus.ACTIVE
    finally:
        delete_test_conversation(repository, conversation_id)


def test_run_finalization_is_atomic_and_exactly_once(
    repository: PostgresRepository,
) -> None:
    conversation_id, session_id, run_id = uuid4(), uuid4(), uuid4()
    context = RunContext(
        conversation_id=conversation_id,
        session_id=session_id,
        run_id=run_id,
    )
    try:
        repository.create_conversation(conversation_id=conversation_id)
        repository.create_session(conversation_id, session_id=session_id)
        repository.create_run(context, status=RunStatus.ACTIVE)

        completed = repository.finalize_run(
            context,
            answer="Migration completed.",
            status=RunStatus.COMPLETED,
            revision_count=2,
            metrics={"revision_count": 2, "tool_calls": 4},
            output_metadata={"artifact_path": "migration.sql"},
            artifact={
                "file_name": "migration.sql",
                "local_path": f"data/generated/migrations/{conversation_id}/{run_id}/migration.sql",
                "status": ArtifactStatus.VALIDATED,
                "sha256": sha256(b"SELECT 1\n").hexdigest(),
            },
        )

        assert completed.status is RunStatus.COMPLETED
        assert completed.revision_count == 2
        assert completed.metrics["tool_calls"] == 4
        assert [item.role for item in repository.list_messages(conversation_id, run_id=run_id)] == [
            "assistant"
        ]
        assert [
            item.event_type for item in repository.list_events(conversation_id, run_id=run_id)
        ] == ["run_output"]
        assert len(repository.list_artifacts(conversation_id, run_id=run_id)) == 1

        with pytest.raises(PersistenceConflictError, match="cannot be finalized"):
            repository.finalize_run(
                context,
                answer="Duplicate",
                status=RunStatus.COMPLETED,
                revision_count=2,
            )
        assert len(repository.list_messages(conversation_id, run_id=run_id)) == 1
    finally:
        delete_test_conversation(repository, conversation_id)


def test_run_finalization_rolls_back_every_record_on_failure(
    repository: PostgresRepository,
) -> None:
    conversation_id, session_id, run_id = uuid4(), uuid4(), uuid4()
    context = RunContext(
        conversation_id=conversation_id,
        session_id=session_id,
        run_id=run_id,
    )
    try:
        repository.create_conversation(conversation_id=conversation_id)
        repository.create_session(conversation_id, session_id=session_id)
        repository.create_run(context, status=RunStatus.ACTIVE)

        with pytest.raises(psycopg.errors.CheckViolation):
            repository.finalize_run(
                context,
                answer="Must be rolled back",
                status=RunStatus.COMPLETED,
                revision_count=0,
                artifact={
                    "file_name": "invalid.sql",
                    "local_path": (
                        f"data/generated/migrations/{conversation_id}/{run_id}/invalid.sql"
                    ),
                    "status": ArtifactStatus.VALIDATED,
                    "sha256": "not-a-sha256",
                },
            )

        assert repository.get_run(run_id).status is RunStatus.ACTIVE
        assert repository.list_messages(conversation_id, run_id=run_id) == []
        assert repository.list_events(conversation_id, run_id=run_id) == []
        assert repository.list_artifacts(conversation_id, run_id=run_id) == []
    finally:
        delete_test_conversation(repository, conversation_id)


def test_composite_keys_reject_cross_conversation_session(
    repository: PostgresRepository,
) -> None:
    first_conversation = uuid4()
    second_conversation = uuid4()
    first_session = uuid4()
    second_session = uuid4()
    run_id = uuid4()
    try:
        repository.create_conversation(conversation_id=first_conversation)
        repository.create_conversation(conversation_id=second_conversation)
        repository.create_session(first_conversation, session_id=first_session)
        repository.create_session(second_conversation, session_id=second_session)
        repository.create_run(
            RunContext(
                conversation_id=first_conversation,
                session_id=first_session,
                run_id=run_id,
            )
        )

        invalid_context = RunContext(
            conversation_id=first_conversation,
            session_id=second_session,
            run_id=run_id,
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            repository.add_message(
                invalid_context,
                actor_type=ActorType.USER,
                content="This session belongs to a different conversation",
            )
    finally:
        delete_test_conversation(repository, first_conversation)
        delete_test_conversation(repository, second_conversation)
