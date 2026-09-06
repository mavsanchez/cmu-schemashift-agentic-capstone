"""Registry operations for controlled local source files and databases."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from psycopg import errors
from psycopg.types.json import Jsonb

from schemashift.domain.migration import SourceRole
from schemashift.persistence._common import as_record, page_size
from schemashift.persistence.errors import PersistenceConflictError
from schemashift.persistence.models import SourceStatus, UploadedSourceRecord
from schemashift.persistence.postgres import PostgresDatabase


class SourceRepository:
    db: PostgresDatabase

    def register_source(
        self,
        *,
        conversation_id: UUID,
        session_id: UUID,
        original_name: str,
        local_path: str,
        mime_type: str,
        sha256: str,
        size_bytes: int,
        inferred_role: SourceRole | str | None = None,
        source_id: UUID | None = None,
        table_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UploadedSourceRecord:
        identifier = source_id or uuid4()
        role = SourceRole(inferred_role).value if inferred_role is not None else None
        normalized_hash = sha256.lower()
        if len(normalized_hash) != 64 or any(c not in "0123456789abcdef" for c in normalized_hash):
            raise ValueError("sha256 must be 64 lowercase or uppercase hexadecimal characters")
        if size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if not original_name.strip() or not local_path.strip() or not mime_type.strip():
            raise ValueError("original_name, local_path, and mime_type must not be blank")
        try:
            with self.db.transaction() as connection:
                row = connection.execute(
                    """
                    INSERT INTO uploaded_sources (
                        source_id, conversation_id, session_id, original_name, local_path,
                        role, mime_type, sha256, size_bytes, table_name, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (conversation_id, session_id, sha256, original_name)
                    DO UPDATE SET updated_at = uploaded_sources.updated_at
                    RETURNING *
                    """,
                    (
                        identifier,
                        conversation_id,
                        session_id,
                        original_name,
                        local_path,
                        role,
                        mime_type,
                        normalized_hash,
                        size_bytes,
                        table_name,
                        Jsonb(metadata or {}),
                    ),
                ).fetchone()
                connection.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE conversation_id = %s",
                    (conversation_id,),
                )
        except errors.UniqueViolation as exc:
            raise PersistenceConflictError(
                "the source identifier or controlled local path is already registered"
            ) from exc
        return as_record(UploadedSourceRecord, row, "uploaded source")

    def get_source(
        self,
        source_id: UUID,
        *,
        conversation_id: UUID | None = None,
    ) -> UploadedSourceRecord | None:
        query = "SELECT * FROM uploaded_sources WHERE source_id = %s"
        parameters: list[Any] = [source_id]
        if conversation_id is not None:
            query += " AND conversation_id = %s"
            parameters.append(conversation_id)
        with self.db.transaction() as connection:
            row = connection.execute(query, parameters).fetchone()
        return UploadedSourceRecord.model_validate(row) if row else None

    def list_sources(
        self,
        conversation_id: UUID,
        *,
        session_id: UUID | None = None,
        role: SourceRole | str | None = None,
        status: SourceStatus | str | None = None,
        limit: int = 500,
    ) -> list[UploadedSourceRecord]:
        limit = page_size(limit, maximum=10_000)
        conditions = ["conversation_id = %s"]
        parameters: list[Any] = [conversation_id]
        if session_id is not None:
            conditions.append("session_id = %s")
            parameters.append(session_id)
        if role is not None:
            conditions.append("role = %s")
            parameters.append(SourceRole(role).value)
        if status is not None:
            conditions.append("status = %s")
            parameters.append(SourceStatus(status).value)
        parameters.append(limit)
        query = f"""
            SELECT * FROM uploaded_sources
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at, source_id
            LIMIT %s
        """
        with self.db.transaction() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [UploadedSourceRecord.model_validate(row) for row in rows]

    def confirm_source(
        self,
        source_id: UUID,
        role: SourceRole | str,
        *,
        conversation_id: UUID | None = None,
        session_id: UUID | None = None,
        table_name: str | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> UploadedSourceRecord:
        source_role = SourceRole(role)
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                UPDATE uploaded_sources
                SET role = %s,
                    status = 'confirmed',
                    table_name = COALESCE(%s, table_name),
                    metadata = metadata || %s,
                    updated_at = NOW()
                WHERE source_id = %s
                  AND (%s::uuid IS NULL OR conversation_id = %s::uuid)
                  AND (%s::uuid IS NULL OR session_id = %s::uuid)
                RETURNING *
                """,
                (
                    source_role.value,
                    table_name,
                    Jsonb(metadata_patch or {}),
                    source_id,
                    conversation_id,
                    conversation_id,
                    session_id,
                    session_id,
                ),
            ).fetchone()
        return as_record(UploadedSourceRecord, row, f"uploaded source {source_id}")

    def update_source_status(
        self,
        source_id: UUID,
        status: SourceStatus | str,
        *,
        metadata_patch: dict[str, Any] | None = None,
    ) -> UploadedSourceRecord:
        source_status = SourceStatus(status)
        if source_status is SourceStatus.CONFIRMED:
            raise ValueError("use confirm_source to confirm a source role")
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                UPDATE uploaded_sources
                SET status = %s,
                    metadata = metadata || %s,
                    updated_at = NOW()
                WHERE source_id = %s
                RETURNING *
                """,
                (source_status.value, Jsonb(metadata_patch or {}), source_id),
            ).fetchone()
        return as_record(UploadedSourceRecord, row, f"uploaded source {source_id}")
