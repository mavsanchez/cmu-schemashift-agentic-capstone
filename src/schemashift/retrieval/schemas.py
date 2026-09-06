"""Schemas for knowledge ingestion and semantic retrieval."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeChunk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    document: str = Field(min_length=1)
    text: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    heading: str | None = None
    content_sha256: str = Field(min_length=64, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk: KnowledgeChunk
    score: float = Field(ge=-1.0, le=1.0)
    distance: float = Field(ge=0.0)


class IngestionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    documents: int = Field(default=0, ge=0)
    chunks: int = Field(default=0, ge=0)
    inserted: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)

    def __add__(self, other: IngestionReport) -> IngestionReport:
        return IngestionReport(
            documents=self.documents + other.documents,
            chunks=self.chunks + other.chunks,
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            unchanged=self.unchanged + other.unchanged,
            removed=self.removed + other.removed,
        )
