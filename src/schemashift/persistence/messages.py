"""Durable chat, subagent, and paired tool-interaction history."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from schemashift.domain.ids import RunContext
from schemashift.persistence._common import as_record, page_size
from schemashift.persistence.models import ActorType, MessageRecord
from schemashift.persistence.postgres import PostgresDatabase


class MessageRepository:
    db: PostgresDatabase

    @staticmethod
    def _insert_message(
        connection: Any,
        context: RunContext,
        *,
        actor_type: ActorType,
        content: str,
        actor_name: str | None,
        role: str | None,
        tool_name: str | None,
        tool_call_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> MessageRecord:
        row = connection.execute(
            """
            INSERT INTO messages (
                conversation_id, session_id, run_id, turn_id,
                actor_type, actor_name, role, content,
                tool_name, tool_call_id, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                context.conversation_id,
                context.session_id,
                context.run_id,
                context.turn_id,
                actor_type.value,
                actor_name,
                role,
                content,
                tool_name,
                tool_call_id,
                Jsonb(metadata or {}),
            ),
        ).fetchone()
        return as_record(MessageRecord, row, "new message")

    def add_message(
        self,
        context: RunContext,
        *,
        actor_type: ActorType | str,
        content: str,
        actor_name: str | None = None,
        role: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MessageRecord:
        actor = ActorType(actor_type)
        if actor is not ActorType.TOOL and (tool_name is not None or tool_call_id is not None):
            raise ValueError("tool_name and tool_call_id are valid only for tool messages")
        if actor is ActorType.TOOL and (not tool_name or not tool_call_id):
            raise ValueError("tool messages require tool_name and tool_call_id")
        with self.db.transaction() as connection:
            record = self._insert_message(
                connection,
                context,
                actor_type=actor,
                content=content,
                actor_name=actor_name,
                role=role,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                metadata=metadata,
            )
            connection.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE conversation_id = %s",
                (context.conversation_id,),
            )
        return record

    def add_tool_interaction(
        self,
        context: RunContext,
        *,
        tool_name: str,
        tool_call_id: str,
        request_content: str,
        result_content: str,
        actor_name: str | None = None,
        request_metadata: dict[str, Any] | None = None,
        result_metadata: dict[str, Any] | None = None,
    ) -> tuple[MessageRecord, MessageRecord]:
        if not tool_name.strip() or not tool_call_id.strip():
            raise ValueError("tool_name and tool_call_id must not be blank")
        with self.db.transaction() as connection:
            request = self._insert_message(
                connection,
                context,
                actor_type=ActorType.TOOL,
                content=request_content,
                actor_name=actor_name,
                role="tool_call",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                metadata=request_metadata,
            )
            result = self._insert_message(
                connection,
                context,
                actor_type=ActorType.TOOL,
                content=result_content,
                actor_name=actor_name,
                role="tool_result",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                metadata=result_metadata,
            )
            connection.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE conversation_id = %s",
                (context.conversation_id,),
            )
        return request, result

    def get_message(self, message_id: int) -> MessageRecord | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE message_id = %s",
                (message_id,),
            ).fetchone()
        return MessageRecord.model_validate(row) if row else None

    def list_messages(
        self,
        conversation_id: UUID,
        *,
        session_id: UUID | None = None,
        run_id: UUID | None = None,
        limit: int = 500,
        ascending: bool = True,
    ) -> list[MessageRecord]:
        limit = page_size(limit, maximum=10_000)
        conditions = ["conversation_id = %s"]
        parameters: list[Any] = [conversation_id]
        if session_id is not None:
            conditions.append("session_id = %s")
            parameters.append(session_id)
        if run_id is not None:
            conditions.append("run_id = %s")
            parameters.append(run_id)
        order = "ASC" if ascending else "DESC"
        parameters.append(limit)
        query = f"""
            SELECT * FROM messages
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at {order}, message_id {order}
            LIMIT %s
        """
        with self.db.transaction() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [MessageRecord.model_validate(row) for row in rows]
