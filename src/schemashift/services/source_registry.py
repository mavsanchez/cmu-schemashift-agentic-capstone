"""Persistence-backed registry resolving opaque IDs to controlled local files."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID

from schemashift.mcp.registry import (
    DatabaseRecord,
    PathOutsideRegistryError,
    SourceRecord,
    UnknownDatabaseError,
    UnknownSourceError,
    coerce_database_record,
    coerce_source_record,
)
from schemashift.persistence import PostgresRepository, SourceStatus


class ApplicationSourceRegistry:
    """Resolve only PostgreSQL records or explicitly registered local fixtures.

    Database manifests are written by :class:`IngestionService`, then discovered
    from controlled roots on restart. A model can select an opaque identifier,
    but it can never supply or alter the resolved path.
    """

    def __init__(
        self,
        allowed_roots: Iterable[str | Path],
        *,
        repository: PostgresRepository | None = None,
    ) -> None:
        roots = tuple(Path(root).resolve() for root in allowed_roots)
        if not roots:
            raise ValueError("At least one controlled source root is required")
        self.allowed_roots = roots
        self.repository = repository
        self._sources: dict[str, SourceRecord] = {}
        self._databases: dict[str, DatabaseRecord] = {}
        self._lock = RLock()

    def assert_allowed_path(self, path: str | Path) -> Path:
        resolved = Path(path).resolve()
        for root in self.allowed_roots:
            try:
                if os.path.commonpath((str(root), str(resolved))) == str(root):
                    return resolved
            except ValueError:
                continue
        raise PathOutsideRegistryError(
            f"Registered path is outside a controlled data root: {resolved}"
        )

    def register_source(self, record: SourceRecord | Mapping[str, Any] | Any) -> SourceRecord:
        source = coerce_source_record(record)
        self.assert_allowed_path(source.path)
        with self._lock:
            self._sources[source.source_id] = source
        return source

    def register_database(
        self, record: DatabaseRecord | SourceRecord | Mapping[str, Any] | Any
    ) -> DatabaseRecord:
        database = coerce_database_record(record)
        self.assert_allowed_path(database.path)
        with self._lock:
            self._databases[database.database_id] = database
        return database

    def get_source(self, source_id: str) -> SourceRecord:
        identifier = str(source_id)
        with self._lock:
            local = self._sources.get(identifier)
        if local is not None:
            return local
        if self.repository is None:
            raise UnknownSourceError(f"Unknown source ID: {identifier}")
        try:
            record = self.repository.get_source(UUID(identifier))
        except (TypeError, ValueError):
            record = None
        if record is None:
            raise UnknownSourceError(f"Unknown source ID: {identifier}")
        if record.status not in {SourceStatus.CONFIRMED, SourceStatus.INDEXED}:
            raise UnknownSourceError(f"Source ID is not confirmed: {identifier}")
        return self.register_source(record)

    def list_sources(self, conversation_id: str, session_id: str) -> list[SourceRecord]:
        keyed: dict[str, SourceRecord] = {}
        with self._lock:
            for record in self._sources.values():
                if record.conversation_id == str(conversation_id) and record.session_id == str(
                    session_id
                ):
                    keyed[record.source_id] = record
        if self.repository is not None:
            try:
                persisted = self.repository.list_sources(
                    UUID(str(conversation_id)),
                    session_id=UUID(str(session_id)),
                )
            except (TypeError, ValueError):
                persisted = []
            for record in persisted:
                if record.status in {SourceStatus.CONFIRMED, SourceStatus.INDEXED}:
                    source = coerce_source_record(record)
                    self.assert_allowed_path(source.path)
                    keyed[source.source_id] = source
        return sorted(
            keyed.values(),
            key=lambda item: (item.original_name or item.path.name, item.source_id),
        )

    def get_database(self, database_id: str) -> DatabaseRecord:
        identifier = str(database_id)
        with self._lock:
            record = self._databases.get(identifier)
        if record is not None:
            return record
        try:
            source = self.get_source(identifier)
        except UnknownSourceError as exc:
            raise UnknownDatabaseError(f"Unknown database ID: {identifier}") from exc
        if source.path.suffix.casefold() not in {".duckdb", ".ddb"}:
            raise UnknownDatabaseError(f"Source is not a DuckDB database: {identifier}")
        return self.register_database(
            DatabaseRecord(
                database_id=identifier,
                path=source.path,
                conversation_id=source.conversation_id,
                session_id=source.session_id,
                role=source.role,
                metadata=source.metadata,
            )
        )

    def load_database_manifests(self) -> int:
        """Load trusted generated manifests beneath configured data roots."""

        loaded = 0
        for root in self.allowed_roots:
            if not root.exists():
                continue
            candidates = [*root.rglob("database_manifest.json")]
            demo = root / "generated" / "demo_manifest.json"
            if demo.is_file():
                candidates.append(demo)
            for manifest_path in sorted(set(candidates)):
                self.assert_allowed_path(manifest_path)
                try:
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                records: list[Mapping[str, Any]] = []
                if isinstance(payload, Mapping) and isinstance(payload.get("databases"), list):
                    records.extend(
                        item for item in payload["databases"] if isinstance(item, Mapping)
                    )
                elif isinstance(payload, Mapping):
                    for side in ("old", "new"):
                        identifier = payload.get(f"{side}_database_id")
                        path = payload.get(f"{side}_database_path")
                        if identifier and path:
                            records.append(
                                {
                                    "database_id": identifier,
                                    "database_path": path,
                                    "role": f"{side}_data",
                                    "metadata": {"manifest": str(manifest_path)},
                                }
                            )
                for item in records:
                    try:
                        self.register_database(item)
                    except (TypeError, ValueError, PathOutsideRegistryError):
                        continue
                    loaded += 1
        return loaded
