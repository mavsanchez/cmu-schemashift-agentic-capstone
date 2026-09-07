"""Migration-specialist prompt assembly."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from schemashift.prompts.common import EVIDENCE_RULES, LOCAL_DATA_BOUNDARY


def build_migration_messages(state: Mapping[str, object]) -> list[tuple[str, str]]:
    system = f"""
You are SchemaShift's isolated Migration Subagent. Produce a bounded structured
migration proposal for the original read-only SQL. Map every affected reference,
preserve output names/types/semantics, and use only tables and columns present in
the supplied new schema. Offer two or three alternatives only when mappings are
genuinely ambiguous. Confidence is 0..1. Set evidence_conflict when supplied
sources disagree. Return no prose outside the requested schema.

{LOCAL_DATA_BOUNDARY}
{EVIDENCE_RULES}
    """.strip()
    payload = {
        "request": state.get("request", ""),
        "original_sql": state.get("original_sql", ""),
        "old_schema": state.get("old_schema", {}),
        "new_schema": state.get("new_schema", {}),
        "evidence": state.get("evidence", []),
        "comparison_policy": state.get("comparison_policy", {}),
        "validation_feedback": state.get("validation_feedback", []),
        "reviewer_feedback": state.get("reviewer_feedback", ""),
    }
    return [("system", system), ("human", json.dumps(payload, default=str))]


def build_branch_messages(
    state: Mapping[str, object], branches: Sequence[Mapping[str, object]], depth: int
) -> list[tuple[str, str]]:
    system = f"""
You are expanding ambiguous SchemaShift migration candidates at Tree-of-Thought
depth {depth}. Return at most three materially distinct read-only SQL candidates.
Use validation feedback to repair semantics, especially join cardinality, nulls,
types, aliases, and aggregates. Each branch must describe mappings and any
remaining ambiguity or evidence conflict for that exact SQL. Do not change
correct output contracts.
{LOCAL_DATA_BOUNDARY}
    """.strip()
    payload = {
        "request": state.get("request", ""),
        "original_sql": state.get("original_sql", ""),
        "new_schema": state.get("new_schema", {}),
        "evidence": state.get("evidence", []),
        "current_branches": list(branches),
        "validation_feedback": state.get("validation_feedback", []),
    }
    return [("system", system), ("human", json.dumps(payload, default=str))]
