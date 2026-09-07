"""Idempotent Redis vector store for migration knowledge chunks."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from schemashift.memory.redis_client import (
    DEFAULT_KNOWLEDGE_INDEX,
    DEFAULT_KNOWLEDGE_PREFIX,
    VECTOR_DIMENSIONS,
    ensure_knowledge_index,
    vector_to_bytes,
)
from schemashift.model import ModelProvider

from .embeddings import embed_text, embed_texts
from .schemas import IngestionReport, KnowledgeChunk, KnowledgeDocument, KnowledgeSearchResult

_INDEXED_METADATA = ("schema_version", "old_table", "new_table", "topic", "effective_date")


def _decode(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _value(document: Any, name: str, default: Any = None) -> Any:
    return getattr(document, name, default)


def _escape_tag(value: str) -> str:
    special = set(r",.<>{}[]\"':;!@#$%^&*()-+=~ ")
    return "".join(f"\\{char}" if char in special else char for char in value)


class KnowledgeStore:
    """Redis HASH/HNSW store with content-addressed, replace-by-document ingest."""

    def __init__(
        self,
        client: Any,
        model: ModelProvider,
        *,
        index_name: str = DEFAULT_KNOWLEDGE_INDEX,
        key_prefix: str = DEFAULT_KNOWLEDGE_PREFIX,
        embedding_dimensions: int = VECTOR_DIMENSIONS,
    ) -> None:
        if embedding_dimensions != VECTOR_DIMENSIONS:
            raise ValueError(f"Knowledge retrieval requires {VECTOR_DIMENSIONS} dimensions")
        self.client = client
        self.model = model
        self.index_name = index_name
        self.key_prefix = f"{key_prefix.rstrip(':')}:"
        self.embedding_dimensions = embedding_dimensions
        self.manifest_prefix = f"{self.key_prefix}manifest:"

    @classmethod
    def from_settings(cls, client: Any, model: ModelProvider, settings: Any) -> KnowledgeStore:
        return cls(
            client,
            model,
            index_name=getattr(settings, "redis_knowledge_index", DEFAULT_KNOWLEDGE_INDEX),
            key_prefix=getattr(settings, "redis_knowledge_prefix", DEFAULT_KNOWLEDGE_PREFIX),
            embedding_dimensions=int(getattr(settings, "embedding_dimensions", VECTOR_DIMENSIONS)),
        )

    def ensure_index(self) -> bool:
        return ensure_knowledge_index(
            self.client,
            index_name=self.index_name,
            key_prefix=self.key_prefix,
            dimensions=self.embedding_dimensions,
        )

    def _key(self, chunk_id: str) -> str:
        return f"{self.key_prefix}{chunk_id}"

    def _manifest_key(self, document_id: str) -> str:
        return f"{self.manifest_prefix}{document_id}"

    def ingest(self, chunks: Iterable[KnowledgeChunk]) -> IngestionReport:
        unique = {chunk.chunk_id: chunk for chunk in chunks}
        if not unique:
            return IngestionReport()

        by_document: dict[str, list[KnowledgeChunk]] = defaultdict(list)
        for chunk in sorted(unique.values(), key=lambda item: (item.document_id, item.ordinal)):
            by_document[chunk.document_id].append(chunk)

        chunks_to_write: list[KnowledgeChunk] = []
        inserted = 0
        updated = 0
        unchanged = 0
        for chunk in unique.values():
            key = self._key(chunk.chunk_id)
            existing_hash = self.client.hget(key, "content_sha256")
            existing_metadata = self.client.hget(key, "metadata_json")
            metadata_json = json.dumps(chunk.metadata, sort_keys=True, default=str)
            if (
                _decode(existing_hash) == chunk.content_sha256
                and _decode(existing_metadata) == metadata_json
            ):
                unchanged += 1
            else:
                chunks_to_write.append(chunk)
                if existing_hash is None:
                    inserted += 1
                else:
                    updated += 1

        vectors = embed_texts(
            self.model,
            [chunk.text for chunk in chunks_to_write],
            dimensions=self.embedding_dimensions,
        )
        for chunk, vector in zip(chunks_to_write, vectors, strict=True):
            indexed = {name: str(chunk.metadata.get(name, "")) for name in _INDEXED_METADATA}
            self.client.hset(
                self._key(chunk.chunk_id),
                mapping={
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "document": chunk.document,
                    "text": chunk.text,
                    "ordinal": chunk.ordinal,
                    "heading": chunk.heading or "",
                    "content_sha256": chunk.content_sha256,
                    "metadata_json": json.dumps(chunk.metadata, sort_keys=True, default=str),
                    "embedding": vector_to_bytes(vector, dimensions=self.embedding_dimensions),
                    **indexed,
                },
            )

        removed = 0
        for document_id, document_chunks in by_document.items():
            manifest_key = self._manifest_key(document_id)
            old_ids = {_decode(item) for item in self.client.smembers(manifest_key)}
            new_ids = {chunk.chunk_id for chunk in document_chunks}
            stale = old_ids - new_ids
            if stale:
                removed += int(self.client.delete(*(self._key(chunk_id) for chunk_id in stale)))
            self.client.delete(manifest_key)
            if new_ids:
                self.client.sadd(manifest_key, *sorted(new_ids))

        return IngestionReport(
            documents=len(by_document),
            chunks=len(unique),
            inserted=inserted,
            updated=updated,
            unchanged=unchanged,
            removed=removed,
        )

    def list_documents(self, *, offset: int = 0, limit: int = 200) -> list[KnowledgeDocument]:
        """Read a page of indexed documents without embedding or retrieving passages."""
        from redis.commands.search import reducers
        from redis.commands.search.aggregation import AggregateRequest

        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("offset must be non-negative and limit must be between 1 and 1000")
        request = (
            AggregateRequest("*")
            .load("@document_id", "@document", "@metadata_json")
            .group_by(
                ["@document_id", "@document"],
                reducers.count().alias("chunk_count"),
                reducers.tolist("@metadata_json").alias("metadata"),
            )
            .sort_by("@document", "@document_id")
            .limit(offset, limit)
        )
        response = self.client.ft(self.index_name).aggregate(request)
        documents = []
        for row in response.rows:
            fields = {_decode(key): value for key, value in zip(row[::2], row[1::2], strict=True)}
            documents.append(
                KnowledgeDocument(
                    document_id=_decode(fields["document_id"]),
                    document=_decode(fields["document"]),
                    chunk_count=int(_decode(fields["chunk_count"])),
                    metadata=[
                        json.loads(value) for value in sorted(map(_decode, fields["metadata"]))
                    ],
                )
            )
        return documents

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        filters: Mapping[str, str] | None = None,
        min_score: float = 0.0,
    ) -> list[KnowledgeSearchResult]:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if not -1.0 <= min_score <= 1.0:
            raise ValueError("min_score must be between -1 and 1")
        filters = dict(filters or {})
        unknown = set(filters) - set(_INDEXED_METADATA) - {"document", "document_id"}
        if unknown:
            raise ValueError(f"Unsupported knowledge filter(s): {', '.join(sorted(unknown))}")

        filter_parts = [
            f"@{field}:{{{_escape_tag(str(value))}}}" for field, value in filters.items()
        ]
        base = " ".join(filter_parts) if filter_parts else "*"
        expression = f"({base})=>[KNN {top_k} @embedding $query_vector AS vector_distance]"
        vector = vector_to_bytes(
            embed_text(self.model, query, dimensions=self.embedding_dimensions),
            dimensions=self.embedding_dimensions,
        )
        try:
            from redis.commands.search.query import Query
        except ImportError as exc:  # pragma: no cover - dependency installation issue
            raise RuntimeError("Knowledge search requires redis-py search modules") from exc
        redis_query = (
            Query(expression)
            .sort_by("vector_distance")
            .return_fields(
                "chunk_id",
                "document_id",
                "document",
                "text",
                "ordinal",
                "heading",
                "content_sha256",
                "metadata_json",
                "vector_distance",
            )
            .paging(0, top_k)
            .dialect(2)
        )
        response = self.client.ft(self.index_name).search(
            redis_query,
            query_params={"query_vector": vector},
        )

        results: list[KnowledgeSearchResult] = []
        for document in response.docs:
            distance = float(_decode(_value(document, "vector_distance"), "inf"))
            score = max(-1.0, min(1.0, 1.0 - distance))
            if score < min_score:
                continue
            metadata = json.loads(_decode(_value(document, "metadata_json"), "{}"))
            heading = _decode(_value(document, "heading")) or None
            chunk = KnowledgeChunk(
                chunk_id=_decode(_value(document, "chunk_id")),
                document_id=_decode(_value(document, "document_id")),
                document=_decode(_value(document, "document")),
                text=_decode(_value(document, "text")),
                ordinal=int(_decode(_value(document, "ordinal"), "0")),
                heading=heading,
                content_sha256=_decode(_value(document, "content_sha256")),
                metadata=metadata,
            )
            results.append(KnowledgeSearchResult(chunk=chunk, score=score, distance=distance))
        return results


# Explicit backend name for callers that prefer it over the concise interface.
RedisKnowledgeStore = KnowledgeStore
