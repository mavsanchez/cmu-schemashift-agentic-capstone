"""Validated, centralized runtime settings for SchemaShift."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """SchemaShift settings loaded from ``SCHEMASHIFT_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SCHEMASHIFT_",
        extra="ignore",
        case_sensitive=False,
    )

    model_provider: Literal["ollama", "mock"] = "ollama"
    chat_model: str = "gpt-oss:20b"
    embedding_model: str = "bge-m3"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_keep_alive: str = "10m"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    model_max_attempts: int = Field(default=3, ge=1, le=10)

    max_migration_revisions: int = Field(default=10, ge=0, le=50)
    low_confidence_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    tot_ambiguity_margin: float = Field(default=0.10, ge=0.0, le=1.0)
    tot_branching_factor: int = Field(default=3, ge=1, le=3)
    tot_beam_width: int = Field(default=2, ge=1, le=2)
    tot_max_depth: int = Field(default=3, ge=1, le=3)

    postgres_dsn: str = "postgresql://schemashift:schemashift@127.0.0.1:5432/schemashift"
    postgres_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)

    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_checkpoint_prefix: str = "schemashift:checkpoint"
    redis_memory_prefix: str = "schemashift:memory"
    redis_knowledge_prefix: str = "schemashift:knowledge"
    redis_memory_index: str = "schemashift-memory-v1"
    redis_knowledge_index: str = "schemashift-knowledge-v1"
    retrieval_top_k: int = Field(default=3, ge=1, le=100)
    embedding_dimensions: int = Field(default=1024, ge=1)
    knowledge_chunk_chars: int = Field(default=1200, ge=128)
    knowledge_chunk_overlap_chars: int = Field(default=150, ge=0)

    float_absolute_tolerance: float = Field(default=1e-6, ge=0.0)
    float_relative_tolerance: float = Field(default=1e-6, ge=0.0)
    query_timeout_seconds: float = Field(default=10.0, gt=0.0, le=300.0)
    result_row_limit: int = Field(default=10_000, ge=1, le=1_000_000)
    mismatch_sample_limit: int = Field(default=20, ge=0, le=10_000)
    upload_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1)

    data_root: Path = Path("data")
    upload_root: Path = Path("data/generated/uploads")
    artifact_root: Path = Path("data/generated/migrations")

    gradio_host: str = "127.0.0.1"
    gradio_port: int = Field(default=7860, ge=1, le=65_535)

    @model_validator(mode="after")
    def validate_knowledge_chunking(self) -> Settings:
        if self.knowledge_chunk_overlap_chars >= self.knowledge_chunk_chars:
            raise ValueError(
                "knowledge_chunk_overlap_chars must be smaller than knowledge_chunk_chars"
            )
        return self

    @property
    def max_candidate_attempts(self) -> int:
        """The initial candidate plus the configured revision allowance."""

        return self.max_migration_revisions + 1

    @property
    def provider(self) -> str:
        """Compatibility shorthand used by provider factories."""

        return self.model_provider

    @property
    def top_k(self) -> int:
        """Compatibility shorthand used by retrieval services."""

        return self.retrieval_top_k


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable-by-convention settings instance."""

    return Settings()


def reset_settings_cache() -> None:
    """Clear cached settings, primarily for tests that modify the environment."""

    get_settings.cache_clear()
