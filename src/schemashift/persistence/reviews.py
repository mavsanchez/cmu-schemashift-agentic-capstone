"""Durable, idempotent human-review requests and decisions."""

from __future__ import annotations

from uuid import UUID

from psycopg import errors
from psycopg.types.json import Jsonb

from schemashift.domain.events import HumanDecision, HumanReviewRequiredEvent
from schemashift.persistence._common import as_record, page_size
from schemashift.persistence.errors import PersistenceConflictError
from schemashift.persistence.models import HumanReviewRecord, ReviewStatus
from schemashift.persistence.postgres import PostgresDatabase


class ReviewRepository:
    db: PostgresDatabase

    def create_review(self, event: HumanReviewRequiredEvent) -> HumanReviewRecord:
        """Persist an interrupt request once, even if its graph node restarts."""

        try:
            with self.db.transaction() as connection:
                row = connection.execute(
                    """
                    INSERT INTO human_reviews (
                        review_id, candidate_id, conversation_id, run_id,
                        requested_session_id, title, reason, risk, payload, requested_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (review_id) DO NOTHING
                    RETURNING *
                    """,
                    (
                        event.review_id,
                        event.candidate_id,
                        event.conversation_id,
                        event.run_id,
                        event.session_id,
                        event.title,
                        event.reason,
                        event.risk.value,
                        Jsonb(event.payload),
                        event.timestamp,
                    ),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        "SELECT * FROM human_reviews WHERE review_id = %s",
                        (event.review_id,),
                    ).fetchone()
                    if row and (
                        row["candidate_id"] != event.candidate_id
                        or row["conversation_id"] != event.conversation_id
                        or row["run_id"] != event.run_id
                    ):
                        raise PersistenceConflictError(
                            f"review {event.review_id} conflicts with an existing request"
                        )
                run_row = connection.execute(
                    """
                    UPDATE runs
                    SET status = 'waiting_human',
                        waiting_at = COALESCE(waiting_at, NOW()),
                        updated_at = NOW()
                    WHERE conversation_id = %s
                      AND run_id = %s
                      AND status IN ('pending', 'active', 'waiting_human')
                    RETURNING run_id
                    """,
                    (event.conversation_id, event.run_id),
                ).fetchone()
                if run_row is None:
                    raise PersistenceConflictError(
                        f"run {event.run_id} is not open for human review"
                    )
        except errors.UniqueViolation as exc:
            raise PersistenceConflictError(
                f"candidate {event.candidate_id} already has a review request"
            ) from exc
        return as_record(HumanReviewRecord, row, f"review {event.review_id}")

    def get_review(self, review_id: UUID) -> HumanReviewRecord | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM human_reviews WHERE review_id = %s",
                (review_id,),
            ).fetchone()
        return HumanReviewRecord.model_validate(row) if row else None

    def get_pending_review(self, conversation_id: UUID) -> HumanReviewRecord | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM human_reviews
                WHERE conversation_id = %s AND status = 'pending'
                ORDER BY requested_at DESC, review_id
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        return HumanReviewRecord.model_validate(row) if row else None

    def list_reviews(
        self,
        conversation_id: UUID,
        *,
        status: ReviewStatus | str | None = None,
        limit: int = 100,
    ) -> list[HumanReviewRecord]:
        limit = page_size(limit)
        if status is None:
            query = """
                SELECT * FROM human_reviews
                WHERE conversation_id = %s
                ORDER BY requested_at DESC, review_id
                LIMIT %s
            """
            parameters = (conversation_id, limit)
        else:
            query = """
                SELECT * FROM human_reviews
                WHERE conversation_id = %s AND status = %s
                ORDER BY requested_at DESC, review_id
                LIMIT %s
            """
            parameters = (conversation_id, ReviewStatus(status).value, limit)
        with self.db.transaction() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [HumanReviewRecord.model_validate(row) for row in rows]

    def decide_review(self, decision: HumanDecision) -> HumanReviewRecord:
        """Record one decision; identical retries return the original decision."""

        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM human_reviews WHERE review_id = %s FOR UPDATE",
                (decision.review_id,),
            ).fetchone()
            record = as_record(
                HumanReviewRecord,
                existing,
                f"review {decision.review_id}",
            )
            if record.run_id != decision.run_id:
                raise PersistenceConflictError(
                    f"review {decision.review_id} does not belong to run {decision.run_id}"
                )
            if record.status is not ReviewStatus.PENDING:
                if record.decision == decision.decision:
                    return record
                raise PersistenceConflictError(
                    f"review {decision.review_id} already has a different decision"
                )

            persisted_status = (
                ReviewStatus.APPROVED
                if decision.decision.value == "approve"
                else ReviewStatus.REJECTED
            )
            row = connection.execute(
                """
                UPDATE human_reviews
                SET status = %s,
                    decision = %s,
                    decision_session_id = %s,
                    reviewer_feedback = %s,
                    decided_at = %s
                WHERE review_id = %s AND status = 'pending'
                RETURNING *
                """,
                (
                    persisted_status.value,
                    decision.decision.value,
                    decision.session_id,
                    decision.reviewer_feedback,
                    decision.decided_at,
                    decision.review_id,
                ),
            ).fetchone()
        return as_record(HumanReviewRecord, row, f"review {decision.review_id}")

    def claim_review_decision(self, decision: HumanDecision) -> HumanReviewRecord:
        """Atomically claim one pending interrupt and reactivate its waiting run.

        Unlike :meth:`decide_review`, this service-facing operation is exactly
        once: a duplicate resume must never replay a LangGraph ``Command`` or
        move a terminal run back to ``active``.
        """

        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM human_reviews WHERE review_id = %s FOR UPDATE",
                (decision.review_id,),
            ).fetchone()
            record = as_record(
                HumanReviewRecord,
                existing,
                f"review {decision.review_id}",
            )
            if record.run_id != decision.run_id:
                raise PersistenceConflictError(
                    f"review {decision.review_id} does not belong to run {decision.run_id}"
                )
            if record.status is not ReviewStatus.PENDING:
                raise PersistenceConflictError(
                    f"review {decision.review_id} has already been resumed"
                )

            persisted_status = (
                ReviewStatus.APPROVED
                if decision.decision.value == "approve"
                else ReviewStatus.REJECTED
            )
            row = connection.execute(
                """
                UPDATE human_reviews
                SET status = %s,
                    decision = %s,
                    decision_session_id = %s,
                    reviewer_feedback = %s,
                    decided_at = %s
                WHERE review_id = %s AND status = 'pending'
                RETURNING *
                """,
                (
                    persisted_status.value,
                    decision.decision.value,
                    decision.session_id,
                    decision.reviewer_feedback,
                    decision.decided_at,
                    decision.review_id,
                ),
            ).fetchone()
            run_row = connection.execute(
                """
                UPDATE runs
                SET status = 'active', completed_at = NULL, updated_at = NOW()
                WHERE conversation_id = %s
                  AND run_id = %s
                  AND status = 'waiting_human'
                RETURNING run_id
                """,
                (record.conversation_id, record.run_id),
            ).fetchone()
            if run_row is None:
                raise PersistenceConflictError(
                    f"run {record.run_id} is not waiting for review {record.review_id}"
                )
            connection.execute(
                """
                INSERT INTO messages (
                    conversation_id, session_id, run_id, turn_id,
                    actor_type, actor_name, role, content, metadata
                )
                VALUES (%s, %s, %s, %s, 'user', 'Reviewer', 'human_decision', %s, %s)
                """,
                (
                    record.conversation_id,
                    decision.session_id,
                    record.run_id,
                    record.run_id,
                    decision.decision.value,
                    Jsonb(
                        {
                            "review_id": str(decision.review_id),
                            "reviewer_feedback": decision.reviewer_feedback,
                        }
                    ),
                ),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE conversation_id = %s",
                (record.conversation_id,),
            )
        return as_record(HumanReviewRecord, row, f"review {decision.review_id}")

    def release_review_decision(
        self,
        decision: HumanDecision,
        *,
        error_detail: str | None = None,
    ) -> HumanReviewRecord:
        """Compensate a claimed decision when durable graph resume does not start.

        The review, its reviewer message, and the run lifecycle are restored in one
        transaction so the same visible decision can be retried instead of being lost.
        """

        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM human_reviews WHERE review_id = %s FOR UPDATE",
                (decision.review_id,),
            ).fetchone()
            record = as_record(HumanReviewRecord, existing, f"review {decision.review_id}")
            if record.run_id != decision.run_id:
                raise PersistenceConflictError(
                    f"review {decision.review_id} does not belong to run {decision.run_id}"
                )
            expected_status = (
                ReviewStatus.APPROVED
                if decision.decision.value == "approve"
                else ReviewStatus.REJECTED
            )
            if record.status is ReviewStatus.PENDING:
                return record
            if record.status is not expected_status or record.decision != decision.decision:
                raise PersistenceConflictError(
                    f"review {decision.review_id} has a different persisted decision"
                )

            connection.execute(
                """
                DELETE FROM messages
                WHERE conversation_id = %s
                  AND run_id = %s
                  AND role = 'human_decision'
                  AND metadata ->> 'review_id' = %s
                """,
                (record.conversation_id, record.run_id, str(record.review_id)),
            )
            row = connection.execute(
                """
                UPDATE human_reviews
                SET status = 'pending',
                    decision = NULL,
                    decision_session_id = NULL,
                    reviewer_feedback = NULL,
                    decided_at = NULL
                WHERE review_id = %s
                RETURNING *
                """,
                (record.review_id,),
            ).fetchone()
            run_row = connection.execute(
                """
                UPDATE runs
                SET status = 'waiting_human',
                    error_detail = %s,
                    completed_at = NULL,
                    waiting_at = COALESCE(waiting_at, NOW()),
                    updated_at = NOW()
                WHERE conversation_id = %s
                  AND run_id = %s
                  AND status = 'active'
                RETURNING run_id
                """,
                (error_detail, record.conversation_id, record.run_id),
            ).fetchone()
            if run_row is None:
                raise PersistenceConflictError(
                    f"run {record.run_id} cannot restore review {record.review_id}"
                )
            connection.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE conversation_id = %s",
                (record.conversation_id,),
            )
        return as_record(HumanReviewRecord, row, f"review {decision.review_id}")
