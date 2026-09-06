"""Durable run lifecycle and streamed LangGraph event delivery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from schemashift.domain import (
    ActivityEvent,
    Component,
    ComponentStatus,
    ComponentStatusEvent,
    HumanDecision,
    HumanReviewRequiredEvent,
    RunContext,
    RuntimeEvent,
)
from schemashift.persistence import (
    ActorType,
    ArtifactStatus,
    PostgresRepository,
    ReviewStatus,
    RunStatus,
)

from .ingestion import sha256_file
from .instrumentation import instrument_run

_EVENT_ADAPTER = TypeAdapter(RuntimeEvent)


class MigrationRequest(BaseModel):
    """Explicit controlled inputs for one migration run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: str = Field(min_length=1)
    original_sql: str = Field(min_length=1)
    old_schema_source_id: str = Field(min_length=1)
    new_schema_source_id: str = Field(min_length=1)
    old_database_id: str = Field(min_length=1)
    new_database_id: str = Field(min_length=1)
    comparison_policy: dict[str, Any] = Field(default_factory=dict)
    knowledge_document_ids: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StreamEnvelope:
    """A persisted event ready for UI delivery."""

    event: dict[str, Any]
    persisted: bool = True


def _event_dict(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    return dict(event)


class MigrationService:
    """Coordinate PostgreSQL, checkpointed graphs, and streaming consumers."""

    def __init__(self, repository: PostgresRepository, graph: Any) -> None:
        self.repository = repository
        self.graph = graph

    def create_conversation(self, title: str | None = None) -> tuple[UUID, UUID]:
        conversation = self.repository.create_conversation(title=title)
        session = self.repository.create_session(conversation.conversation_id)
        return conversation.conversation_id, session.session_id

    def open_session(self, conversation_id: UUID) -> UUID:
        if self.repository.get_conversation(conversation_id) is None:
            raise ValueError(f"Unknown conversation: {conversation_id}")
        return self.repository.create_session(conversation_id).session_id

    def reconstruct_conversation(self, conversation_id: UUID) -> dict[str, Any]:
        conversation = self.repository.get_conversation(conversation_id)
        if conversation is None:
            raise ValueError(f"Unknown conversation: {conversation_id}")
        pending = self.repository.get_pending_review(conversation_id)
        return {
            "conversation": conversation.model_dump(mode="json"),
            "messages": [
                item.model_dump(mode="json")
                for item in self.repository.list_messages(conversation_id)
                if item.role in {None, "user", "assistant"}
                or item.actor_type in {ActorType.USER, ActorType.AGENT}
                and item.role != "model_result"
            ],
            "events": [
                item.model_dump(mode="json")
                for item in self.repository.list_events(conversation_id)
            ],
            "runs": [
                item.model_dump(mode="json") for item in self.repository.list_runs(conversation_id)
            ],
            "sources": [
                item.model_dump(mode="json")
                for item in self.repository.list_sources(conversation_id)
            ],
            "artifacts": [
                item.model_dump(mode="json")
                for item in self.repository.list_artifacts(conversation_id)
            ],
            "pending_review": pending.model_dump(mode="json") if pending else None,
        }

    def _persist_runtime_event(self, payload: dict[str, Any]) -> RuntimeEvent:
        event = _EVENT_ADAPTER.validate_python(payload)
        self.repository.add_event(event)
        if isinstance(event, HumanReviewRequiredEvent):
            existing = self.repository.get_review(event.review_id)
            if existing is None or existing.status.value == "pending":
                self.repository.create_review(event)
        return event

    def _reset_events(self, context: RunContext) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for name in Component:
            event = ComponentStatusEvent(
                conversation_id=context.conversation_id,
                session_id=context.session_id,
                run_id=context.run_id,
                component=name,
                status=ComponentStatus.IDLE,
                detail="Ready",
            )
            self.repository.add_event(event)
            values.append(event.model_dump(mode="json"))
        return values

    def _artifact_values(
        self,
        artifact_event: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        raw_path = str(artifact_event.get("path") or "")
        if not raw_path:
            return None
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Graph artifact was not written: {path}")
        status = ArtifactStatus(str(artifact_event.get("status", "validated")))
        validation = state.get("validation") or {}
        comparison = validation.get("execution_comparison") or {}
        known_differences: list[Any] | dict[str, Any] = []
        if status is ArtifactStatus.HUMAN_APPROVED and not comparison.get("equivalent", False):
            known_differences = {
                "issues": list(validation.get("issues") or []),
                "mismatch_samples": list(comparison.get("mismatch_samples") or []),
                "missing_row_count": int(comparison.get("missing_row_count", 0) or 0),
                "extra_row_count": int(comparison.get("extra_row_count", 0) or 0),
                "duplicate_keys": list(comparison.get("duplicate_keys") or []),
                "null_deltas": dict(comparison.get("null_deltas") or {}),
                "numeric_aggregates": dict(comparison.get("numeric_aggregates") or {}),
                "errors": list(comparison.get("errors") or []),
            }
        return {
            "file_name": path.name,
            "local_path": str(path),
            "sha256": sha256_file(path),
            "status": status,
            "known_differences": known_differences,
            "metadata": {
                "candidate_id": artifact_event.get("candidate_id"),
                "validation_verdict": validation.get("verdict"),
            },
        }

    def _persist_artifact(
        self,
        context: RunContext,
        artifact_event: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        values = self._artifact_values(artifact_event, state)
        if values is not None:
            self.repository.create_artifact(context, **values)

    def _finalize(
        self,
        context: RunContext,
        output: dict[str, Any],
        state: dict[str, Any],
        artifact_event: dict[str, Any] | None,
    ) -> None:
        answer = str(output.get("answer") or state.get("answer") or "")
        if not answer:
            raise ValueError("Graph completed without an assistant response")
        graph_status = str(output.get("status", "completed"))
        run_status = RunStatus.UNRESOLVED if graph_status == "unresolved" else RunStatus.COMPLETED
        metrics = dict(output.get("metrics") or {})
        revision_count = int(metrics.get("revision_count", state.get("revision_count", 0)))
        artifact = self._artifact_values(artifact_event, state) if artifact_event else None
        finalizer = getattr(self.repository, "finalize_run", None)
        if callable(finalizer):
            finalizer(
                context,
                answer=answer,
                status=run_status,
                revision_count=revision_count,
                metrics=metrics,
                output_metadata={
                    "artifact_path": output.get("artifact_path", ""),
                    "metrics": metrics,
                },
                artifact=artifact,
            )
            return

        # Compatibility path for narrow test doubles; the real PostgreSQL facade
        # implements the single-transaction operation above.
        self.repository.add_message(
            context,
            actor_type=ActorType.AGENT,
            actor_name="SchemaShift",
            role="assistant",
            content=answer,
            metadata={"status": output.get("status", "completed")},
        )
        self.repository.add_raw_event(
            context,
            event_type="run_output",
            label="Run output persisted",
            detail=str(output.get("status", "completed")),
            metadata={
                "artifact_path": output.get("artifact_path", ""),
                "metrics": output.get("metrics") or {},
            },
        )
        if artifact is not None:
            self.repository.create_artifact(context, **artifact)
        self.repository.update_run_status(
            context.run_id,
            run_status,
            conversation_id=context.conversation_id,
            revision_count=revision_count,
            metrics_patch=metrics,
        )

    def _stream_graph(
        self,
        context: RunContext,
        graph_input: dict[str, Any] | Command,
    ):
        config = {"configurable": {"thread_id": str(context.conversation_id)}}
        latest_state: dict[str, Any] = {}
        artifact_event: dict[str, Any] | None = None
        finalized = False
        review_seen = False
        with instrument_run(context):
            stream = self.graph.stream(
                graph_input,
                config=config,
                stream_mode=["custom", "values"],
            )
            for item in stream:
                if isinstance(item, tuple) and len(item) == 2:
                    mode, chunk = item
                else:
                    mode, chunk = "custom", item
                if mode == "values":
                    if isinstance(chunk, dict):
                        latest_state = {
                            key: value for key, value in chunk.items() if key != "__interrupt__"
                        }
                    continue
                if not isinstance(chunk, dict):
                    continue
                payload = dict(chunk)
                event_type = str(payload.get("type", ""))
                if event_type in {
                    "component_status",
                    "activity",
                    "human_review_required",
                }:
                    persisted = self._persist_runtime_event(payload)
                    review_seen = review_seen or isinstance(persisted, HumanReviewRequiredEvent)
                    yield StreamEnvelope(_event_dict(persisted))
                elif event_type == "assistant_token":
                    # Deliberately streamed but never persisted token-by-token.
                    yield StreamEnvelope(payload, persisted=False)
                elif event_type == "artifact_created":
                    artifact_event = payload
                    self.repository.add_raw_event(
                        context,
                        event_type="artifact_created",
                        label="Migration artifact written",
                        detail=str(payload.get("status", "")),
                        metadata={
                            key: value
                            for key, value in payload.items()
                            if key not in {"conversation_id", "session_id", "run_id"}
                        },
                    )
                    yield StreamEnvelope(payload)
                elif event_type == "run_output":
                    self._finalize(context, payload, latest_state, artifact_event)
                    finalized = True
                    yield StreamEnvelope(payload)
                else:
                    self.repository.add_raw_event(
                        context,
                        event_type=event_type or "graph_event",
                        detail=str(payload.get("detail", "")),
                        metadata=payload,
                    )
                    yield StreamEnvelope(payload)
        if review_seen and not finalized:
            self.repository.update_run_status(
                context.run_id,
                RunStatus.WAITING_HUMAN,
                conversation_id=context.conversation_id,
                revision_count=int(latest_state.get("revision_count", 0)),
            )
        elif not finalized:
            raise RuntimeError("Graph stream ended without a final output or human interrupt")

    def start_run(
        self,
        conversation_id: UUID,
        session_id: UUID,
        migration: MigrationRequest,
    ):
        context = RunContext.create(
            conversation_id=conversation_id,
            session_id=session_id,
        )
        self.repository.create_run(context, status=RunStatus.ACTIVE)
        try:
            self.repository.add_message(
                context,
                actor_type=ActorType.USER,
                actor_name="User",
                role="user",
                content=migration.request,
                metadata={"original_sql": migration.original_sql},
            )
            for reset in self._reset_events(context):
                yield StreamEnvelope(reset)
            graph_input = {
                **context.correlation(),
                **migration.model_dump(mode="json"),
            }
            yield from self._stream_graph(context, graph_input)
        except Exception as exc:
            failure = ActivityEvent(
                conversation_id=context.conversation_id,
                session_id=context.session_id,
                run_id=context.run_id,
                level="error",
                label="Run failed",
                detail=str(exc),
            )
            self.repository.add_event(failure)
            self.repository.update_run_status(
                context.run_id,
                RunStatus.FAILED,
                conversation_id=context.conversation_id,
                error_detail=str(exc),
            )
            # The failure is durable before the UI receives it.
            yield StreamEnvelope(_event_dict(failure))
            raise

    def resume_run(
        self,
        conversation_id: UUID,
        session_id: UUID,
        *,
        review_id: UUID,
        decision: Literal["approve", "reject"],
        reviewer_feedback: str | None = None,
    ):
        review = self.repository.get_review(review_id)
        if review is None or review.conversation_id != conversation_id:
            raise ValueError("Pending review was not found in this conversation")
        run = self.repository.get_run(review.run_id, conversation_id=conversation_id)
        if run is None:
            raise ValueError("The reviewed run no longer exists")
        review_status = getattr(review, "status", ReviewStatus.PENDING)
        if review_status != ReviewStatus.PENDING:
            raise ValueError("This Human Decision has already been recorded")
        run_status = getattr(run, "status", RunStatus.WAITING_HUMAN)
        if run_status != RunStatus.WAITING_HUMAN:
            raise ValueError("The reviewed run is not waiting for a Human Decision")
        review_payload = getattr(review, "payload", {})
        if decision == "approve" and not bool(review_payload.get("approvable", False)):
            raise ValueError(
                "This candidate cannot be approved because deterministic parse, schema, "
                "read-only, and execution checks have not all passed"
            )
        context = run.as_context(session_id=session_id)
        human_decision = HumanDecision(
            decision=decision,
            review_id=review_id,
            run_id=run.run_id,
            session_id=session_id,
            reviewer_feedback=reviewer_feedback,
        )
        claim = getattr(self.repository, "claim_review_decision", None)
        decision_message_persisted = False
        decision_claimed = False
        try:
            if callable(claim):
                claim(human_decision)
                decision_message_persisted = True
            else:  # narrow test doubles and adapters without the atomic repository API
                self.repository.decide_review(human_decision)
                self.repository.update_run_status(
                    run.run_id,
                    RunStatus.ACTIVE,
                    conversation_id=conversation_id,
                )
            decision_claimed = True
            if not decision_message_persisted:
                self.repository.add_message(
                    context,
                    actor_type=ActorType.USER,
                    actor_name="Reviewer",
                    role="human_decision",
                    content=decision,
                    metadata={
                        "review_id": str(review_id),
                        "reviewer_feedback": reviewer_feedback,
                    },
                )
            command = Command(
                resume=human_decision.model_dump(mode="json"),
                update={"session_id": str(session_id)},
            )
            yield from self._stream_graph(context, command)
        except Exception as exc:
            failure = ActivityEvent(
                conversation_id=conversation_id,
                session_id=session_id,
                run_id=run.run_id,
                level="error",
                label="Review resume failed",
                detail=str(exc),
            )
            self.repository.add_event(failure)
            released = False
            release = getattr(self.repository, "release_review_decision", None)
            if decision_claimed and callable(release):
                try:
                    release(human_decision, error_detail=str(exc))
                    released = True
                except Exception:
                    released = False
            if decision_claimed and not released:
                self.repository.update_run_status(
                    run.run_id,
                    RunStatus.FAILED,
                    conversation_id=conversation_id,
                    error_detail=str(exc),
                )
            yield StreamEnvelope(_event_dict(failure))
            raise


__all__ = ["MigrationRequest", "MigrationService", "StreamEnvelope"]
