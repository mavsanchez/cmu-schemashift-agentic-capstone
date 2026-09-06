"""Embedding validation shared by retrieval callers."""

from __future__ import annotations

from collections.abc import Sequence

from schemashift.memory.redis_client import VECTOR_DIMENSIONS
from schemashift.model import ModelProvider


class EmbeddingDimensionError(ValueError):
    pass


def validate_embedding(
    embedding: Sequence[float],
    *,
    dimensions: int = VECTOR_DIMENSIONS,
) -> list[float]:
    values = [float(value) for value in embedding]
    if len(values) != dimensions:
        raise EmbeddingDimensionError(
            f"Expected a {dimensions}-dimension embedding, received {len(values)}"
        )
    return values


def embed_text(
    provider: ModelProvider,
    text: str,
    *,
    dimensions: int = VECTOR_DIMENSIONS,
) -> list[float]:
    return validate_embedding(provider.embed(text), dimensions=dimensions)


def embed_texts(
    provider: ModelProvider,
    texts: Sequence[str],
    *,
    dimensions: int = VECTOR_DIMENSIONS,
) -> list[list[float]]:
    vectors = provider.embed_many(texts)
    if len(vectors) != len(texts):
        raise EmbeddingDimensionError("Embedding provider returned the wrong batch length")
    return [validate_embedding(vector, dimensions=dimensions) for vector in vectors]
