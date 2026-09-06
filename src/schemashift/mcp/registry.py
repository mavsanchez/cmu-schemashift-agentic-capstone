"""Controlled source/database registry abstractions for MCP tools.

Tools receive opaque IDs, never caller-supplied paths.  The registry is the only
component allowed to translate an ID into a local file.  Production persistence
repositories can implement :class:`SourceRegistry`; the in-memory and manifest
implementations keep unit tests and the stdio server deterministic.
"""

from __future__ import annotations

import json
import mimetypes
import os
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Protocol, runtime_checkable


class RegistryError(LookupError):
    """Base class for controlled registry failures."""


class UnknownSourceError(RegistryError):
    pass


class UnknownDatabaseError(RegistryError):
    pass


class PathOutsideRegistryError(RegistryError):
    pass


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    conversation_id: str
    session_id: str
    path: Path
    role: str | None = None
    original_name: str | None = None
    content_type: str | None = None
    sha256: str | None = None
    size: int | None = None
    status: str = "confirmed"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path).resolve())
        if not self.source_id:
            raise ValueError("source_id must not be empty")

    def summary(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "original_name": self.original_name or self.path.name,
            "role": self.role,
            "content_type": self.content_type
            or mimetypes.guess_type(self.path.name)[0]
            or "application/octet-stream",
            "sha256": self.sha256,
            "size": self.size if self.size is not None else _safe_size(self.path),
            "status": self.status,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class DatabaseRecord:
    database_id: str
    path: Path
    conversation_id: str | None = None
    session_id: str | None = None
    role: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path).resolve())
        if not self.database_id:
            raise ValueError("database_id must not be empty")


@runtime_checkable
class SourceRegistry(Protocol):
    """Minimal protocol consumed by the deterministic tool layer."""

    def get_source(self, source_id: str) -> SourceRecord | Mapping[str, Any] | None: ...

    def list_sources(
        self, conversation_id: str, session_id: str
    ) -> Iterable[SourceRecord | Mapping[str, Any]]: ...

    def get_database(
        self, database_id: str
    ) -> DatabaseRecord | SourceRecord | Mapping[str, Any] | None: ...


def _safe_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value):
        return asdict(value)
    data = getattr(value, "model_dump", None)
    if callable(data):
        return data()
    if hasattr(value, "__dict__"):
        return vars(value)
    raise TypeError(f"Unsupported registry record type: {type(value).__name__}")


def coerce_source_record(value: object, *, source_id: str | None = None) -> SourceRecord:
    if isinstance(value, SourceRecord):
        return value
    data = _mapping(value)
    path = data.get("path") or data.get("local_path")
    identifier = data.get("source_id") or data.get("file_id") or source_id
    if not identifier or not path:
        raise TypeError("A source record requires source_id/file_id and path/local_path")
    metadata = data.get("metadata") or {}
    return SourceRecord(
        source_id=str(identifier),
        conversation_id=str(data.get("conversation_id") or ""),
        session_id=str(data.get("session_id") or ""),
        path=Path(str(path)),
        role=_enum_value(data.get("role")),
        original_name=data.get("original_name") or data.get("filename"),
        content_type=data.get("content_type") or data.get("mime_type"),
        sha256=data.get("sha256"),
        size=data.get("size") or data.get("size_bytes"),
        status=str(data.get("status") or "confirmed"),
        metadata=metadata if isinstance(metadata, Mapping) else {},
    )


def coerce_database_record(value: object, *, database_id: str | None = None) -> DatabaseRecord:
    if isinstance(value, DatabaseRecord):
        return value
    if isinstance(value, SourceRecord):
        return DatabaseRecord(
            database_id=database_id or value.source_id,
            path=value.path,
            conversation_id=value.conversation_id,
            session_id=value.session_id,
            role=value.role,
            metadata=value.metadata,
        )
    if isinstance(value, (str, Path)):
        if not database_id:
            raise TypeError("database_id is required when coercing a path")
        return DatabaseRecord(database_id=database_id, path=Path(value))
    data = _mapping(value)
    path = data.get("path") or data.get("local_path") or data.get("database_path")
    identifier = data.get("database_id") or data.get("source_id") or database_id
    if not identifier or not path:
        raise TypeError("A database record requires database_id and a local path")
    metadata = data.get("metadata") or {}
    return DatabaseRecord(
        database_id=str(identifier),
        path=Path(str(path)),
        conversation_id=_optional_str(data.get("conversation_id")),
        session_id=_optional_str(data.get("session_id")),
        role=_enum_value(data.get("role")),
        metadata=metadata if isinstance(metadata, Mapping) else {},
    )


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


class InMemorySourceRegistry:
    """Thread-safe controlled registry for local use and tests."""

    def __init__(self, allowed_roots: Iterable[str | Path]) -> None:
        roots = tuple(Path(root).resolve() for root in allowed_roots)
        if not roots:
            raise ValueError("At least one controlled root is required")
        self.allowed_roots = roots
        self._sources: dict[str, SourceRecord] = {}
        self._databases: dict[str, DatabaseRecord] = {}
        self._lock = RLock()

    def assert_allowed_path(self, path: str | Path) -> Path:
        resolved = Path(path).resolve()
        for root in self.allowed_roots:
            try:
                common = os.path.commonpath((str(root), str(resolved)))
                if os.path.normcase(common) == os.path.normcase(str(root)):
                    return resolved
            except ValueError:
                continue
        raise PathOutsideRegistryError(f"Registered path is outside controlled roots: {resolved}")

    def register_source(self, record: SourceRecord | Mapping[str, Any]) -> SourceRecord:
        source = coerce_source_record(record)
        self.assert_allowed_path(source.path)
        with self._lock:
            self._sources[source.source_id] = source
        return source

    def register_database(
        self, record: DatabaseRecord | SourceRecord | Mapping[str, Any]
    ) -> DatabaseRecord:
        database = coerce_database_record(record)
        self.assert_allowed_path(database.path)
        with self._lock:
            self._databases[database.database_id] = database
        return database

    def get_source(self, source_id: str) -> SourceRecord:
        with self._lock:
            try:
                return self._sources[str(source_id)]
            except KeyError as exc:
                raise UnknownSourceError(f"Unknown source ID: {source_id}") from exc

    def list_sources(self, conversation_id: str, session_id: str) -> list[SourceRecord]:
        with self._lock:
            return sorted(
                (
                    source
                    for source in self._sources.values()
                    if source.conversation_id == str(conversation_id)
                    and source.session_id == str(session_id)
                ),
                key=lambda item: (item.original_name or item.path.name, item.source_id),
            )

    def get_database(self, database_id: str) -> DatabaseRecord:
        with self._lock:
            try:
                return self._databases[str(database_id)]
            except KeyError as exc:
                raise UnknownDatabaseError(f"Unknown database ID: {database_id}") from exc


class ManifestSourceRegistry(InMemorySourceRegistry):
    """Read a trusted JSON registry manifest used by the standalone MCP server."""

    def __init__(self, manifest_path: str | Path, allowed_roots: Iterable[str | Path]):
        super().__init__(allowed_roots)
        self.manifest_path = Path(manifest_path).resolve()
        if self.manifest_path.exists():
            self.reload()

    def reload(self) -> None:
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("Registry manifest root must be a JSON object")
        sources = payload.get("sources", [])
        databases = payload.get("databases", [])
        if not isinstance(sources, list) or not isinstance(databases, list):
            raise ValueError("Registry manifest sources/databases must be arrays")
        with self._lock:
            self._sources.clear()
            self._databases.clear()
        for source in sources:
            self.register_source(source)
        for database in databases:
            self.register_database(database)


def resolve_source(registry: SourceRegistry, source_id: str) -> SourceRecord:
    try:
        value = registry.get_source(str(source_id))
    except (KeyError, LookupError) as exc:
        raise UnknownSourceError(f"Unknown source ID: {source_id}") from exc
    except Exception as exc:
        raise RegistryError(f"Source registry lookup failed for ID: {source_id}") from exc
    if value is None:
        raise UnknownSourceError(f"Unknown source ID: {source_id}")
    record = coerce_source_record(value, source_id=str(source_id))
    _assert_registry_path(registry, record.path)
    return record


def resolve_database(registry: SourceRegistry, database_id: str) -> DatabaseRecord:
    try:
        value = registry.get_database(str(database_id))
    except (AttributeError, KeyError, LookupError) as exc:
        # A persistence adapter may store DuckDB uploads as ordinary sources.
        try:
            source = resolve_source(registry, str(database_id))
        except RegistryError:
            raise UnknownDatabaseError(f"Unknown database ID: {database_id}") from exc
        value = source
    except Exception as exc:
        raise RegistryError(f"Database registry lookup failed for ID: {database_id}") from exc
    if value is None:
        raise UnknownDatabaseError(f"Unknown database ID: {database_id}")
    record = coerce_database_record(value, database_id=str(database_id))
    _assert_registry_path(registry, record.path)
    return record


def _assert_registry_path(registry: SourceRegistry, path: Path) -> None:
    checker = getattr(registry, "assert_allowed_path", None)
    if callable(checker):
        checker(path)


__all__ = [
    "DatabaseRecord",
    "InMemorySourceRegistry",
    "ManifestSourceRegistry",
    "PathOutsideRegistryError",
    "RegistryError",
    "SourceRecord",
    "SourceRegistry",
    "UnknownDatabaseError",
    "UnknownSourceError",
    "coerce_database_record",
    "coerce_source_record",
    "resolve_database",
    "resolve_source",
]
