"""Create/update the local Redis knowledge index from ``data/knowledge``."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from schemashift.memory import (
    DEFAULT_KNOWLEDGE_INDEX,
    DEFAULT_KNOWLEDGE_PREFIX,
    create_redis_client,
)
from schemashift.model import OllamaProvider
from schemashift.retrieval import KnowledgeIngestor, KnowledgeStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _project_settings() -> Any:
    from schemashift.config.settings import get_settings

    return get_settings()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--knowledge-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "knowledge",
        help="Directory containing Markdown and JSON migration knowledge",
    )
    parser.add_argument("--redis-url", help="Override SCHEMASHIFT_REDIS_URL")
    parser.add_argument("--index-name", help="Override the configured knowledge index")
    parser.add_argument("--key-prefix", help="Override the configured knowledge key prefix")
    parser.add_argument(
        "--max-chars",
        type=int,
        help="Override SCHEMASHIFT_KNOWLEDGE_CHUNK_CHARS",
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        help="Override SCHEMASHIFT_KNOWLEDGE_CHUNK_OVERLAP_CHARS",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = _project_settings()
    redis_url = args.redis_url or settings.redis_url
    client = create_redis_client(redis_url)
    try:
        model = OllamaProvider.from_settings(settings)
        probe = model.embed("SchemaShift knowledge-index dimension probe")
        expected_dimensions = int(getattr(settings, "embedding_dimensions", 1024))
        if len(probe) != expected_dimensions:
            raise ValueError(
                f"Embedding probe returned {len(probe)} dimensions; expected {expected_dimensions}"
            )
        store = KnowledgeStore(
            client,
            model,
            index_name=(
                args.index_name
                or getattr(settings, "redis_knowledge_index", DEFAULT_KNOWLEDGE_INDEX)
            ),
            key_prefix=(
                args.key_prefix
                or getattr(settings, "redis_knowledge_prefix", DEFAULT_KNOWLEDGE_PREFIX)
            ),
            embedding_dimensions=expected_dimensions,
        )
        created = store.ensure_index()
        report = KnowledgeIngestor(
            store,
            max_chars=(
                args.max_chars
                if args.max_chars is not None
                else int(settings.knowledge_chunk_chars)
            ),
            overlap_chars=(
                args.overlap_chars
                if args.overlap_chars is not None
                else int(settings.knowledge_chunk_overlap_chars)
            ),
        ).ingest_directory(args.knowledge_dir)
        state = "created" if created else "ready"
        print(
            f"Knowledge index {state}: documents={report.documents}, chunks={report.chunks}, "
            f"inserted={report.inserted}, updated={report.updated}, "
            f"unchanged={report.unchanged}, "
            f"removed={report.removed}"
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
