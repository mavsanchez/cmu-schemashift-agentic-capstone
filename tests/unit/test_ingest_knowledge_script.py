from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ingest_knowledge.py"
_SPEC = importlib.util.spec_from_file_location("schemashift_ingest_knowledge_script", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
ingest_knowledge = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ingest_knowledge)


def test_ingest_script_probes_before_index_and_uses_central_chunk_settings(
    monkeypatch,
    tmp_path,
) -> None:
    events: list[Any] = []
    settings = SimpleNamespace(
        redis_url="redis://local",
        redis_knowledge_index="knowledge-index",
        redis_knowledge_prefix="knowledge-prefix",
        embedding_dimensions=1024,
        knowledge_chunk_chars=1200,
        knowledge_chunk_overlap_chars=150,
    )

    class Client:
        def close(self) -> None:
            events.append("close")

    class Provider:
        def embed(self, text: str) -> list[float]:
            events.append(("probe", text))
            return [0.0] * 1024

    class ProviderFactory:
        @staticmethod
        def from_settings(_settings: Any) -> Provider:
            return Provider()

    class Store:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def ensure_index(self) -> bool:
            events.append("index")
            return True

    class Ingestor:
        def __init__(self, _store: Any, *, max_chars: int, overlap_chars: int) -> None:
            events.append(("chunk_policy", max_chars, overlap_chars))

        def ingest_directory(self, path: Any) -> Any:
            events.append(("ingest", path))
            return SimpleNamespace(
                documents=1,
                chunks=1,
                inserted=1,
                updated=0,
                unchanged=0,
                removed=0,
            )

    monkeypatch.setattr(ingest_knowledge, "_project_settings", lambda: settings)
    monkeypatch.setattr(ingest_knowledge, "create_redis_client", lambda _url: Client())
    monkeypatch.setattr(ingest_knowledge, "OllamaProvider", ProviderFactory)
    monkeypatch.setattr(ingest_knowledge, "KnowledgeStore", Store)
    monkeypatch.setattr(ingest_knowledge, "KnowledgeIngestor", Ingestor)

    assert ingest_knowledge.main(["--knowledge-dir", str(tmp_path)]) == 0
    assert events[:3] == [
        ("probe", "SchemaShift knowledge-index dimension probe"),
        "index",
        ("chunk_policy", 1200, 150),
    ]


def test_ingest_script_refuses_wrong_embedding_dimension_before_index(
    monkeypatch,
    tmp_path,
) -> None:
    events: list[str] = []
    settings = SimpleNamespace(
        redis_url="redis://local",
        embedding_dimensions=1024,
    )

    class Client:
        def close(self) -> None:
            events.append("close")

    class Provider:
        def embed(self, _text: str) -> list[float]:
            events.append("probe")
            return [0.0] * 3

    class ProviderFactory:
        @staticmethod
        def from_settings(_settings: Any) -> Provider:
            return Provider()

    monkeypatch.setattr(ingest_knowledge, "_project_settings", lambda: settings)
    monkeypatch.setattr(ingest_knowledge, "create_redis_client", lambda _url: Client())
    monkeypatch.setattr(ingest_knowledge, "OllamaProvider", ProviderFactory)

    try:
        ingest_knowledge.main(["--knowledge-dir", str(tmp_path)])
    except ValueError as exc:
        assert "returned 3 dimensions" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("dimension mismatch should fail before index construction")

    assert events == ["probe", "close"]
