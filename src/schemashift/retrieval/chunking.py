"""Logical, deterministic chunking for local Markdown and JSON knowledge."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .schemas import KnowledgeChunk

DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 150
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_JSON_RECORD_METADATA_FIELDS = (
    "schema_version",
    "table",
    "old_table",
    "new_table",
    "topic",
    "effective_date",
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_document_id(document: str) -> str:
    """A stable ID tied to the source identity rather than its current bytes."""

    normalized = document.replace("\\", "/").strip()
    return _sha256(normalized)


def _front_matter(text: str) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return text, {}
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        return text, {}

    metadata: dict[str, Any] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key, raw_value = key.strip(), raw_value.strip()
        if not key:
            continue
        try:
            metadata[key] = json.loads(raw_value)
        except json.JSONDecodeError:
            metadata[key] = raw_value.strip("\"'")
    return "\n".join(lines[end + 1 :]), metadata


def _paragraphs(text: str) -> list[str]:
    """Split on blank lines while keeping fenced code blocks intact."""

    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            block = "\n".join(current).strip()
            if block:
                blocks.append(block)
            current = []
        else:
            current.append(line.rstrip())
    block = "\n".join(current).strip()
    if block:
        blocks.append(block)
    return blocks


def _tail(text: str, size: int) -> str:
    if size <= 0 or len(text) <= size:
        return text if size > 0 else ""
    start = len(text) - size
    boundary = text.find(" ", start)
    if boundary == -1:
        boundary = start
    return text[boundary:].strip()


def _split_long(text: str, *, limit: int, overlap: int) -> list[str]:
    parts: list[str] = []
    cursor = 0
    while cursor < len(text):
        end = min(cursor + limit, len(text))
        if end < len(text):
            candidates = [
                text.rfind("\n", cursor + max(1, limit // 2), end),
                text.rfind(". ", cursor + max(1, limit // 2), end),
                text.rfind(" ", cursor + max(1, limit // 2), end),
            ]
            boundary = max(candidates)
            if boundary > cursor:
                end = boundary + (1 if text[boundary : boundary + 2] == ". " else 0)
        part = text[cursor:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        next_cursor = max(cursor + 1, end - overlap)
        while next_cursor < end and not text[next_cursor].isspace():
            next_cursor += 1
        cursor = next_cursor
    return parts


def _pack_blocks(blocks: Sequence[str], *, limit: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long(block, limit=limit, overlap=overlap))
            continue
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        chunks.append(current)
        prefix = _tail(current, overlap)
        current = f"{prefix}\n\n{block}".strip() if prefix else block
        if len(current) > limit:
            chunks.extend(_split_long(current, limit=limit, overlap=overlap)[:-1])
            current = _split_long(current, limit=limit, overlap=overlap)[-1]
    if current:
        chunks.append(current)
    return chunks


def _make_chunks(
    sections: Sequence[tuple[str | None, str]],
    *,
    document: str,
    metadata: Mapping[str, Any],
    section_metadata: Sequence[Mapping[str, Any]] | None = None,
    max_chars: int,
    overlap_chars: int,
) -> list[KnowledgeChunk]:
    if section_metadata is not None and len(section_metadata) != len(sections):
        raise ValueError("section_metadata must align with sections")
    document_id = stable_document_id(document)
    chunks: list[KnowledgeChunk] = []
    ordinal = 0
    for section_index, (heading, body) in enumerate(sections):
        record_metadata = section_metadata[section_index] if section_metadata is not None else {}
        chunk_metadata = {**dict(metadata), **dict(record_metadata)}
        heading_prefix = f"# {heading}\n\n" if heading else ""
        body_limit = max_chars - len(heading_prefix)
        if body_limit < 32:
            heading_prefix = ""
            body_limit = max_chars
        blocks = _paragraphs(body)
        if not blocks and heading:
            blocks = [heading]
        for packed in _pack_blocks(blocks, limit=body_limit, overlap=overlap_chars):
            text = f"{heading_prefix}{packed}".strip()
            digest = _sha256(text)
            chunk_id = _sha256(f"{document_id}\x1f{ordinal}\x1f{heading or ''}\x1f{digest}")
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    document=document,
                    text=text,
                    ordinal=ordinal,
                    heading=heading,
                    content_sha256=digest,
                    metadata=chunk_metadata,
                )
            )
            ordinal += 1
    return chunks


def chunk_document(
    text: str,
    document: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[KnowledgeChunk]:
    """Chunk Markdown/plain text at headings and paragraph boundaries."""

    if not document.strip():
        raise ValueError("document is required")
    if max_chars < 128:
        raise ValueError("max_chars must be at least 128")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be non-negative and smaller than max_chars")
    if not text.strip():
        return []

    body, front_matter = _front_matter(text)
    merged_metadata = {**front_matter, **dict(metadata or {})}
    sections: list[tuple[str | None, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            section_text = "\n".join(current_lines).strip()
            if section_text:
                sections.append((current_heading, section_text))
            current_heading = match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
    section_text = "\n".join(current_lines).strip()
    if section_text or current_heading:
        sections.append((current_heading, section_text))
    if not sections:
        sections = [(None, body.strip())]
    return _make_chunks(
        sections,
        document=document,
        metadata=merged_metadata,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )


def chunk_json_document(
    value: Any,
    document: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[KnowledgeChunk]:
    """Chunk JSON by top-level record/key before applying size limits."""

    sections: list[tuple[str | None, str]] = []
    record_metadata: list[dict[str, Any]] = []

    def append_record(heading: str | None, item: Any) -> None:
        sections.append((heading, json.dumps(item, indent=2, sort_keys=True)))
        if isinstance(item, Mapping):
            record_metadata.append(
                {field: item[field] for field in _JSON_RECORD_METADATA_FIELDS if field in item}
            )
        else:
            record_metadata.append({})

    if isinstance(value, Mapping) and any(field in value for field in _JSON_RECORD_METADATA_FIELDS):
        # A top-level object carrying record fields is itself one logical
        # record, rather than a container whose individual properties should
        # become unrelated chunks.
        append_record("record 0", value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            append_record(str(key), item)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            append_record(f"record {index}", item)
    else:
        append_record(None, value)
    return _make_chunks(
        sections,
        document=document,
        metadata=dict(metadata or {}),
        section_metadata=record_metadata,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )


def chunk_file(
    path: str | Path,
    *,
    document: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[KnowledgeChunk]:
    source = Path(path)
    document_name = document or source.name
    text = source.read_text(encoding="utf-8")
    if source.suffix.casefold() == ".json":
        return chunk_json_document(
            json.loads(text),
            document_name,
            metadata=metadata,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
    return chunk_document(
        text,
        document_name,
        metadata=metadata,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )


# Convenient name for callers ingesting uploaded strings rather than files.
chunk_text = chunk_document
