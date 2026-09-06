"""Redis connection and RediSearch vector-index setup helpers."""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable
from contextlib import suppress
from typing import Any, Literal

VECTOR_DIMENSIONS = 1024
VECTOR_DATA_TYPE = "FLOAT32"
VECTOR_DISTANCE_METRIC = "COSINE"

DEFAULT_MEMORY_INDEX = "idx:schemashift:memory"
DEFAULT_MEMORY_PREFIX = "schemashift:memory:"
DEFAULT_KNOWLEDGE_INDEX = "idx:schemashift:knowledge"
DEFAULT_KNOWLEDGE_PREFIX = "schemashift:knowledge:"


class RedisUnavailableError(RuntimeError):
    """Raised when required Redis persistence/search is unavailable."""


class RedisIndexError(RuntimeError):
    """Raised when a SchemaShift vector index cannot be prepared."""


def create_redis_client(redis_url: str, *, verify: bool = True) -> Any:
    """Create a binary-safe Redis client.

    ``decode_responses=False`` is required because vector fields contain packed
    float bytes and is also required by the LangGraph Redis checkpointer when a
    client is supplied manually.
    """

    if not redis_url:
        raise ValueError("redis_url is required")
    try:
        from redis import Redis
    except ImportError as exc:  # pragma: no cover - dependency installation issue
        raise RedisUnavailableError("Redis support requires the 'redis' package") from exc

    client = Redis.from_url(redis_url, decode_responses=False)
    if verify:
        try:
            client.ping()
        except Exception as exc:
            with suppress(Exception):
                client.close()
            raise RedisUnavailableError(f"Could not connect to Redis at {redis_url}") from exc
    return client


def vector_to_bytes(vector: Iterable[float], *, dimensions: int = VECTOR_DIMENSIONS) -> bytes:
    """Validate and serialize a Redis FLOAT32 vector in little-endian order."""

    values = [float(value) for value in vector]
    if len(values) != dimensions:
        raise ValueError(f"Expected a {dimensions}-dimension vector, received {len(values)}")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Vector values must all be finite")
    return struct.pack(f"<{dimensions}f", *values)


def bytes_to_vector(value: bytes, *, dimensions: int = VECTOR_DIMENSIONS) -> list[float]:
    expected_bytes = dimensions * 4
    if len(value) != expected_bytes:
        raise ValueError(f"Expected {expected_bytes} vector bytes, received {len(value)}")
    return list(struct.unpack(f"<{dimensions}f", value))


def _is_missing_index_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    return "unknown index name" in message or "no such index" in message


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _pairs(values: Any) -> dict[str, Any]:
    if not isinstance(values, (list, tuple)):
        return {}
    return {
        _text(values[index]).casefold(): values[index + 1] for index in range(0, len(values) - 1, 2)
    }


def _validate_existing_index(
    info: Any,
    *,
    index_name: str,
    key_prefix: str,
    dimensions: int,
) -> None:
    """Fail fast if an existing named index has an incompatible vector schema."""

    if not isinstance(info, dict):
        return
    definition = info.get("index_definition", info.get(b"index_definition"))
    definition_fields = _pairs(definition)
    prefixes = definition_fields.get("prefixes")
    if isinstance(prefixes, (list, tuple)):
        actual_prefixes = {_text(prefix) for prefix in prefixes}
        if key_prefix not in actual_prefixes:
            raise RedisIndexError(
                f"Redis index {index_name!r} does not cover expected prefix {key_prefix!r}"
            )

    attributes = info.get("attributes", info.get(b"attributes"))
    if not isinstance(attributes, (list, tuple)):
        return
    vector_fields = []
    for raw_attribute in attributes:
        attribute = _pairs(raw_attribute)
        if _text(attribute.get("identifier", "")) == "embedding":
            vector_fields.append(attribute)
    if not vector_fields:
        raise RedisIndexError(f"Redis index {index_name!r} has no embedding vector field")
    vector = vector_fields[0]
    actual = {
        "type": _text(vector.get("type", "")).upper(),
        "data_type": _text(vector.get("data_type", "")).upper(),
        "dim": int(vector.get("dim", 0)),
        "distance_metric": _text(vector.get("distance_metric", "")).upper(),
    }
    expected = {
        "type": "VECTOR",
        "data_type": VECTOR_DATA_TYPE,
        "dim": dimensions,
        "distance_metric": VECTOR_DISTANCE_METRIC,
    }
    if actual != expected:
        raise RedisIndexError(
            f"Redis index {index_name!r} has incompatible embedding schema: {actual!r}"
        )


def _ensure_index(
    client: Any,
    *,
    index_name: str,
    key_prefix: str,
    kind: Literal["memory", "knowledge"],
    dimensions: int,
) -> bool:
    """Create one HASH/HNSW index if absent; return whether it was created."""

    if dimensions != VECTOR_DIMENSIONS:
        raise ValueError(
            f"SchemaShift indexes are fixed at {VECTOR_DIMENSIONS} dimensions, got {dimensions}"
        )
    try:
        from redis.commands.search.field import NumericField, TagField, TextField, VectorField
        from redis.commands.search.index_definition import IndexDefinition, IndexType
    except ImportError as exc:  # pragma: no cover - dependency installation issue
        raise RedisIndexError("RediSearch support requires redis-py search modules") from exc

    key_prefix = f"{key_prefix.rstrip(':')}:"
    search = client.ft(index_name)
    try:
        info = search.info()
    except Exception as exc:
        if not _is_missing_index_error(exc):
            raise RedisIndexError(f"Could not inspect Redis index {index_name!r}") from exc
    else:
        _validate_existing_index(
            info,
            index_name=index_name,
            key_prefix=key_prefix,
            dimensions=dimensions,
        )
        return False

    vector = VectorField(
        "embedding",
        "HNSW",
        {
            "TYPE": VECTOR_DATA_TYPE,
            "DIM": dimensions,
            "DISTANCE_METRIC": VECTOR_DISTANCE_METRIC,
        },
    )
    if kind == "memory":
        fields = [
            TagField("memory_id"),
            TagField("conversation_id"),
            TextField("text"),
            TagField("memory_type"),
            TagField("source_type"),
            TagField("source_id"),
            NumericField("confidence"),
            vector,
        ]
    else:
        fields = [
            TagField("chunk_id"),
            TagField("document_id"),
            TagField("document"),
            TextField("text"),
            TagField("schema_version"),
            TagField("old_table"),
            TagField("new_table"),
            TagField("topic"),
            TagField("effective_date"),
            vector,
        ]

    try:
        search.create_index(
            fields,
            definition=IndexDefinition(prefix=[key_prefix], index_type=IndexType.HASH),
        )
    except Exception as exc:
        # Another local worker can win the creation race.  Confirm the index is
        # now readable before treating the operation as failed.
        try:
            search.info()
        except Exception:
            raise RedisIndexError(f"Could not create Redis index {index_name!r}") from exc
        return False
    return True


def ensure_memory_index(
    client: Any,
    *,
    index_name: str = DEFAULT_MEMORY_INDEX,
    key_prefix: str = DEFAULT_MEMORY_PREFIX,
    dimensions: int = VECTOR_DIMENSIONS,
) -> bool:
    return _ensure_index(
        client,
        index_name=index_name,
        key_prefix=key_prefix,
        kind="memory",
        dimensions=dimensions,
    )


def ensure_knowledge_index(
    client: Any,
    *,
    index_name: str = DEFAULT_KNOWLEDGE_INDEX,
    key_prefix: str = DEFAULT_KNOWLEDGE_PREFIX,
    dimensions: int = VECTOR_DIMENSIONS,
) -> bool:
    return _ensure_index(
        client,
        index_name=index_name,
        key_prefix=key_prefix,
        kind="knowledge",
        dimensions=dimensions,
    )
