"""Persistence mapping for typed runtime events."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from schemashift.domain.events import (
    ActivityEvent,
    Component,
    ComponentStatus,
    ComponentStatusEvent,
    HumanReviewRequiredEvent,
    RuntimeEvent,
)
from schemashift.domain.ids import RunContext
from schemashift.persistence._common import as_record, page_size
from schemashift.persistence.models import AgentEventRecord
from schemashift.persistence.postgres import PostgresDatabase


class EventRepository:
    db: PostgresDatabase

    def add_event(self, event: RuntimeEvent) -> AgentEventRecord:
        if isinstance(event, ComponentStatusEvent):
            component: Component | None = event.component
            status: ComponentStatus | None = event.status
            label = None
            detail = event.detail
            metadata: dict[str, Any] = {}
        elif isinstance(event, ActivityEvent):
            component = None
            status = None
            label = event.label
            detail = event.detail
            metadata = {"level": event.level.value, **event.metadata}
        elif isinstance(event, HumanReviewRequiredEvent):
            component = Component.GUARDRAIL
            status = ComponentStatus.ACTIVE
            label = event.title
            detail = event.reason
            metadata = {
                "review_id": str(event.review_id),
                "candidate_id": str(event.candidate_id),
                "risk": event.risk.value,
                "payload": event.payload,
            }
        else:  # pragma: no cover - closed typed union, defensive at runtime
            raise TypeError(f"unsupported runtime event: {type(event)!r}")

        context = RunContext(
            conversation_id=event.conversation_id,
            session_id=event.session_id,
            run_id=event.run_id,
        )
        return self.add_raw_event(
            context,
            event_type=event.type,
            component=component,
            status=status,
            label=label,
            detail=detail,
            metadata=metadata,
            created_at=event.timestamp,
            dedupe_review_id=event.review_id
            if isinstance(event, HumanReviewRequiredEvent)
            else None,
        )

    def add_raw_event(
        self,
        context: RunContext,
        *,
        event_type: str,
        component: Component | str | None = None,
        status: ComponentStatus | str | None = None,
        label: str | None = None,
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: Any | None = None,
        dedupe_review_id: UUID | None = None,
    ) -> AgentEventRecord:
        component_value = Component(component).value if component is not None else None
        status_value = ComponentStatus(status).value if status is not None else None
        if not event_type.strip():
            raise ValueError("event_type must not be blank")
        event_metadata = metadata or {}
        with self.db.transaction() as connection:
            if dedupe_review_id is None:
                row = connection.execute(
                    """
                    INSERT INTO agent_events (
                        conversation_id, session_id, run_id, event_type,
                        component, status, label, detail, metadata, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()))
                    RETURNING *
                    """,
                    (
                        context.conversation_id,
                        context.session_id,
                        context.run_id,
                        event_type,
                        component_value,
                        status_value,
                        label,
                        detail,
                        Jsonb(event_metadata),
                        created_at,
                    ),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    INSERT INTO agent_events (
                        conversation_id, session_id, run_id, event_type,
                        component, status, label, detail, metadata, created_at
                    )
                    SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW())
                    WHERE NOT EXISTS (
                        SELECT 1 FROM agent_events
                        WHERE event_type = 'human_review_required'
                          AND metadata ->> 'review_id' = %s
                    )
                    ON CONFLICT DO NOTHING
                    RETURNING *
                    """,
                    (
                        context.conversation_id,
                        context.session_id,
                        context.run_id,
                        event_type,
                        component_value,
                        status_value,
                        label,
                        detail,
                        Jsonb(event_metadata),
                        created_at,
                        str(dedupe_review_id),
                    ),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        """
                        SELECT * FROM agent_events
                        WHERE event_type = 'human_review_required'
                          AND metadata ->> 'review_id' = %s
                        ORDER BY event_id
                        LIMIT 1
                        """,
                        (str(dedupe_review_id),),
                    ).fetchone()
            connection.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE conversation_id = %s",
                (context.conversation_id,),
            )
        return as_record(AgentEventRecord, row, "new agent event")

    def list_events(
        self,
        conversation_id: UUID,
        *,
        run_id: UUID | None = None,
        limit: int = 1_000,
        ascending: bool = True,
    ) -> list[AgentEventRecord]:
        limit = page_size(limit, maximum=10_000)
        order = "ASC" if ascending else "DESC"
        if run_id is None:
            query = f"""
                SELECT * FROM agent_events
                WHERE conversation_id = %s
                ORDER BY created_at {order}, event_id {order}
                LIMIT %s
            """
            parameters = (conversation_id, limit)
        else:
            query = f"""
                SELECT * FROM agent_events
                WHERE conversation_id = %s AND run_id = %s
                ORDER BY created_at {order}, event_id {order}
                LIMIT %s
            """
            parameters = (conversation_id, run_id, limit)
        with self.db.transaction() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [AgentEventRecord.model_validate(row) for row in rows]
