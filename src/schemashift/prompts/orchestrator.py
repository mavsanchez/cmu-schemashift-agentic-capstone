"""Parent-orchestrator prompts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from schemashift.prompts.common import EVIDENCE_RULES, LOCAL_DATA_BOUNDARY


def build_impact_messages(
    request: str,
    parsed_sql: Mapping[str, object],
    old_schema: Mapping[str, object],
    new_schema: Mapping[str, object],
) -> list[tuple[str, str]]:
    system = (
        "You are SchemaShift's Context & Impact Orchestrator. Identify only the "
        "tables, columns, expressions, and output fields affected by the schema "
        f"migration. {LOCAL_DATA_BOUNDARY}\n{EVIDENCE_RULES}"
    )
    payload = {
        "request": request,
        "parsed_original_sql": parsed_sql,
        "old_schema": old_schema,
        "new_schema": new_schema,
    }
    return [("system", system), ("human", json.dumps(payload, default=str))]


def build_answer_messages(
    request: str,
    candidate_sql: str,
    rationale: str,
    validation: Mapping[str, object],
    evidence: Sequence[Mapping[str, object]],
    *,
    artifact_path: str | None = None,
    human_approved: bool = False,
) -> list[tuple[str, str]]:
    system = (
        "You are SchemaShift's parent agent. Give a concise migration result. "
        "State whether it was deterministically validated, human-approved with "
        "known differences, or unresolved. Include the migrated SQL in a fenced "
        "sql block and summarize the evidence and validation. Do not claim that "
        f"a check passed unless the report says it did. {LOCAL_DATA_BOUNDARY}"
    )
    payload = {
        "request": request,
        "candidate_sql": candidate_sql,
        "rationale": rationale,
        "validation": validation,
        "evidence": list(evidence),
        "artifact_path": artifact_path,
        "human_approved": human_approved,
    }
    return [("system", system), ("human", json.dumps(payload, default=str))]
