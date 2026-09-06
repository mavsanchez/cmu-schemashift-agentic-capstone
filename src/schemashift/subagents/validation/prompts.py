"""Validation specialist explanatory prompt."""

from __future__ import annotations

import json
from collections.abc import Mapping


def build_verdict_messages(report: Mapping[str, object]) -> list[tuple[str, str]]:
    return [
        (
            "system",
            "You are an independent validation specialist. Deterministic checks "
            "are authoritative. Convert them to a short structured verdict. Use "
            "pass only when every required check and result equivalence passed; "
            "use revise for repairable failures and human_review only for genuine "
            "semantic uncertainty. Do not invent observations.",
        ),
        ("human", json.dumps(report, default=str)),
    ]
