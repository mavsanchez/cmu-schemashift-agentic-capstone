"""Custom event emission helpers used by every graph node."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from langgraph.config import get_stream_writer


def correlated(state: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(event)
    result.setdefault("timestamp", datetime.now(UTC).isoformat())
    for field in ("conversation_id", "session_id", "run_id"):
        result.setdefault(field, str(state.get(field, "")))
    return result


def emit(state: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    """Emit when running in LangGraph and remain usable in direct unit tests."""

    payload = correlated(state, event)
    with suppress(RuntimeError):
        get_stream_writer()(payload)
    return payload


def component(state: Mapping[str, Any], name: str, status: str, detail: str) -> dict[str, Any]:
    return emit(
        state,
        {
            "type": "component_status",
            "component": name,
            "status": status,
            "detail": detail,
        },
    )


def activity(
    state: Mapping[str, Any],
    label: str,
    detail: str,
    *,
    level: str = "info",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return emit(
        state,
        {
            "type": "activity",
            "level": level,
            "label": label,
            "detail": detail,
            "metadata": dict(metadata or {}),
        },
    )
