from __future__ import annotations

from contextvars import copy_context
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from schemashift.domain import RunContext
from schemashift.persistence import ActorType, ReviewStatus, RunStatus
from schemashift.services.instrumentation import current_run_context, instrument_run
from schemashift.services.migration import MigrationRequest, MigrationService


@dataclass
class FakeRun:
    conversation_id: UUID
    origin_session_id: UUID
    run_id: UUID
    turn_id: UUID

    def as_context(self, *, session_id: UUID | None = None) -> RunContext:
        return RunContext(
            conversation_id=self.conversation_id,
            session_id=session_id or self.origin_session_id,
            run_id=self.run_id,
            turn_id=self.turn_id,
        )


class RecordingRepository:
    def __init__(self, *, fail_final_message: bool = False) -> None:
        self.fail_final_message = fail_final_message
        self.operations: list[tuple[str, Any]] = []
        self.run: FakeRun | None = None
        self.review: Any | None = None
        self.events: list[Any] = []
        self.messages: list[dict[str, Any]] = []
        self.statuses: list[RunStatus] = []
        self.decisions: list[Any] = []

    def create_run(self, context: RunContext, *, status: RunStatus) -> FakeRun:
        self.operations.append(("create_run", status))
        self.run = FakeRun(
            context.conversation_id,
            context.session_id,
            context.run_id,
            context.turn_id,
        )
        return self.run

    def add_message(self, context: RunContext, **values: Any) -> None:
        if self.fail_final_message and values.get("role") == "assistant":
            raise RuntimeError("final persistence unavailable")
        payload = {"context": context, **values}
        self.operations.append(("add_message", values.get("role")))
        self.messages.append(payload)

    def add_event(self, event: Any) -> None:
        self.operations.append(("add_event", getattr(event, "type", None)))
        self.events.append(event)

    def add_raw_event(self, context: RunContext, **values: Any) -> None:
        self.operations.append(("add_raw_event", values.get("event_type")))

    def update_run_status(self, _run_id: UUID, status: RunStatus, **values: Any) -> None:
        del values
        self.operations.append(("update_run_status", status))
        self.statuses.append(status)

    def get_review(self, review_id: UUID) -> Any | None:
        return self.review if self.review and self.review.review_id == review_id else None

    def get_run(self, run_id: UUID, **values: Any) -> FakeRun | None:
        del values
        return self.run if self.run and self.run.run_id == run_id else None

    def decide_review(self, decision: Any) -> None:
        self.operations.append(("decide_review", decision.decision))
        self.decisions.append(decision)


class CompletingGraph:
    def __init__(self) -> None:
        self.invocations: list[tuple[Any, dict[str, Any], Any]] = []

    def stream(self, graph_input: Any, *, config: dict[str, Any], stream_mode: Any):
        self.invocations.append((graph_input, config, stream_mode))
        if hasattr(graph_input, "resume"):
            identity = {
                "conversation_id": config["configurable"]["thread_id"],
                "session_id": graph_input.update["session_id"],
                "run_id": graph_input.resume["run_id"],
            }
        else:
            identity = {
                key: graph_input[key] for key in ("conversation_id", "session_id", "run_id")
            }
        yield (
            "custom",
            {
                "type": "component_status",
                **identity,
                "component": "agent",
                "status": "done",
                "detail": "Complete",
            },
        )
        yield ("custom", {"type": "assistant_token", **identity, "text": "Done"})
        state = {**identity, "answer": "Done", "status": "completed", "revision_count": 0}
        yield ("values", state)
        yield (
            "custom",
            {
                "type": "run_output",
                **identity,
                "answer": "Done",
                "status": "completed",
                "artifact_path": "",
                "metrics": {"revision_count": 0},
            },
        )


class FailingResumeGraph:
    def stream(self, *_args: Any, **_kwargs: Any):
        raise RuntimeError("checkpoint temporarily unavailable")
        yield  # pragma: no cover - make this a generator


class ContextCheckingGraph(CompletingGraph):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.fail = fail
        self.contexts: list[RunContext | None] = []
        self.closed = False

    def stream(self, *args: Any, **kwargs: Any):
        context = current_run_context()
        assert context is not None
        # Also exercise context-manager cleanup inside the graph generator.
        with instrument_run(context):
            try:
                for item in super().stream(*args, **kwargs):
                    self.contexts.append(current_run_context())
                    yield item
                    if self.fail:
                        raise RuntimeError("graph execution failed")
            finally:
                self.contexts.append(current_run_context())
                self.closed = True


class RecoveringReviewRepository(RecordingRepository):
    def claim_review_decision(self, decision: Any) -> None:
        self.operations.append(("claim_review_decision", decision.decision))

    def release_review_decision(self, decision: Any, **values: Any) -> None:
        self.operations.append(("release_review_decision", decision.decision))
        self.operations.append(("release_error", values["error_detail"]))


def _request() -> MigrationRequest:
    return MigrationRequest(
        request="Migrate this query",
        original_sql="SELECT legacy_name FROM legacy_customers",
        old_schema_source_id="old-schema",
        new_schema_source_id="new-schema",
        old_database_id="old-db",
        new_database_id="new-db",
    )


def test_service_persists_events_before_delivery_and_assistant_once() -> None:
    repository = RecordingRepository()
    graph = CompletingGraph()
    service = MigrationService(repository, graph)  # type: ignore[arg-type]
    conversation_id, session_id = uuid4(), uuid4()

    envelopes = list(service.start_run(conversation_id, session_id, _request()))

    resets = envelopes[:7]
    assert {item.event["component"] for item in resets} == {
        "mcp",
        "memory",
        "vector",
        "agent",
        "subagent",
        "model",
        "guardrail",
    }
    assert all(item.event["status"] == "idle" for item in resets)
    assert all(item.persisted for item in resets)
    streamed_component = next(
        item
        for item in envelopes
        if item.event.get("type") == "component_status" and item.event["status"] == "done"
    )
    assert any(
        str(event.run_id) == streamed_component.event["run_id"] and event.status.value == "done"
        for event in repository.events
    )
    tokens = [item for item in envelopes if item.event.get("type") == "assistant_token"]
    assert len(tokens) == 1 and tokens[0].persisted is False
    assert [message["role"] for message in repository.messages] == ["user", "assistant"]
    assert repository.statuses[-1] is RunStatus.COMPLETED


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("outcome", ["complete", "close", "error"])
def test_stream_survives_consumer_context_switches(resume: bool, outcome: str) -> None:
    repository = RecordingRepository()
    graph = ContextCheckingGraph(fail=outcome == "error")
    service = MigrationService(repository, graph)  # type: ignore[arg-type]
    conversation_id, session_id = uuid4(), uuid4()
    if resume:
        run_id, review_id = uuid4(), uuid4()
        repository.run = FakeRun(conversation_id, uuid4(), run_id, run_id)
        repository.review = SimpleNamespace(
            review_id=review_id,
            conversation_id=conversation_id,
            run_id=run_id,
            payload={"approvable": True},
        )
        stream = service.resume_run(
            conversation_id, session_id, review_id=review_id, decision="approve"
        )
    else:
        stream = service.start_run(conversation_id, session_id, _request())

    caller_context = RunContext.create()
    delivered = []
    consumer_contexts = []
    with instrument_run(caller_context):
        try:
            while True:
                # Gradio advances synchronous generators in separate copied contexts.
                consumer = copy_context()
                consumer_contexts.append(consumer)
                try:
                    envelope = consumer.run(next, stream)
                except StopIteration:
                    assert outcome == "complete"
                    break
                except RuntimeError as exc:
                    assert outcome == "error"
                    assert str(exc) == "graph execution failed"
                    break
                delivered.append(envelope)
                if outcome == "close" and graph.contexts:
                    break
        finally:
            copy_context().run(stream.close)
        assert all(
            consumer.run(current_run_context) == caller_context for consumer in consumer_contexts
        )
        assert current_run_context() == caller_context

    assert current_run_context() is None
    assert graph.closed
    assert repository.run is not None
    assert all(
        context == repository.run.as_context(session_id=session_id) for context in graph.contexts
    )
    if outcome == "complete":
        assert delivered[-1].event["type"] == "run_output"
        assert repository.statuses[-1] is RunStatus.COMPLETED
        assert RunStatus.FAILED not in repository.statuses
    elif outcome == "error":
        assert delivered[-1].event["detail"] == "graph execution failed"
        assert repository.statuses[-1] is RunStatus.FAILED


def test_cross_session_resume_preserves_run_and_uses_current_decision_session() -> None:
    repository = RecordingRepository()
    graph = CompletingGraph()
    service = MigrationService(repository, graph)  # type: ignore[arg-type]
    conversation_id, origin_session, later_session = uuid4(), uuid4(), uuid4()
    run_id = uuid4()
    repository.run = FakeRun(conversation_id, origin_session, run_id, run_id)
    review_id = uuid4()
    repository.review = SimpleNamespace(
        review_id=review_id,
        conversation_id=conversation_id,
        run_id=run_id,
        payload={"approvable": True},
    )

    envelopes = list(
        service.resume_run(
            conversation_id,
            later_session,
            review_id=review_id,
            decision="approve",
            reviewer_feedback="Reviewed in a new browser session.",
        )
    )

    command, config, modes = graph.invocations[0]
    assert command.resume["run_id"] == str(run_id)
    assert command.resume["session_id"] == str(later_session)
    assert command.update == {"session_id": str(later_session)}
    assert config == {"configurable": {"thread_id": str(conversation_id)}}
    assert modes == ["custom", "values"]
    assert repository.decisions[0].session_id == later_session
    assert repository.run.run_id == run_id
    assert all(
        envelope.event.get("session_id", str(later_session)) == str(later_session)
        for envelope in envelopes
    )


def test_duplicate_resume_cannot_reopen_a_decided_review() -> None:
    repository = RecordingRepository()
    graph = CompletingGraph()
    service = MigrationService(repository, graph)  # type: ignore[arg-type]
    conversation_id, session_id, run_id, review_id = uuid4(), uuid4(), uuid4(), uuid4()
    repository.run = FakeRun(conversation_id, session_id, run_id, run_id)
    repository.review = SimpleNamespace(
        review_id=review_id,
        conversation_id=conversation_id,
        run_id=run_id,
        status=ReviewStatus.APPROVED,
    )

    with pytest.raises(ValueError, match="already been recorded"):
        list(
            service.resume_run(
                conversation_id,
                session_id,
                review_id=review_id,
                decision="approve",
            )
        )

    assert graph.invocations == []
    assert repository.decisions == []
    assert RunStatus.ACTIVE not in repository.statuses


def test_resume_failure_releases_claim_instead_of_consuming_review() -> None:
    repository = RecoveringReviewRepository()
    service = MigrationService(repository, FailingResumeGraph())  # type: ignore[arg-type]
    conversation_id, session_id, run_id, review_id = uuid4(), uuid4(), uuid4(), uuid4()
    repository.run = FakeRun(conversation_id, session_id, run_id, run_id)
    repository.review = SimpleNamespace(
        review_id=review_id,
        conversation_id=conversation_id,
        run_id=run_id,
        status=ReviewStatus.PENDING,
        payload={"approvable": True},
    )

    delivered = []
    with pytest.raises(RuntimeError, match="checkpoint temporarily unavailable"):
        for envelope in service.resume_run(
            conversation_id,
            session_id,
            review_id=review_id,
            decision="approve",
        ):
            delivered.append(envelope)

    operation_names = [name for name, _ in repository.operations]
    assert "claim_review_decision" in operation_names
    assert "release_review_decision" in operation_names
    assert RunStatus.FAILED not in repository.statuses
    assert delivered[-1].event["label"] == "Review resume failed"


def test_nonapprovable_review_is_rejected_before_decision_persistence() -> None:
    repository = RecordingRepository()
    graph = CompletingGraph()
    service = MigrationService(repository, graph)  # type: ignore[arg-type]
    conversation_id, session_id, run_id, review_id = uuid4(), uuid4(), uuid4(), uuid4()
    repository.run = FakeRun(conversation_id, session_id, run_id, run_id)
    repository.review = SimpleNamespace(
        review_id=review_id,
        conversation_id=conversation_id,
        run_id=run_id,
        status=ReviewStatus.PENDING,
        payload={"approvable": False},
    )

    with pytest.raises(ValueError, match="cannot be approved"):
        list(
            service.resume_run(
                conversation_id,
                session_id,
                review_id=review_id,
                decision="approve",
            )
        )

    assert repository.decisions == []
    assert graph.invocations == []


def test_final_postgres_failure_never_marks_run_complete() -> None:
    repository = RecordingRepository(fail_final_message=True)
    service = MigrationService(repository, CompletingGraph())  # type: ignore[arg-type]

    delivered: list[Any] = []
    with pytest.raises(RuntimeError, match="final persistence unavailable"):
        for envelope in service.start_run(uuid4(), uuid4(), _request()):
            delivered.append(envelope)

    assert RunStatus.COMPLETED not in repository.statuses
    assert repository.statuses[-1] is RunStatus.FAILED
    assert [message["actor_type"] for message in repository.messages] == [ActorType.USER]
    failure = delivered[-1].event
    assert failure["type"] == "activity"
    assert failure["label"] == "Run failed"
    assert failure["level"] == "error"
    assert all(failure[key] for key in ("conversation_id", "session_id", "run_id"))


def test_human_approved_artifact_preserves_all_known_difference_details(
    tmp_path: Path,
) -> None:
    service = MigrationService(RecordingRepository(), CompletingGraph())  # type: ignore[arg-type]
    artifact_path = tmp_path / "migration.sql"
    artifact_path.write_text("SELECT 1;\n", encoding="utf-8")

    values = service._artifact_values(  # noqa: SLF001 - focused service-boundary test
        {"path": str(artifact_path), "status": "human_approved", "candidate_id": str(uuid4())},
        {
            "validation": {
                "verdict": "human_review",
                "issues": [{"code": "row_mismatch", "message": "Rows differ"}],
                "execution_comparison": {
                    "equivalent": False,
                    "mismatch_samples": [{"kind": "missing", "row": [1]}],
                    "missing_row_count": 1,
                    "extra_row_count": 2,
                    "null_deltas": {"total": 1},
                    "numeric_aggregates": {"old": {"total": {"sum": 1}}},
                },
            }
        },
    )

    assert values is not None
    differences = values["known_differences"]
    assert differences["issues"][0]["code"] == "row_mismatch"
    assert differences["mismatch_samples"][0]["kind"] == "missing"
    assert differences["missing_row_count"] == 1
    assert differences["extra_row_count"] == 2
    assert differences["null_deltas"] == {"total": 1}
