"""Atomic persistence for a completed SchemaShift run."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from schemashift.domain.ids import RunContext
from schemashift.persistence._common import as_record
from schemashift.persistence.errors import PersistenceConflictError
from schemashift.persistence.models import ActorType, ArtifactStatus, RunRecord, RunStatus
from schemashift.persistence.postgres import PostgresDatabase


class FinalizationRepository:
    """Repository mixin that makes externally visible completion all-or-nothing."""

    db: PostgresDatabase

    def finalize_run(
        self,
        context: RunContext,
        *,
        answer: str,
        status: RunStatus | str,
        revision_count: int,
        metrics: dict[str, Any] | None = None,
        output_metadata: dict[str, Any] | None = None,
        artifact: dict[str, Any] | None = None,
    ) -> RunRecord:
        run_status = RunStatus(status)
        if run_status not in {RunStatus.COMPLETED, RunStatus.UNRESOLVED}:
            raise ValueError("finalize_run requires completed or unresolved status")
        if not answer:
            raise ValueError("final assistant response must not be empty")
        if revision_count < 0:
            raise ValueError("revision_count must be non-negative")

        with self.db.transaction() as connection:
            open_run = connection.execute(
                """
                SELECT status FROM runs
                WHERE conversation_id = %s AND run_id = %s
                FOR UPDATE
                """,
                (context.conversation_id, context.run_id),
            ).fetchone()
            if open_run is None:
                raise PersistenceConflictError(f"run {context.run_id} was not found")
            if open_run["status"] != RunStatus.ACTIVE.value:
                raise PersistenceConflictError(
                    f"run {context.run_id} cannot be finalized from {open_run['status']}"
                )

            connection.execute(
                """
                INSERT INTO messages (
                    conversation_id, session_id, run_id, turn_id,
                    actor_type, actor_name, role, content, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    context.conversation_id,
                    context.session_id,
                    context.run_id,
                    context.turn_id,
                    ActorType.AGENT.value,
                    "SchemaShift",
                    "assistant",
                    answer,
                    Jsonb({"status": run_status.value}),
                ),
            )
            connection.execute(
                """
                INSERT INTO agent_events (
                    conversation_id, session_id, run_id, event_type,
                    label, detail, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    context.conversation_id,
                    context.session_id,
                    context.run_id,
                    "run_output",
                    "Run output persisted",
                    run_status.value,
                    Jsonb(output_metadata or {}),
                ),
            )

            if artifact is not None:
                artifact_status = ArtifactStatus(artifact["status"])
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, conversation_id, session_id, run_id,
                        file_name, local_path, artifact_type, status, sha256,
                        known_differences, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        artifact.get("artifact_id") or uuid4(),
                        context.conversation_id,
                        context.session_id,
                        context.run_id,
                        artifact["file_name"],
                        artifact["local_path"],
                        artifact.get("artifact_type", "sql"),
                        artifact_status.value,
                        artifact["sha256"],
                        Jsonb(artifact.get("known_differences") or []),
                        Jsonb(artifact.get("metadata") or {}),
                    ),
                )

            row = connection.execute(
                """
                UPDATE runs
                SET status = %s,
                    revision_count = %s,
                    metrics = metrics || %s,
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE conversation_id = %s AND run_id = %s AND status = 'active'
                RETURNING *
                """,
                (
                    run_status.value,
                    revision_count,
                    Jsonb(metrics or {}),
                    context.conversation_id,
                    context.run_id,
                ),
            ).fetchone()
            if row is None:  # pragma: no cover - row lock makes this purely defensive
                raise PersistenceConflictError(f"run {context.run_id} changed while finalizing")
            connection.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE conversation_id = %s",
                (context.conversation_id,),
            )
        return as_record(RunRecord, row, f"run {context.run_id}")


__all__ = ["FinalizationRepository"]
