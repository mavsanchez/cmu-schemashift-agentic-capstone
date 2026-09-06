"""Controlled upload staging, role confirmation, and local dataset building."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

import duckdb

from schemashift.config import Settings
from schemashift.domain import SourceRole
from schemashift.mcp import DatabaseRecord
from schemashift.persistence import PostgresRepository, SourceStatus, UploadedSourceRecord
from schemashift.retrieval import KnowledgeIngestor

from .source_registry import ApplicationSourceRegistry

ACCEPTED_SUFFIXES = frozenset({".sql", ".md", ".json", ".csv", ".parquet"})
_OLD_HINTS = re.compile(r"(?:^|[_\-.])(old|source|legacy|v1)(?:$|[_\-.])", re.IGNORECASE)
_NEW_HINTS = re.compile(r"(?:^|[_\-.])(new|target|current|v2)(?:$|[_\-.])", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RoleInference:
    role: SourceRole
    confidence: float
    reason: str
    schema_version: str | None = None


@dataclass(frozen=True, slots=True)
class StagedSource:
    source_id: UUID
    original_name: str
    local_path: str
    inferred_role: SourceRole
    confidence: float
    reason: str
    table_name: str | None
    status: str = "staged"

    def table_row(self) -> list[Any]:
        return [
            str(self.source_id),
            self.original_name,
            self.inferred_role.value,
            self.table_name or "",
            f"{self.confidence:.2f}",
            self.reason,
        ]


def _role_from_name(name: str, old: SourceRole, new: SourceRole) -> RoleInference:
    if _NEW_HINTS.search(name):
        return RoleInference(new, 0.92, "filename identifies a target/new version", "v2")
    if _OLD_HINTS.search(name):
        return RoleInference(old, 0.92, "filename identifies a source/old version", "v1")
    return RoleInference(old, 0.55, "no version marker found; confirmation is required")


def _role_from_schema_version(
    version: Any,
    old: SourceRole,
    new: SourceRole,
) -> RoleInference | None:
    normalized = str(version).strip().casefold().lstrip("v")
    if normalized in {"1", "1.0", "old", "source", "legacy"}:
        return RoleInference(old, 0.97, "schema content identifies source/old version", "v1")
    if normalized in {"2", "2.0", "new", "target", "current"}:
        return RoleInference(new, 0.97, "schema content identifies target/new version", "v2")
    return None


def _sql_schema_version(text: str) -> str | None:
    match = re.search(
        r"(?im)(?:schema[_ -]?version|version)\s*[:=]\s*['\"]?([A-Za-z0-9_.-]+)",
        text,
    )
    return match.group(1) if match else None


def infer_source_role(path: str | Path, *, text: str | None = None) -> RoleInference:
    """Infer a provisional role; every result still requires user confirmation."""

    source = Path(path)
    suffix = source.suffix.casefold()
    name = source.name.casefold()
    sample = (text or "").lstrip()
    if suffix == ".md":
        return RoleInference(SourceRole.MIGRATION_KNOWLEDGE, 0.99, "Markdown knowledge document")
    if suffix in {".csv", ".parquet"}:
        return _role_from_name(name, SourceRole.OLD_DATA, SourceRole.NEW_DATA)
    if suffix == ".sql":
        if re.match(r"(?is)^(?:--[^\n]*\n|/\*.*?\*/\s*)*(select|with|values|\()", sample):
            return RoleInference(SourceRole.SOURCE_SQL, 0.98, "SQL contains a query expression")
        if re.search(r"(?is)\bcreate\s+(?:or\s+replace\s+)?table\b", sample):
            version_role = _role_from_schema_version(
                _sql_schema_version(sample),
                SourceRole.OLD_SCHEMA,
                SourceRole.NEW_SCHEMA,
            )
            if version_role is not None:
                return version_role
            return _role_from_name(name, SourceRole.OLD_SCHEMA, SourceRole.NEW_SCHEMA)
        return RoleInference(SourceRole.SOURCE_SQL, 0.62, "SQL content is ambiguous")
    if suffix == ".json":
        try:
            value = json.loads(sample) if sample else {}
        except ValueError:
            value = {}
        schema_keys = {"tables", "definitions", "$schema", "properties", "columns"}
        if isinstance(value, dict) and schema_keys.intersection(value):
            version = value.get("schema_version", value.get("schemaVersion", value.get("version")))
            version_role = _role_from_schema_version(
                version,
                SourceRole.OLD_SCHEMA,
                SourceRole.NEW_SCHEMA,
            )
            if version_role is not None:
                return version_role
            return _role_from_name(name, SourceRole.OLD_SCHEMA, SourceRole.NEW_SCHEMA)
        return RoleInference(
            SourceRole.MIGRATION_KNOWLEDGE,
            0.72,
            "JSON appears to contain migration knowledge rather than a schema",
        )
    raise ValueError(f"Unsupported source type: {source.suffix or '<none>'}")


def _safe_name(name: str) -> str:
    base = Path(name).name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return safe[:180] or "source"


def derive_table_name(name: str) -> str:
    stem = re.sub(
        r"(?i)(?:^|[_\-.])(old|new|source|target|v1|v2)(?:$|[_\-.])",
        "_",
        Path(name).stem,
    )
    value = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_").casefold()
    if not value:
        value = "uploaded_table"
    if value[0].isdigit():
        value = f"table_{value}"
    return value[:63]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class IngestionService:
    def __init__(
        self,
        settings: Settings,
        repository: PostgresRepository,
        registry: ApplicationSourceRegistry,
        *,
        knowledge_ingestor: KnowledgeIngestor | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.registry = registry
        self.knowledge_ingestor = knowledge_ingestor
        self.upload_root = settings.upload_root.resolve()

    def _scope(self, conversation_id: UUID, session_id: UUID) -> Path:
        target = (self.upload_root / str(conversation_id) / str(session_id)).resolve()
        if self.upload_root not in target.parents:
            raise ValueError("Upload scope escaped the configured root")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def stage_files(
        self,
        conversation_id: UUID,
        session_id: UUID,
        files: list[str | Path],
    ) -> list[StagedSource]:
        scope = self._scope(conversation_id, session_id)
        staged: list[StagedSource] = []
        existing = self.repository.list_sources(conversation_id, session_id=session_id)
        known = {(item.sha256, item.original_name): item for item in existing}
        for raw in files:
            source = Path(raw).resolve()
            suffix = source.suffix.casefold()
            if suffix not in ACCEPTED_SUFFIXES:
                raise ValueError(f"Unsupported upload extension: {source.suffix or '<none>'}")
            size = source.stat().st_size
            if size > self.settings.upload_max_bytes:
                raise ValueError(
                    f"{source.name} exceeds the {self.settings.upload_max_bytes}-byte upload cap"
                )
            digest = sha256_file(source)
            original_name = source.name
            previous = known.get((digest, original_name))
            if previous is not None:
                inference = RoleInference(
                    previous.role or SourceRole.SOURCE_SQL,
                    float(previous.metadata.get("inference_confidence", 1.0)),
                    str(previous.metadata.get("inference_reason", "previously staged")),
                )
                staged.append(
                    StagedSource(
                        source_id=previous.source_id,
                        original_name=previous.original_name,
                        local_path=previous.local_path,
                        inferred_role=inference.role,
                        confidence=inference.confidence,
                        reason=inference.reason,
                        table_name=previous.table_name,
                        status=previous.status.value,
                    )
                )
                continue
            text: str | None = None
            if suffix in {".sql", ".md", ".json"}:
                text = source.read_text(encoding="utf-8", errors="replace")[:250_000]
            inference = infer_source_role(source, text=text)
            identifier = uuid4()
            target = scope / f"{identifier}_{_safe_name(original_name)}"
            temporary = target.with_suffix(target.suffix + ".part")
            shutil.copyfile(source, temporary)
            temporary.replace(target)
            table_name = (
                derive_table_name(original_name) if suffix in {".csv", ".parquet"} else None
            )
            record = self.repository.register_source(
                source_id=identifier,
                conversation_id=conversation_id,
                session_id=session_id,
                original_name=original_name,
                local_path=str(target),
                mime_type=mimetypes.guess_type(original_name)[0] or "application/octet-stream",
                sha256=digest,
                size_bytes=size,
                inferred_role=inference.role,
                table_name=table_name,
                metadata={
                    "inference_confidence": inference.confidence,
                    "inference_reason": inference.reason,
                    "schema_version": inference.schema_version,
                },
            )
            staged.append(
                StagedSource(
                    source_id=record.source_id,
                    original_name=record.original_name,
                    local_path=record.local_path,
                    inferred_role=inference.role,
                    confidence=inference.confidence,
                    reason=inference.reason,
                    table_name=record.table_name,
                )
            )
        return staged

    def confirm_sources(
        self,
        conversation_id: UUID,
        session_id: UUID,
        selections: list[dict[str, Any]],
    ) -> list[UploadedSourceRecord]:
        prepared: list[tuple[UUID, SourceRole, str | None]] = []
        for selection in selections:
            source_id = UUID(str(selection["source_id"]))
            role = SourceRole(str(selection["role"]))
            table_name = str(selection.get("table_name") or "").strip() or None
            current = self.repository.get_source(source_id, conversation_id=conversation_id)
            if current is None or current.session_id != session_id:
                raise ValueError(
                    f"Staged source {source_id} does not belong to the current conversation/session"
                )
            if role in {SourceRole.OLD_DATA, SourceRole.NEW_DATA} and table_name is None:
                table_name = derive_table_name(current.original_name)
            prepared.append((source_id, role, table_name))

        confirmed: list[UploadedSourceRecord] = []
        for source_id, role, table_name in prepared:
            record = self.repository.confirm_source(
                source_id,
                role,
                conversation_id=conversation_id,
                session_id=session_id,
                table_name=table_name,
                metadata_patch={"role_confirmed_by": "user"},
            )
            self.registry.register_source(record)
            if role is SourceRole.MIGRATION_KNOWLEDGE and self.knowledge_ingestor is not None:
                report = self.knowledge_ingestor.ingest_paths(
                    [record.local_path],
                    root=Path(record.local_path).parent,
                    metadata={
                        "source_id": str(record.source_id),
                        "topic": "uploaded_migration_knowledge",
                    },
                )
                record = self.repository.update_source_status(
                    source_id,
                    SourceStatus.INDEXED,
                    metadata_patch={"knowledge_chunks": report.chunks},
                )
            confirmed.append(record)
        return confirmed

    def build_data_databases(
        self,
        conversation_id: UUID,
        session_id: UUID,
    ) -> dict[str, str]:
        sources = self.repository.list_sources(conversation_id, session_id=session_id)
        result: dict[str, str] = {}
        manifest_records: list[dict[str, Any]] = []
        scope = self._scope(conversation_id, session_id)
        for role, side in ((SourceRole.OLD_DATA, "old"), (SourceRole.NEW_DATA, "new")):
            selected = [
                item
                for item in sources
                if item.role is role
                and item.status in {SourceStatus.CONFIRMED, SourceStatus.INDEXED}
                and Path(item.local_path).suffix.casefold() in {".csv", ".parquet"}
            ]
            if not selected:
                continue
            database_id = str(uuid5(conversation_id, f"{session_id}:{side}:duckdb"))
            database_path = (scope / f"{side}_data.duckdb").resolve()
            self.registry.assert_allowed_path(database_path)
            if database_path.exists():
                database_path.unlink()
            names: set[str] = set()
            with duckdb.connect(str(database_path)) as connection:
                for item in selected:
                    table_name = item.table_name or derive_table_name(item.original_name)
                    if table_name in names:
                        raise ValueError(f"Duplicate confirmed table name: {table_name}")
                    names.add(table_name)
                    path = self.registry.assert_allowed_path(item.local_path)
                    relation = (
                        connection.read_csv(str(path))
                        if path.suffix.casefold() == ".csv"
                        else connection.from_parquet(str(path))
                    )
                    relation.create(table_name)
                    self.repository.update_source_status(
                        item.source_id,
                        SourceStatus.INDEXED,
                        metadata_patch={
                            "database_id": database_id,
                            "database_path": str(database_path),
                            "table_name": table_name,
                        },
                    )
            database = self.registry.register_database(
                DatabaseRecord(
                    database_id=database_id,
                    path=database_path,
                    conversation_id=str(conversation_id),
                    session_id=str(session_id),
                    role=role.value,
                    metadata={"tables": sorted(names)},
                )
            )
            result[f"{side}_database_id"] = database.database_id
            manifest_records.append(
                {
                    "database_id": database.database_id,
                    "database_path": str(database.path),
                    "conversation_id": database.conversation_id,
                    "session_id": database.session_id,
                    "role": database.role,
                    "metadata": dict(database.metadata),
                }
            )
        manifest = scope / "database_manifest.json"
        temporary = scope / ".database_manifest.json.tmp"
        temporary.write_text(
            json.dumps({"databases": manifest_records}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(manifest)
        return result


__all__ = [
    "ACCEPTED_SUFFIXES",
    "IngestionService",
    "RoleInference",
    "StagedSource",
    "derive_table_name",
    "infer_source_role",
    "sha256_file",
]
