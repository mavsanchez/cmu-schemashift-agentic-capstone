from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from schemashift.config import Settings
from schemashift.domain import SourceRole
from schemashift.mcp import PathOutsideRegistryError
from schemashift.persistence import SourceStatus, UploadedSourceRecord
from schemashift.services.ingestion import IngestionService, infer_source_role
from schemashift.services.source_registry import ApplicationSourceRegistry


class FakeSourceRepository:
    def __init__(self) -> None:
        self.sources: dict[UUID, UploadedSourceRecord] = {}

    def list_sources(
        self, conversation_id: UUID, *, session_id: UUID | None = None
    ) -> list[UploadedSourceRecord]:
        return [
            source
            for source in self.sources.values()
            if source.conversation_id == conversation_id
            and (session_id is None or source.session_id == session_id)
        ]

    def register_source(self, **values: Any) -> UploadedSourceRecord:
        now = datetime.now(UTC)
        payload = dict(values)
        inferred_role = payload.pop("inferred_role")
        record = UploadedSourceRecord(
            **payload,
            role=inferred_role,
            status=SourceStatus.STAGED,
            created_at=now,
            updated_at=now,
        )
        self.sources[record.source_id] = record
        return record

    def get_source(
        self, source_id: UUID, *, conversation_id: UUID | None = None
    ) -> UploadedSourceRecord | None:
        source = self.sources.get(source_id)
        if source is not None and (
            conversation_id is None or source.conversation_id == conversation_id
        ):
            return source
        return None

    def confirm_source(
        self,
        source_id: UUID,
        role: SourceRole,
        *,
        conversation_id: UUID | None = None,
        session_id: UUID | None = None,
        table_name: str | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> UploadedSourceRecord:
        current = self.sources[source_id]
        assert conversation_id is None or current.conversation_id == conversation_id
        assert session_id is None or current.session_id == session_id
        record = current.model_copy(
            update={
                "role": role,
                "table_name": table_name,
                "status": SourceStatus.CONFIRMED,
                "metadata": {**current.metadata, **(metadata_patch or {})},
                "updated_at": datetime.now(UTC),
            }
        )
        self.sources[source_id] = record
        return record

    def update_source_status(
        self,
        source_id: UUID,
        status: SourceStatus,
        *,
        metadata_patch: dict[str, Any] | None = None,
    ) -> UploadedSourceRecord:
        current = self.sources[source_id]
        record = current.model_copy(
            update={
                "status": status,
                "metadata": {**current.metadata, **(metadata_patch or {})},
                "updated_at": datetime.now(UTC),
            }
        )
        self.sources[source_id] = record
        return record


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("customers_v1.sql", "CREATE TABLE customers(id INTEGER)", SourceRole.OLD_SCHEMA),
        ("customers_target.sql", "CREATE TABLE customers(id BIGINT)", SourceRole.NEW_SCHEMA),
        ("migration.sql", "WITH c AS (SELECT 1) SELECT * FROM c", SourceRole.SOURCE_SQL),
        ("notes.md", "# Migration rules", SourceRole.MIGRATION_KNOWLEDGE),
        ("orders_new.csv", None, SourceRole.NEW_DATA),
        ("orders_legacy.parquet", None, SourceRole.OLD_DATA),
        (
            "schema_v2.json",
            '{"tables": [{"name": "customers", "columns": []}]}',
            SourceRole.NEW_SCHEMA,
        ),
        (
            "schema.sql",
            "-- schema_version: v2\nCREATE TABLE customers(id BIGINT)",
            SourceRole.NEW_SCHEMA,
        ),
        (
            "misleading_new.sql",
            "/* schema-version = v1 */\nCREATE TABLE legacy_customer(id INTEGER)",
            SourceRole.OLD_SCHEMA,
        ),
        (
            "schema.json",
            '{"schema_version":"v2","tables":[{"name":"customers","columns":[]}]}',
            SourceRole.NEW_SCHEMA,
        ),
        ("rules.json", '{"topic": "rename", "rule": "x to y"}', SourceRole.MIGRATION_KNOWLEDGE),
    ],
)
def test_role_inference_uses_content_extension_and_version_hints(
    name: str, text: str | None, expected: SourceRole
) -> None:
    assert infer_source_role(name, text=text).role is expected


def test_staging_copies_into_exact_conversation_session_scope(tmp_path: Path) -> None:
    incoming = tmp_path / "legacy customers v1.sql"
    incoming.write_text("CREATE TABLE customers(id INTEGER)", encoding="utf-8")
    upload_root = tmp_path / "controlled" / "uploads"
    repository = FakeSourceRepository()
    registry = ApplicationSourceRegistry((upload_root,))
    service = IngestionService(
        Settings(
            model_provider="mock",
            data_root=tmp_path / "controlled",
            upload_root=upload_root,
            artifact_root=tmp_path / "controlled" / "migrations",
        ),
        repository,  # type: ignore[arg-type]
        registry,
    )
    conversation_id, session_id = uuid4(), uuid4()

    staged = service.stage_files(conversation_id, session_id, [incoming])

    assert len(staged) == 1
    local_path = Path(staged[0].local_path).resolve()
    expected_scope = (upload_root / str(conversation_id) / str(session_id)).resolve()
    assert local_path.parent == expected_scope
    assert local_path.is_file()
    assert local_path.read_text(encoding="utf-8") == incoming.read_text(encoding="utf-8")
    assert local_path.name.endswith("legacy_customers_v1.sql")
    assert repository.sources[staged[0].source_id].status is SourceStatus.STAGED


def test_confirmation_can_correct_role_before_registry_exposure(tmp_path: Path) -> None:
    incoming = tmp_path / "ambiguous.csv"
    incoming.write_text("id,name\n1,Ada\n", encoding="utf-8")
    upload_root = tmp_path / "uploads"
    repository = FakeSourceRepository()
    registry = ApplicationSourceRegistry((upload_root,))
    service = IngestionService(
        Settings(model_provider="mock", upload_root=upload_root),
        repository,  # type: ignore[arg-type]
        registry,
    )
    conversation_id, session_id = uuid4(), uuid4()
    staged = service.stage_files(conversation_id, session_id, [incoming])[0]

    confirmed = service.confirm_sources(
        conversation_id,
        session_id,
        [
            {
                "source_id": str(staged.source_id),
                "role": SourceRole.NEW_DATA.value,
                "table_name": "customers",
            }
        ],
    )[0]

    assert confirmed.role is SourceRole.NEW_DATA
    assert confirmed.table_name == "customers"
    assert confirmed.status is SourceStatus.CONFIRMED
    assert confirmed.metadata["role_confirmed_by"] == "user"
    assert registry.get_source(str(staged.source_id)).role == SourceRole.NEW_DATA.value


def test_confirmation_rejects_source_from_another_scope_before_mutation(tmp_path: Path) -> None:
    incoming = tmp_path / "legacy.csv"
    incoming.write_text("id,name\n1,Ada\n", encoding="utf-8")
    upload_root = tmp_path / "uploads"
    repository = FakeSourceRepository()
    service = IngestionService(
        Settings(model_provider="mock", upload_root=upload_root),
        repository,  # type: ignore[arg-type]
        ApplicationSourceRegistry((upload_root,)),
    )
    owner_conversation, owner_session = uuid4(), uuid4()
    staged = service.stage_files(owner_conversation, owner_session, [incoming])[0]

    with pytest.raises(ValueError, match="does not belong"):
        service.confirm_sources(
            uuid4(),
            uuid4(),
            [{"source_id": str(staged.source_id), "role": "new_data"}],
        )

    assert repository.sources[staged.source_id].status is SourceStatus.STAGED


def test_application_registry_rejects_sibling_prefix_and_unregistered_ids(
    tmp_path: Path,
) -> None:
    controlled = tmp_path / "data"
    controlled.mkdir()
    sibling = tmp_path / "data-elsewhere" / "source.sql"
    sibling.parent.mkdir()
    sibling.write_text("SELECT 1", encoding="utf-8")
    registry = ApplicationSourceRegistry((controlled,))

    with pytest.raises(PathOutsideRegistryError):
        registry.assert_allowed_path(sibling)
    with pytest.raises(LookupError, match="Unknown source ID"):
        registry.get_source(str(sibling))


def test_upload_cap_is_enforced_before_copy(tmp_path: Path) -> None:
    incoming = tmp_path / "too_large.sql"
    incoming.write_text("SELECT 123", encoding="utf-8")
    upload_root = tmp_path / "uploads"
    service = IngestionService(
        Settings(model_provider="mock", upload_root=upload_root, upload_max_bytes=3),
        FakeSourceRepository(),  # type: ignore[arg-type]
        ApplicationSourceRegistry((upload_root,)),
    )

    with pytest.raises(ValueError, match="upload cap"):
        service.stage_files(uuid4(), uuid4(), [incoming])
