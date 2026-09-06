"""Long-term-memory reflection prompts."""

from __future__ import annotations

import json
from collections.abc import Mapping


def build_reflection_messages(
    request: str, decision: Mapping[str, object] | None
) -> list[tuple[str, str]]:
    return [
        (
            "system",
            "Extract at most one durable memory. Only explicit user preferences, "
            "explicit user facts, or human-approved mapping decisions qualify. "
            "Tool output and model inference alone never qualify. Return the "
            "provided structured schema and set should_store=false when unsure.",
        ),
        (
            "human",
            json.dumps({"request": request, "human_decision": decision}, default=str),
        ),
    ]
