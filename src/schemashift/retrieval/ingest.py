"""Filesystem-to-vector-store knowledge ingestion service."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .chunking import DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS, chunk_file
from .redis_vector import KnowledgeStore
from .schemas import IngestionReport, KnowledgeChunk

SUPPORTED_KNOWLEDGE_SUFFIXES = frozenset({".md", ".json"})


class KnowledgeIngestor:
    def __init__(
        self,
        store: KnowledgeStore,
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    ) -> None:
        self.store = store
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    @classmethod
    def from_settings(cls, store: KnowledgeStore, settings: Any) -> KnowledgeIngestor:
        """Build an ingestor from the application's centralized chunk policy."""

        return cls(
            store,
            max_chars=int(getattr(settings, "knowledge_chunk_chars", DEFAULT_MAX_CHARS)),
            overlap_chars=int(
                getattr(settings, "knowledge_chunk_overlap_chars", DEFAULT_OVERLAP_CHARS)
            ),
        )

    def chunk_paths(
        self,
        paths: Iterable[str | Path],
        *,
        root: str | Path | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[KnowledgeChunk]:
        root_path = Path(root).resolve() if root is not None else None
        chunks: list[KnowledgeChunk] = []
        for raw_path in sorted((Path(path) for path in paths), key=lambda item: item.as_posix()):
            if raw_path.suffix.casefold() not in SUPPORTED_KNOWLEDGE_SUFFIXES:
                continue
            resolved = raw_path.resolve()
            if root_path is not None:
                try:
                    document = resolved.relative_to(root_path).as_posix()
                except ValueError:
                    document = resolved.name
            else:
                document = raw_path.name
            chunks.extend(
                chunk_file(
                    resolved,
                    document=document,
                    metadata=metadata,
                    max_chars=self.max_chars,
                    overlap_chars=self.overlap_chars,
                )
            )
        return chunks

    def ingest_paths(
        self,
        paths: Iterable[str | Path],
        *,
        root: str | Path | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> IngestionReport:
        return self.store.ingest(self.chunk_paths(paths, root=root, metadata=metadata))

    def ingest_directory(
        self,
        directory: str | Path,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> IngestionReport:
        root = Path(directory)
        if not root.is_dir():
            raise FileNotFoundError(f"Knowledge directory does not exist: {root}")
        paths = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.casefold() in SUPPORTED_KNOWLEDGE_SUFFIXES
        ]
        return self.ingest_paths(paths, root=root, metadata=metadata)
