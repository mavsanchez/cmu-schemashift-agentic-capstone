"""Conversation, UI-session, and graph-run history operations."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from psycopg import errors
from psycopg.types.json import Jsonb

from schemashift.domain.ids import RunContext
from schemashift.persistence._common import as_record, page_size
from schemashift.persistence.errors import (
    ActiveRunExistsError,
    PersistenceConflictError,
)
from schemashift.persistence.models import (
    ConversationRecord,
    RunRecord,
    RunStatus,
    SessionRecord,
)
from schemashift.persistence.postgres import PostgresDatabase


class ConversationRepository:
    """Repository mixin for durable conversation/run lifecycle state."""

    db: PostgresDatabase

    def create_conversation(
        self,
        title: str | None = None,
        *,
        conversation_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationRecord:
        identifier = conversation_id or uuid4()
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                INSERT INTO conversations (conversation_id, title, metadata)
                VALUES (%s, %s, %s)
                ON CONFLICT (conversation_id) DO NOTHING
                RETURNING *
                """,
                (identifier, title, Jsonb(metadata or {})),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM conversations WHERE conversation_id = %s",
                    (identifier,),
                ).fetchone()
        return as_record(ConversationRecord, row, f"conversation {identifier}")

    def get_conversation(self, conversation_id: UUID) -> ConversationRecord | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = %s",
                (conversation_id,),
            ).fetchone()
        return ConversationRecord.model_validate(row) if row else None

    def list_conversations(self, *, limit: int = 100, offset: int = 0) -> list[ConversationRecord]:
        limit = page_size(limit)
        if offset < 0:
            raise ValueError("offset must be non-negative")
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversations
                ORDER BY updated_at DESC, conversation_id
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            ).fetchall()
        return [ConversationRecord.model_validate(row) for row in rows]

    def update_conversation(
        self,
        conversation_id: UUID,
        *,
        title: str | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> ConversationRecord:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                UPDATE conversations
                SET title = COALESCE(%s, title),
                    metadata = metadata || %s,
                    updated_at = NOW()
                WHERE conversation_id = %s
                RETURNING *
                """,
                (title, Jsonb(metadata_patch or {}), conversation_id),
            ).fetchone()
        return as_record(ConversationRecord, row, f"conversation {conversation_id}")

    def create_session(
        self,
        conversation_id: UUID,
        *,
        session_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        identifier = session_id or uuid4()
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                INSERT INTO sessions (session_id, conversation_id, metadata)
                VALUES (%s, %s, %s)
                ON CONFLICT (session_id) DO NOTHING
                RETURNING *
                """,
                (identifier, conversation_id, Jsonb(metadata or {})),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM sessions WHERE session_id = %s",
                    (identifier,),
                ).fetchone()
                if row and row["conversation_id"] != conversation_id:
                    raise PersistenceConflictError(
                        f"session {identifier} already belongs to another conversation"
                    )
            connection.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE conversation_id = %s",
                (conversation_id,),
            )
        return as_record(SessionRecord, row, f"session {identifier}")

    def get_session(self, session_id: UUID) -> SessionRecord | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = %s",
                (session_id,),
            ).fetchone()
        return SessionRecord.model_validate(row) if row else None

    def end_session(self, session_id: UUID) -> SessionRecord:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                UPDATE sessions
                SET ended_at = COALESCE(ended_at, NOW())
                WHERE session_id = %s
                RETURNING *
                """,
                (session_id,),
            ).fetchone()
        return as_record(SessionRecord, row, f"session {session_id}")

    def create_run(
        self,
        context: RunContext,
        *,
        status: RunStatus | str = RunStatus.ACTIVE,
        metrics: dict[str, Any] | None = None,
    ) -> RunRecord:
        run_status = RunStatus(status)
        if run_status in {RunStatus.COMPLETED, RunStatus.UNRESOLVED, RunStatus.FAILED}:
            raise ValueError("a new run cannot start in a terminal status")
        try:
            with self.db.transaction() as connection:
                row = connection.execute(
                    """
                    INSERT INTO runs (
                        run_id, conversation_id, origin_session_id, turn_id, status, metrics,
                        waiting_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s = 'waiting_human' THEN NOW() END)
                    ON CONFLICT (run_id) DO NOTHING
                    RETURNING *
                    """,
                    (
                        context.run_id,
                        context.conversation_id,
                        context.session_id,
                        context.turn_id,
                        run_status.value,
                        Jsonb(metrics or {}),
                        run_status.value,
                    ),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        "SELECT * FROM runs WHERE run_id = %s",
                        (context.run_id,),
                    ).fetchone()
                    if row and (
                        row["conversation_id"] != context.conversation_id
                        or row["origin_session_id"] != context.session_id
                        or row["turn_id"] != context.turn_id
                    ):
                        raise PersistenceConflictError(
                            f"run {context.run_id} conflicts with an existing run"
                        )
                connection.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE conversation_id = %s",
                    (context.conversation_id,),
                )
        except errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "uq_runs_one_open_per_conversation":
                raise ActiveRunExistsError(
                    f"conversation {context.conversation_id} already has an open run"
                ) from exc
            raise PersistenceConflictError("run could not be created") from exc
        return as_record(RunRecord, row, f"run {context.run_id}")

    def get_run(
        self,
        run_id: UUID,
        *,
        conversation_id: UUID | None = None,
    ) -> RunRecord | None:
        sql = "SELECT * FROM runs WHERE run_id = %s"
        params: tuple[Any, ...] = (run_id,)
        if conversation_id is not None:
            sql += " AND conversation_id = %s"
            params += (conversation_id,)
        with self.db.transaction() as connection:
            row = connection.execute(sql, params).fetchone()
        return RunRecord.model_validate(row) if row else None

    def get_open_run(self, conversation_id: UUID) -> RunRecord | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM runs
                WHERE conversation_id = %s
                  AND status IN ('pending', 'active', 'waiting_human')
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        return RunRecord.model_validate(row) if row else None

    def list_runs(self, conversation_id: UUID, *, limit: int = 100) -> list[RunRecord]:
        limit = page_size(limit)
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runs
                WHERE conversation_id = %s
                ORDER BY started_at DESC, run_id
                LIMIT %s
                """,
                (conversation_id, limit),
            ).fetchall()
        return [RunRecord.model_validate(row) for row in rows]

    def update_run_status(
        self,
        run_id: UUID,
        status: RunStatus | str,
        *,
        conversation_id: UUID | None = None,
        revision_count: int | None = None,
        metrics_patch: dict[str, Any] | None = None,
        error_detail: str | None = None,
    ) -> RunRecord:
        run_status = RunStatus(status)
        if revision_count is not None and revision_count < 0:
            raise ValueError("revision_count must be non-negative")
        terminal = run_status in {
            RunStatus.COMPLETED,
            RunStatus.UNRESOLVED,
            RunStatus.FAILED,
        }
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                UPDATE runs
                SET status = %s,
                    revision_count = COALESCE(%s, revision_count),
                    metrics = metrics || %s,
                    error_detail = COALESCE(%s, error_detail),
                    waiting_at = CASE
                        WHEN %s = 'waiting_human' THEN COALESCE(waiting_at, NOW())
                        ELSE waiting_at
                    END,
                    completed_at = CASE
                        WHEN %s THEN COALESCE(completed_at, NOW())
                        ELSE NULL
                    END,
                    updated_at = NOW()
                WHERE run_id = %s
                  AND (%s::uuid IS NULL OR conversation_id = %s::uuid)
                RETURNING *
                """,
                (
                    run_status.value,
                    revision_count,
                    Jsonb(metrics_patch or {}),
                    error_detail,
                    run_status.value,
                    terminal,
                    run_id,
                    conversation_id,
                    conversation_id,
                ),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE conversation_id = %s",
                    (row["conversation_id"],),
                )
        return as_record(RunRecord, row, f"run {run_id}")

    def increment_revision(self, run_id: UUID, *, maximum: int | None = None) -> RunRecord:
        if maximum is not None and maximum < 1:
            raise ValueError("maximum must be positive")
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                UPDATE runs
                SET revision_count = revision_count + 1,
                    updated_at = NOW()
                WHERE run_id = %s
                  AND (%s::integer IS NULL OR revision_count < %s::integer)
                RETURNING *
                """,
                (run_id, maximum, maximum),
            ).fetchone()
            if row is None and maximum is not None:
                existing = connection.execute(
                    "SELECT revision_count FROM runs WHERE run_id = %s",
                    (run_id,),
                ).fetchone()
                if existing and existing["revision_count"] >= maximum:
                    raise PersistenceConflictError(
                        f"run {run_id} reached its revision limit of {maximum}"
                    )
        return as_record(RunRecord, row, f"run {run_id}")
