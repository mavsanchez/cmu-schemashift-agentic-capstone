from __future__ import annotations

from pathlib import Path

import pytest

from schemashift.mcp import (
    InMemorySourceRegistry,
    PathOutsideRegistryError,
    SourceRecord,
    ToolContext,
)
from schemashift.mcp.tools.sources import list_uploaded_sources, read_source


def _source(
    source_id: str, path: Path, conversation: str = "c1", session: str = "s1"
) -> SourceRecord:
    return SourceRecord(
        source_id,
        conversation,
        session,
        path,
        role="migration_knowledge",
        original_name=path.name,
    )


def test_lists_only_exact_conversation_and_session(tmp_path: Path) -> None:
    first, second = tmp_path / "first.md", tmp_path / "second.md"
    first.write_text("first")
    second.write_text("second")
    registry = InMemorySourceRegistry((tmp_path,))
    registry.register_source(_source("one", first))
    registry.register_source(_source("two", second, session="s2"))
    result = list_uploaded_sources("c1", "s1", context=ToolContext(registry))
    assert result["ok"]
    assert result["count"] == 1
    assert result["sources"][0]["source_id"] == "one"
    assert "path" not in result["sources"][0]


def test_reads_text_with_hard_context_cap(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("abcdefghij")
    registry = InMemorySourceRegistry((tmp_path,))
    registry.register_source(_source("notes", path))
    context = ToolContext(registry, read_source_char_limit=5)
    result = read_source("notes", context=context, max_chars=1000)
    assert result["ok"]
    assert result["content"]["text"] == "abcde"
    assert result["content"]["truncated"]


def test_reads_csv_preview(tmp_path: Path) -> None:
    path = tmp_path / "items.csv"
    path.write_text("id,name\n1,one\n2,two\n")
    registry = InMemorySourceRegistry((tmp_path,))
    registry.register_source(_source("items", path))
    result = read_source("items", context=ToolContext(registry))
    assert result["content"]["preview"]["columns"] == ["id", "name"]
    assert result["content"]["preview"]["rows"][0] == ["1", "one"]


def test_registry_rejects_path_outside_controlled_root(tmp_path: Path) -> None:
    controlled = tmp_path / "controlled"
    controlled.mkdir()
    outside = tmp_path / "outside.sql"
    outside.write_text("SELECT 1")
    registry = InMemorySourceRegistry((controlled,))
    with pytest.raises(PathOutsideRegistryError):
        registry.register_source(_source("outside", outside))


def test_unknown_read_never_interprets_id_as_path(tmp_path: Path) -> None:
    path = tmp_path / "private.sql"
    path.write_text("SELECT 1")
    registry = InMemorySourceRegistry((tmp_path,))
    result = read_source(str(path), context=ToolContext(registry))
    assert not result["ok"]
    assert result["error"]["code"] == "unknown_source"
