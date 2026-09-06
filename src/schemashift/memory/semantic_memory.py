"""Persistent semantic long-term memory backed by Redis vector search."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from schemashift.model import ModelProvider

from .policy import MemoryWritePolicy
from .redis_client import (
    DEFAULT_MEMORY_INDEX,
    DEFAULT_MEMORY_PREFIX,
    VECTOR_DIMENSIONS,
    ensure_memory_index,
    vector_to_bytes,
)
from .schemas import (
    MemoryCandidate,
    MemoryProvenance,
    MemoryRecord,
    MemorySearchResult,
    MemoryType,
    MemoryWriteResult,
)

GLOBAL_CONVERSATION = "__global__"
_MEMORY_NAMESPACE = uuid.UUID("01b48649-b0d4-5f2a-98c1-c54bc086e9cc")


class MemoryPolicyRejected(ValueError):
    """Raised when a caller tries to bypass the durable-memory policy."""


def _decode(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _document_value(document: Any, name: str, default: Any = None) -> Any:
    return getattr(document, name, default)


def _hash_value(fields: Mapping[Any, Any], name: str, default: Any = None) -> Any:
    return fields.get(name, fields.get(name.encode("utf-8"), default))


def _escape_tag(value: str) -> str:
    # RediSearch TAG values require punctuation and whitespace escaping.
    special = set(r",.<>{}[]\"':;!@#$%^&*()-+=~ ")
    return "".join(f"\\{char}" if char in special else char for char in value)


def _supersession_key(metadata: Mapping[str, Any]) -> str:
    raw = metadata.get("supersession_key")
    if raw is None:
        return ""
    return " ".join(str(raw).split()).casefold()


class SemanticMemoryStore:
    def __init__(
        self,
        client: Any,
        model: ModelProvider,
        *,
        index_name: str = DEFAULT_MEMORY_INDEX,
        key_prefix: str = DEFAULT_MEMORY_PREFIX,
        embedding_dimensions: int = VECTOR_DIMENSIONS,
        policy: MemoryWritePolicy | None = None,
    ) -> None:
        if embedding_dimensions != VECTOR_DIMENSIONS:
            raise ValueError(f"Semantic memory requires {VECTOR_DIMENSIONS}-dimension embeddings")
        self.client = client
        self.model = model
        self.index_name = index_name
        self.key_prefix = f"{key_prefix.rstrip(':')}:"
        self.embedding_dimensions = embedding_dimensions
        self.policy = policy or MemoryWritePolicy()

    @classmethod
    def from_settings(cls, client: Any, model: ModelProvider, settings: Any) -> SemanticMemoryStore:
        return cls(
            client,
            model,
            index_name=getattr(settings, "redis_memory_index", DEFAULT_MEMORY_INDEX),
            key_prefix=getattr(settings, "redis_memory_prefix", DEFAULT_MEMORY_PREFIX),
            embedding_dimensions=int(getattr(settings, "embedding_dimensions", VECTOR_DIMENSIONS)),
            policy=MemoryWritePolicy(
                min_confidence=float(getattr(settings, "low_confidence_threshold", 0.8))
            ),
        )

    def ensure_index(self) -> bool:
        return ensure_memory_index(
            self.client,
            index_name=self.index_name,
            key_prefix=self.key_prefix,
            dimensions=self.embedding_dimensions,
        )

    def _active_conflicts(
        self,
        candidate: MemoryCandidate,
        *,
        memory_id: str,
        supersession_key: str,
    ) -> list[tuple[Any, str]]:
        """Find active, different values in the candidate's explicit fact slot."""

        if not supersession_key:
            return []
        conversation_id = candidate.conversation_id or GLOBAL_CONVERSATION
        conflicts: list[tuple[Any, str]] = []
        for redis_key in self.client.scan_iter(match=f"{self.key_prefix}*"):
            fields = self.client.hgetall(redis_key)
            existing_id = _decode(_hash_value(fields, "memory_id"))
            if not existing_id or existing_id == memory_id:
                continue
            if _decode(_hash_value(fields, "conversation_id")) != conversation_id:
                continue
            if _decode(_hash_value(fields, "memory_type")) != candidate.memory_type.value:
                continue
            if _decode(_hash_value(fields, "superseded_by")):
                continue
            existing_key = _decode(_hash_value(fields, "supersession_key"))
            if not existing_key:
                try:
                    metadata = json.loads(_decode(_hash_value(fields, "metadata_json"), "{}"))
                except (TypeError, json.JSONDecodeError):
                    metadata = {}
                existing_key = _supersession_key(metadata)
            if existing_key.casefold() != supersession_key:
                continue
            if _decode(_hash_value(fields, "text")) == candidate.text:
                continue
            conflicts.append((redis_key, existing_id))
        return conflicts

    def _candidate(
        self,
        text: str,
        *,
        memory_type: MemoryType | str,
        provenance: MemoryProvenance | Mapping[str, Any],
        confidence: float,
        conversation_id: str | None,
        metadata: Mapping[str, Any] | None,
    ) -> MemoryCandidate:
        provenance_model = (
            provenance
            if isinstance(provenance, MemoryProvenance)
            else MemoryProvenance.model_validate(provenance)
        )
        return MemoryCandidate(
            text=" ".join(text.split()),
            memory_type=memory_type,
            provenance=provenance_model,
            confidence=confidence,
            conversation_id=conversation_id or provenance_model.conversation_id,
            metadata=dict(metadata or {}),
        )

    def try_remember(
        self,
        text: str,
        *,
        memory_type: MemoryType | str,
        provenance: MemoryProvenance | Mapping[str, Any],
        confidence: float = 1.0,
        conversation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryWriteResult:
        candidate = self._candidate(
            text,
            memory_type=memory_type,
            provenance=provenance,
            confidence=confidence,
            conversation_id=conversation_id,
            metadata=metadata,
        )
        decision = self.policy.evaluate(candidate)
        if not decision.allowed:
            return MemoryWriteResult(stored=False, reason=decision.reason)

        embedding = self.model.embed(candidate.text)
        packed = vector_to_bytes(embedding, dimensions=self.embedding_dimensions)
        canonical = "\x1f".join(
            [
                candidate.conversation_id or GLOBAL_CONVERSATION,
                candidate.memory_type.value,
                candidate.provenance.source_id,
                candidate.text,
            ]
        )
        memory_id = str(uuid.uuid5(_MEMORY_NAMESPACE, canonical))
        created_at = datetime.now(UTC)
        supersession_key = _supersession_key(candidate.metadata)
        conflicts = self._active_conflicts(
            candidate,
            memory_id=memory_id,
            supersession_key=supersession_key,
        )
        metadata = dict(candidate.metadata)
        if supersession_key:
            metadata["supersession_key"] = supersession_key
            metadata["supersedes"] = sorted(existing_id for _, existing_id in conflicts)
        else:
            metadata.pop("supersedes", None)
        candidate = candidate.model_copy(update={"metadata": metadata})
        record = MemoryRecord(
            **candidate.model_dump(),
            memory_id=memory_id,
            created_at=created_at,
            embedding=embedding,
        )
        key = f"{self.key_prefix}{memory_id}"
        pipeline = self.client.pipeline(transaction=True)
        pipeline.hset(
            key,
            mapping={
                "memory_id": memory_id,
                "conversation_id": candidate.conversation_id or GLOBAL_CONVERSATION,
                "text": candidate.text,
                "memory_type": candidate.memory_type.value,
                "source_type": candidate.provenance.source_type.value,
                "source_id": candidate.provenance.source_id,
                "confidence": candidate.confidence,
                "created_at": created_at.isoformat(),
                "provenance_json": candidate.provenance.model_dump_json(),
                "metadata_json": json.dumps(candidate.metadata, sort_keys=True, default=str),
                "supersession_key": supersession_key,
                "embedding": packed,
            },
        )
        # Replaying the currently selected fact is idempotent and makes it active.
        pipeline.hdel(key, "superseded_by", "superseded_at", "superseded_provenance_json")
        superseded_at = created_at.isoformat()
        superseding_provenance = candidate.provenance.model_dump_json()
        for old_key, _ in conflicts:
            pipeline.hset(
                old_key,
                mapping={
                    "superseded_by": memory_id,
                    "superseded_at": superseded_at,
                    "superseded_provenance_json": superseding_provenance,
                },
            )
        pipeline.execute()
        return MemoryWriteResult(stored=True, reason=decision.reason, memory=record)

    def remember(
        self,
        text: str,
        *,
        memory_type: MemoryType | str,
        provenance: MemoryProvenance | Mapping[str, Any],
        confidence: float = 1.0,
        conversation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryRecord:
        result = self.try_remember(
            text,
            memory_type=memory_type,
            provenance=provenance,
            confidence=confidence,
            conversation_id=conversation_id,
            metadata=metadata,
        )
        if not result.stored or result.memory is None:
            raise MemoryPolicyRejected(result.reason)
        return result.memory

    def recall(
        self,
        query: str,
        *,
        top_k: int = 3,
        conversation_id: str | None = None,
        min_score: float = 0.0,
    ) -> list[MemorySearchResult]:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if not -1.0 <= min_score <= 1.0:
            raise ValueError("min_score must be between -1 and 1")
        vector = vector_to_bytes(self.model.embed(query), dimensions=self.embedding_dimensions)
        try:
            from redis.commands.search.query import Query
        except ImportError as exc:  # pragma: no cover - dependency installation issue
            raise RuntimeError("Semantic recall requires redis-py search modules") from exc

        if conversation_id:
            tags = f"{_escape_tag(conversation_id)}|{_escape_tag(GLOBAL_CONVERSATION)}"
            base = f"@conversation_id:{{{tags}}}"
        else:
            base = "*"
        ceiling = max(10_000, top_k)
        search_k = min(ceiling, max(top_k * 4, top_k + 8))
        while True:
            expression = f"({base})=>[KNN {search_k} @embedding $query_vector AS vector_distance]"
            redis_query = (
                Query(expression)
                .sort_by("vector_distance")
                .return_fields(
                    "memory_id",
                    "conversation_id",
                    "text",
                    "memory_type",
                    "confidence",
                    "created_at",
                    "provenance_json",
                    "metadata_json",
                    "superseded_by",
                    "vector_distance",
                )
                .paging(0, search_k)
                .dialect(2)
            )
            response = self.client.ft(self.index_name).search(
                redis_query,
                query_params={"query_vector": vector},
            )

            results: list[MemorySearchResult] = []
            below_threshold = False
            for document in response.docs:
                if _decode(_document_value(document, "superseded_by")):
                    continue
                distance = float(_decode(_document_value(document, "vector_distance"), "inf"))
                score = max(-1.0, min(1.0, 1.0 - distance))
                if score < min_score:
                    below_threshold = True
                    continue
                provenance = MemoryProvenance.model_validate_json(
                    _decode(_document_value(document, "provenance_json"), "{}")
                )
                metadata = json.loads(_decode(_document_value(document, "metadata_json"), "{}"))
                stored_conversation = _decode(_document_value(document, "conversation_id"))
                record = MemoryRecord(
                    memory_id=_decode(_document_value(document, "memory_id")),
                    conversation_id=(
                        None if stored_conversation == GLOBAL_CONVERSATION else stored_conversation
                    ),
                    text=_decode(_document_value(document, "text")),
                    memory_type=_decode(_document_value(document, "memory_type")),
                    provenance=provenance,
                    confidence=float(_decode(_document_value(document, "confidence"), "1")),
                    created_at=datetime.fromisoformat(
                        _decode(_document_value(document, "created_at"))
                    ),
                    metadata=metadata,
                )
                results.append(MemorySearchResult(memory=record, score=score, distance=distance))
                if len(results) == top_k:
                    return results
            if below_threshold or len(response.docs) < search_k or search_k >= ceiling:
                return results[:top_k]
            search_k = min(ceiling, search_k * 2)
