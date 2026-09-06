"""Registry for validated or explicitly human-approved migration artifacts."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from psycopg import errors
from psycopg.types.json import Jsonb

from schemashift.domain.ids import RunContext
from schemashift.persistence._common import as_record, page_size
from schemashift.persistence.errors import PersistenceConflictError
from schemashift.persistence.models import ArtifactRecord, ArtifactStatus
from schemashift.persistence.postgres import PostgresDatabase


class ArtifactRepository:
    db: PostgresDatabase

    def create_artifact(
        self,
        context: RunContext,
        *,
        file_name: str,
        local_path: str,
        sha256: str,
        status: ArtifactStatus | str,
        artifact_type: str = "sql",
        known_differences: list[Any] | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: UUID | None = None,
    ) -> ArtifactRecord:
        identifier = artifact_id or uuid4()
        artifact_status = ArtifactStatus(status)
        normalized_hash = sha256.lower()
        if len(normalized_hash) != 64 or any(c not in "0123456789abcdef" for c in normalized_hash):
            raise ValueError("sha256 must be 64 hexadecimal characters")
        if not file_name.strip() or not local_path.strip() or not artifact_type.strip():
            raise ValueError("artifact file_name, local_path, and type must not be blank")
        differences: list[Any] | dict[str, Any] = known_differences or []
        try:
            with self.db.transaction() as connection:
                row = connection.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, conversation_id, session_id, run_id,
                        file_name, local_path, artifact_type, status, sha256,
                        known_differences, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (conversation_id, run_id, file_name) DO NOTHING
                    RETURNING *
                    """,
                    (
                        identifier,
                        context.conversation_id,
                        context.session_id,
                        context.run_id,
                        file_name,
                        local_path,
                        artifact_type,
                        artifact_status.value,
                        normalized_hash,
                        Jsonb(differences),
                        Jsonb(metadata or {}),
                    ),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        """
                        SELECT * FROM artifacts
                        WHERE conversation_id = %s AND run_id = %s AND file_name = %s
                        """,
                        (context.conversation_id, context.run_id, file_name),
                    ).fetchone()
                    if row and (
                        row["local_path"] != local_path
                        or row["sha256"] != normalized_hash
                        or row["status"] != artifact_status.value
                    ):
                        raise PersistenceConflictError(
                            f"artifact {file_name!r} conflicts with an existing run artifact"
                        )
        except errors.UniqueViolation as exc:
            raise PersistenceConflictError(
                "the artifact identifier or controlled local path is already registered"
            ) from exc
        return as_record(ArtifactRecord, row, f"artifact {identifier}")

    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = %s",
                (artifact_id,),
            ).fetchone()
        return ArtifactRecord.model_validate(row) if row else None

    def list_artifacts(
        self,
        conversation_id: UUID,
        *,
        run_id: UUID | None = None,
        limit: int = 100,
    ) -> list[ArtifactRecord]:
        limit = page_size(limit)
        if run_id is None:
            query = """
                SELECT * FROM artifacts
                WHERE conversation_id = %s
                ORDER BY created_at DESC, artifact_id
                LIMIT %s
            """
            parameters = (conversation_id, limit)
        else:
            query = """
                SELECT * FROM artifacts
                WHERE conversation_id = %s AND run_id = %s
                ORDER BY created_at DESC, artifact_id
                LIMIT %s
            """
            parameters = (conversation_id, run_id, limit)
        with self.db.transaction() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [ArtifactRecord.model_validate(row) for row in rows]
