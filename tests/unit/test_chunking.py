from __future__ import annotations

import pytest

from schemashift.config import Settings
from schemashift.retrieval import chunk_document, chunk_json_document
from schemashift.retrieval.chunking import DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS
from schemashift.retrieval.ingest import KnowledgeIngestor


def test_default_chunk_target_and_overlap_match_runtime_contract() -> None:
    assert DEFAULT_MAX_CHARS == 1200
    assert DEFAULT_OVERLAP_CHARS == 150

    settings = Settings(_env_file=None)
    ingestor = KnowledgeIngestor.from_settings(object(), settings)  # type: ignore[arg-type]
    assert settings.knowledge_chunk_chars == ingestor.max_chars == 1200
    assert settings.knowledge_chunk_overlap_chars == ingestor.overlap_chars == 150


def test_settings_reject_invalid_chunk_overlap() -> None:
    with pytest.raises(ValueError, match="overlap.*smaller"):
        Settings(
            _env_file=None,
            knowledge_chunk_chars=1200,
            knowledge_chunk_overlap_chars=1200,
        )


def test_markdown_chunks_follow_headings_and_are_stable() -> None:
    document = """---
schema_version: v2
topic: customers
---
# Customer identifiers

Rename legacy_customer_id to customer_id.

## Status mapping

Join status_lookup and select status_description.
"""

    first = chunk_document(document, "customer_migration.md", max_chars=256, overlap_chars=24)
    second = chunk_document(document, "customer_migration.md", max_chars=256, overlap_chars=24)

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert [chunk.heading for chunk in first] == ["Customer identifiers", "Status mapping"]
    assert all(len(chunk.text) <= 256 for chunk in first)
    assert all(chunk.metadata["schema_version"] == "v2" for chunk in first)


def test_long_paragraph_respects_chunk_limit() -> None:
    text = "# Mapping\n\n" + " ".join(f"column_{index}" for index in range(300))

    chunks = chunk_document(text, "wide.md", max_chars=220, overlap_chars=30)

    assert len(chunks) > 1
    assert all(0 < len(chunk.text) <= 220 for chunk in chunks)


def test_json_chunks_top_level_records_deterministically() -> None:
    value = [
        {
            "schema_version": "v2",
            "old_table": "customer_v1",
            "new_table": "customer",
            "topic": "customer-identity",
            "effective_date": "2026-01-01",
        },
        {
            "schema_version": "v3",
            "old_table": "order_v1",
            "new_table": "orders",
            "topic": "orders",
            "effective_date": "2026-02-01",
        },
    ]

    chunks = chunk_json_document(
        value,
        "mappings.json",
        metadata={"topic": "document-default", "source_id": "source-1"},
        max_chars=512,
        overlap_chars=16,
    )

    assert len(chunks) == 2
    assert chunks[0].heading == "record 0"
    assert "customer_v1" in chunks[0].text
    assert chunks[0].document_id == chunks[1].document_id
    assert chunks[0].chunk_id != chunks[1].chunk_id
    assert chunks[0].metadata == {
        "source_id": "source-1",
        "schema_version": "v2",
        "old_table": "customer_v1",
        "new_table": "customer",
        "topic": "customer-identity",
        "effective_date": "2026-01-01",
    }
    assert chunks[1].metadata["schema_version"] == "v3"
    assert chunks[1].metadata["topic"] == "orders"


def test_json_record_metadata_survives_splitting_with_stable_unique_ids() -> None:
    value = {
        "schema_version": "v4",
        "table": "sales",
        "old_table": "sales_v3",
        "new_table": "sales",
        "topic": "currency migration",
        "effective_date": "2026-09-05",
        "description": " ".join(f"detail-{index}" for index in range(160)),
    }

    first = chunk_json_document(value, "single-record.json", max_chars=220, overlap_chars=24)
    second = chunk_json_document(value, "single-record.json", max_chars=220, overlap_chars=24)

    assert len(first) > 1
    assert [chunk.ordinal for chunk in first] == list(range(len(first)))
    assert len({chunk.chunk_id for chunk in first}) == len(first)
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert all(
        chunk.metadata
        == {
            "schema_version": "v4",
            "table": "sales",
            "old_table": "sales_v3",
            "new_table": "sales",
            "topic": "currency migration",
            "effective_date": "2026-09-05",
        }
        for chunk in first
    )
