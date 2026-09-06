"""Rendering and state reduction for the seven-stage activity rail."""

from __future__ import annotations

import html
from collections.abc import Mapping

COMPONENTS: tuple[tuple[str, str, str, str], ...] = (
    ("mcp", "MCP", "Deterministic tools", "link"),
    ("memory", "Memory", "Prior decisions", "brain"),
    ("vector", "Vector DB", "Migration evidence", "cube"),
    ("agent", "Agent", "Orchestration", "bot"),
    ("subagent", "Subagent", "Migration & validation", "subagent"),
    ("model", "AI Model", "Inference", "network"),
    ("guardrail", "Guardrail", "Read-only & review", "shield"),
)

COMPONENT_IDS = tuple(component[0] for component in COMPONENTS)
VALID_STATUSES = ("idle", "active", "done", "error")
STATUS_LABEL = {
    "idle": "Idle",
    "active": "Calling…",
    "done": "Complete",
    "error": "Attention",
}

ICONS = {
    "link": '<path d="M8 8l-3.5 3.5a3 3 0 0 0 4.2 4.2L15 9.4a2 2 0 1 0-2.8-2.8L6.4 12.4a1 1 0 0 0 1.4 1.4L13 8.6"/><path d="M10 16l1.8 1.8a3 3 0 0 0 4.2 0l3-3a3 3 0 0 0 0-4.2L17.4 9"/>',
    "brain": '<path d="M9 4.2A3 3 0 0 0 4.5 7a3.2 3.2 0 0 0-1 5.9A3.5 3.5 0 0 0 7 18.5 3 3 0 0 0 12 17V7a3 3 0 0 0-3-2.8Z"/><path d="M15 4.2A3 3 0 0 1 19.5 7a3.2 3.2 0 0 1 1 5.9A3.5 3.5 0 0 1 17 18.5 3 3 0 0 1 12 17"/><path d="M8 9h4M12 12H7M16 9h-4M12 15h5"/>',
    "cube": '<path d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>',
    "bot": '<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M9 3h6M12 3v4M8.5 12h.01M15.5 12h.01M9 16h6"/>',
    "subagent": '<rect x="5" y="8" width="12" height="10" rx="2.5"/><path d="M8.5 12h.01M13.5 12h.01M9 15h4M11 5v3"/><circle cx="11" cy="4" r="1"/><path d="M17 11h2.5M19.5 11l-1.5-1.5M19.5 11 18 12.5"/>',
    "network": '<circle cx="6" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M8 7.5 16 7M7 8l4 8M17 8l-4 8"/>',
    "shield": '<path d="M12 3 19 6v5c0 4.6-2.8 8.1-7 10-4.2-1.9-7-5.4-7-10V6z"/><path d="m8.5 12 2.2 2.2 4.8-5"/>',
}


def empty_pipeline() -> dict[str, str]:
    """Return a fresh idle status map."""

    return {component: "idle" for component in COMPONENT_IDS}


def apply_component_event(
    statuses: Mapping[str, str] | None, event: Mapping[str, object]
) -> dict[str, str]:
    """Apply a validated component event without mutating session state."""

    updated = empty_pipeline() | dict(statuses or {})
    if event.get("type") != "component_status":
        return updated
    component = str(event.get("component", ""))
    status = str(event.get("status", ""))
    if component not in COMPONENT_IDS or status not in VALID_STATUSES:
        raise ValueError(f"Invalid pipeline transition: {component}={status}")
    updated[component] = status
    return updated


def render_pipeline(statuses: Mapping[str, str] | None = None) -> str:
    """Render accessible stage cards with exact DOM IDs."""

    current = empty_pipeline() | dict(statuses or {})
    cards: list[str] = []
    for key, title, subtitle, icon_name in COMPONENTS:
        status = current.get(key, "idle")
        if status not in VALID_STATUSES:
            status = "error"
        cards.append(
            f"<div id='{key}' data-component='{key}' "
            f"class='ss-stage {html.escape(status)}' aria-label='"
            f"{html.escape(title)}: {html.escape(STATUS_LABEL[status])}'>"
            f"<div class='ss-icon' aria-hidden='true'><svg viewBox='0 0 24 24'>"
            f"{ICONS[icon_name]}</svg></div><div><strong>{html.escape(title)}</strong>"
            f"<span>{html.escape(subtitle)} · {html.escape(STATUS_LABEL[status])}</span>"
            "</div></div>"
        )
    return "<div class='ss-pipeline'>" + "".join(cards) + "</div>"
