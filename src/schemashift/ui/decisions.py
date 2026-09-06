"""Human-review panel rendering."""

from __future__ import annotations

import html
import json
from collections.abc import Mapping


def render_decision(review: Mapping[str, object] | None) -> str:
    """Render a dynamic review card or an inert empty state."""

    if not review:
        return (
            "<div class='ss-decision ss-decision-empty'><span class='ss-risk'>"
            "No review pending</span><h3>SchemaShift will ask when evidence is "
            "insufficient.</h3><p>High-confidence migrations that pass deterministic "
            "validation complete automatically.</p></div>"
        )
    risk = html.escape(str(review.get("risk", "medium")).title())
    title = html.escape(str(review.get("title", "Review migration candidate")))
    reason = html.escape(str(review.get("reason", "Human input is required.")))
    payload = review.get("payload")
    candidate_sql = ""
    if isinstance(payload, Mapping):
        candidate_sql = str(payload.get("candidate_sql", ""))
    sql_html = ""
    if candidate_sql:
        sql_html = f"<pre><code>{html.escape(candidate_sql)}</code></pre>"
    candidate_id = html.escape(str(review.get("candidate_id", "")))
    detail = f"<p class='ss-review-id'>Candidate {candidate_id}</p>" if candidate_id else ""
    if isinstance(payload, Mapping):
        alternatives = payload.get("alternatives") or []
        validation = payload.get("validation") or {}
        if alternatives:
            detail += (
                "<h4>Alternatives considered</h4><pre><code>"
                + html.escape(json.dumps(alternatives, indent=2, default=str)[:12_000])
                + "</code></pre>"
            )
        if validation:
            detail += (
                "<h4>Independent validation evidence</h4><pre><code>"
                + html.escape(json.dumps(validation, indent=2, default=str)[:20_000])
                + "</code></pre>"
            )
        elif not candidate_sql:
            detail += (
                "<pre><code>"
                + html.escape(json.dumps(dict(payload), indent=2, default=str)[:20_000])
                + "</code></pre>"
            )
    return (
        f"<div class='ss-decision'><span class='ss-risk'>{risk} risk</span>"
        f"<h3>{title}</h3><p>{reason}</p>{detail}{sql_html}</div>"
    )
