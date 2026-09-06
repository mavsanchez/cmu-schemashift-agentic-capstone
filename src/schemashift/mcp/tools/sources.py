"""Bounded reads and scoped listing for registered uploaded sources."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from schemashift.mcp.context import ToolContext
from schemashift.mcp.registry import (
    RegistryError,
    coerce_source_record,
    resolve_source,
)

from .sql_executor import _json_value


def list_uploaded_sources(
    conversation_id: str,
    session_id: str,
    *,
    context: ToolContext,
) -> dict[str, Any]:
    """Return metadata only for sources in the requested conversation/session."""

    try:
        try:
            values = context.registry.list_sources(str(conversation_id), str(session_id))
        except TypeError:
            # PostgreSQL SourceRepository uses a keyword-only session filter.
            values = context.registry.list_sources(str(conversation_id), session_id=str(session_id))
        sources = []
        for value in values:
            source = coerce_source_record(value)
            # Do not trust an adapter to apply scope correctly.
            if source.conversation_id != str(conversation_id) or source.session_id != str(
                session_id
            ):
                continue
            sources.append(source.summary())
        sources.sort(key=lambda item: (item["original_name"], item["source_id"]))
    except (RegistryError, TypeError, ValueError) as exc:
        error = {"code": "source_listing_failed", "message": str(exc)}
        return {
            "ok": False,
            "conversation_id": str(conversation_id),
            "session_id": str(session_id),
            "sources": [],
            "count": 0,
            "error": error,
            "errors": [error],
        }
    return {
        "ok": True,
        "conversation_id": str(conversation_id),
        "session_id": str(session_id),
        "sources": sources,
        "count": len(sources),
        "errors": [],
    }


def _read_text(path: Path, max_chars: int) -> tuple[str, bool]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        text = handle.read(max_chars + 1)
    return text[:max_chars], len(text) > max_chars


def _csv_preview(text: str, limit: int = 20) -> dict[str, Any]:
    reader = csv.reader(io.StringIO(text))
    rows = []
    truncated = False
    for index, row in enumerate(reader):
        if index > limit:
            truncated = True
            break
        rows.append(row)
    return {
        "columns": rows[0] if rows else [],
        "rows": rows[1 : limit + 1],
        "preview_truncated": truncated,
    }


def _parquet_preview(path: Path, limit: int = 20) -> dict[str, Any]:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("duckdb is required to preview Parquet sources") from exc
    connection = duckdb.connect(database=":memory:")
    try:
        relation = connection.from_parquet(str(path))
        preview = relation.limit(limit + 1)
        rows = preview.fetchall()
        return {
            "columns": [
                {"name": name, "type": str(column_type)}
                for name, column_type in zip(relation.columns, relation.types, strict=True)
            ],
            "rows": [[_json_value(value) for value in row] for row in rows[:limit]],
            "preview_truncated": len(rows) > limit,
        }
    finally:
        connection.close()


def read_source(
    source_id: str,
    *,
    context: ToolContext,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Read bounded text or metadata/preview from a registered source ID."""

    limit = min(max_chars or context.read_source_char_limit, context.read_source_char_limit)
    if limit < 1:
        error = {"code": "invalid_limit", "message": "max_chars must be positive"}
        return {
            "ok": False,
            "source_id": str(source_id),
            "error": error,
            "errors": [error],
        }
    try:
        source = resolve_source(context.registry, str(source_id))
    except (RegistryError, TypeError, ValueError) as exc:
        error = {"code": "unknown_source", "message": str(exc)}
        return {
            "ok": False,
            "source_id": str(source_id),
            "error": error,
            "errors": [error],
        }
    if not source.path.is_file():
        message = f"Registered source file does not exist: {source.source_id}"
        error = {"code": "source_unavailable", "message": message}
        return {
            "ok": False,
            "source_id": source.source_id,
            "error": error,
            "errors": [error],
        }

    suffix = source.path.suffix.lower()
    summary = source.summary()
    try:
        if suffix == ".parquet":
            content = {
                "kind": "parquet",
                "binary": True,
                "preview": _parquet_preview(source.path),
            }
        elif suffix in {".sql", ".md", ".json", ".csv"}:
            text, truncated = _read_text(source.path, limit)
            content = {"kind": "text", "text": text, "truncated": truncated}
            if suffix == ".json":
                try:
                    content["json"] = json.loads(text) if not truncated else None
                except json.JSONDecodeError as exc:
                    content["parse_error"] = str(exc)
            elif suffix == ".csv":
                content["preview"] = _csv_preview(text)
        else:
            content = {
                "kind": "binary",
                "binary": True,
                "message": "Binary content is not returned by the source tool",
            }
    except Exception as exc:
        error = {"code": "source_read_failed", "message": str(exc)}
        return {
            "ok": False,
            "source_id": source.source_id,
            "error": error,
            "errors": [error],
        }
    return {
        "ok": True,
        "source_id": source.source_id,
        "source": summary,
        "content": content,
        "errors": [],
    }


__all__ = ["list_uploaded_sources", "read_source"]
