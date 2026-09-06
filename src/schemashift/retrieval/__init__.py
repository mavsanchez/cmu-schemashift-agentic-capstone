"""Migration-document chunking, ingestion, and semantic search."""

from .chunking import (
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
    chunk_document,
    chunk_file,
    chunk_json_document,
    chunk_text,
    stable_document_id,
)
from .embeddings import EmbeddingDimensionError, embed_text, embed_texts, validate_embedding
from .ingest import SUPPORTED_KNOWLEDGE_SUFFIXES, KnowledgeIngestor
from .redis_vector import KnowledgeStore, RedisKnowledgeStore
from .schemas import IngestionReport, KnowledgeChunk, KnowledgeSearchResult

__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_OVERLAP_CHARS",
    "EmbeddingDimensionError",
    "IngestionReport",
    "KnowledgeChunk",
    "KnowledgeIngestor",
    "KnowledgeSearchResult",
    "KnowledgeStore",
    "RedisKnowledgeStore",
    "SUPPORTED_KNOWLEDGE_SUFFIXES",
    "chunk_document",
    "chunk_file",
    "chunk_json_document",
    "chunk_text",
    "embed_text",
    "embed_texts",
    "stable_document_id",
    "validate_embedding",
]
