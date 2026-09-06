"""HTML rendering for correlated backend activity."""

from __future__ import annotations

import html
from collections.abc import Iterable, Mapping
from datetime import datetime


def _event_time(event: Mapping[str, object]) -> str:
    raw = event.get("timestamp") or event.get("created_at")
    if isinstance(raw, datetime):
        return raw.astimezone().strftime("%H:%M:%S")
    if raw:
        text = str(raw)
        return text[11:19] if len(text) >= 19 else text
    return "Now"


def render_activity(events: Iterable[Mapping[str, object]], limit: int = 200) -> str:
    """Render recent events; all user-controlled fields are escaped."""

    recent = list(events)[-limit:]
    if not recent:
        return "<div class='ss-activity ss-empty'>Waiting for a request.</div>"
    rows: list[str] = []
    for event in recent:
        level = str(event.get("level", "info"))
        if level not in {"info", "warning", "error"}:
            level = "info"
        label = event.get("label") or event.get("component") or event.get("type", "Activity")
        detail = event.get("detail") or ""
        rows.append(
            f"<div class='ss-event {level}'><span class='ss-dot'></span>"
            f"<time>{html.escape(_event_time(event))}</time><div>"
            f"<strong>{html.escape(str(label))}</strong>"
            f"<p>{html.escape(str(detail))}</p></div></div>"
        )
    return "<div class='ss-activity'>" + "".join(rows) + "</div>"
