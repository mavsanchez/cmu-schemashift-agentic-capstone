"""The SchemaShift parent StateGraph."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from schemashift.agents import ImpactAnalysis, OrchestratorRuntime
from schemashift.graph.events import activity, component, emit
from schemashift.graph.routing import after_human_review, after_validation
from schemashift.graph.state import FRESH_RUN_DEFAULTS, SchemaShiftState
from schemashift.prompts import build_answer_messages, build_impact_messages
from schemashift.subagents.migration import build_migration_graph
from schemashift.subagents.validation import build_validation_graph


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value)


def _review_id(state: SchemaShiftState) -> str:
    run_id = str(state.get("run_id", "unknown"))
    candidate_id = str((state.get("proposal") or {}).get("candidate_id", "candidate"))
    revision = int(state.get("revision_count", 0))
    try:
        namespace = UUID(run_id)
    except ValueError:
        namespace = NAMESPACE_URL
    return str(uuid5(namespace, f"{run_id}:{candidate_id}:{revision}"))


def _issue_feedback(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues = report.get("issues") or []
    return [dict(item) if isinstance(item, Mapping) else {"message": str(item)} for item in issues]


def _user_fact_slot(text: str) -> tuple[str, str]:
    """Derive a stable, explicit-user-fact slot for contradiction supersession."""

    normalized = " ".join(text.strip().split())
    match = re.match(
        r"(?is)^(?P<subject>.+?)\s+(?:is|are|was|were|should(?:\s+be)?|must(?:\s+be)?|"
        r"uses?|equals?|prefers?|wants?|=|->)\s+.+$",
        normalized,
    )
    if match:
        subject = match.group("subject")
    else:
        words = normalized.split()
        subject = " ".join(words[:-1] if len(words) > 1 else words)
    subject = re.sub(r"[^a-z0-9_.:-]+", " ", subject.casefold()).strip()
    return f"user_fact:{subject or 'general'}", subject or "general"


def build_main_graph(runtime: OrchestratorRuntime, *, checkpointer: Any | None = None):
    """Compile the parent and its two isolated, checkpointer-free specialists."""

    settings = runtime.settings

    def record_specialist(
        specialist: str,
        *,
        role: str,
        content: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        recorder = getattr(runtime.provider, "record_subagent_interaction", None)
        if callable(recorder):
            recorder(
                specialist,
                role=role,
                content=dict(content),
                metadata=dict(metadata or {}),
            )

    def call_tool_observation(
        state: Mapping[str, Any], name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Turn MCP/transport failures into explicit graph observations."""

        try:
            result = runtime.call_tool(name, arguments)
        except Exception as exc:
            result = {
                "ok": False,
                "error": {
                    "code": "tool_failure",
                    "message": f"{type(exc).__name__}: {exc}",
                },
            }
        if not result.get("ok", True):
            raw_error = result.get("error")
            detail = (
                str(raw_error.get("message", raw_error))
                if isinstance(raw_error, Mapping)
                else str(raw_error or f"{name} returned an unsuccessful observation")
            )
            if all(state.get(key) for key in ("conversation_id", "session_id", "run_id")):
                activity(
                    state,
                    f"Tool observation: {name}",
                    detail,
                    level="warning",
                    metadata={"tool": name, "ok": False},
                )
        return result

    def evaluate_candidate(sql: str, state: Mapping[str, Any]) -> Mapping[str, Any]:
        parsed = call_tool_observation(state, "parse_sql", {"sql": sql})
        comparison = call_tool_observation(
            state,
            "compare_results",
            {
                "old_database_id": state.get("old_database_id", ""),
                "old_sql": state.get("original_sql", ""),
                "new_database_id": state.get("new_database_id", ""),
                "new_sql": sql,
                "policy": state.get("comparison_policy") or {},
            },
        )
        complexity = (parsed.get("complexity") or {}).get("node_count", len(sql))
        new_execution = comparison.get("new_execution") or {}
        return {
            "guardrail_ok": bool(parsed.get("read_only")),
            "schema_valid": bool(new_execution.get("ok", False)),
            "execution_ok": bool(comparison.get("ok")),
            "equivalent": bool(comparison.get("equivalent")),
            "complexity": complexity,
        }

    migration_graph = build_migration_graph(
        runtime.provider,
        evaluator=evaluate_candidate,
        confidence_threshold=settings.low_confidence_threshold,
        ambiguity_margin=settings.tot_ambiguity_margin,
        branching_factor=settings.tot_branching_factor,
        beam_width=settings.tot_beam_width,
        max_depth=settings.tot_max_depth,
    )
    validation_graph = build_validation_graph(
        runtime.tool_context,
        tool_caller=runtime.call_tool,
    )

    def load_context(state: SchemaShiftState) -> dict[str, Any]:
        component(state, "agent", "active", "Loading the migration request and source context")
        required = [
            "conversation_id",
            "session_id",
            "run_id",
            "request",
            "original_sql",
            "old_schema_source_id",
            "new_schema_source_id",
            "old_database_id",
            "new_database_id",
        ]
        missing = [name for name in required if not state.get(name)]
        if missing:
            component(state, "agent", "error", "Required migration context is incomplete")
            raise ValueError("Missing required run fields: " + ", ".join(missing))
        activity(state, "Run started", "Loaded explicit SQL, schemas, and controlled datasets")
        return {
            **FRESH_RUN_DEFAULTS,
            "max_revisions": settings.max_migration_revisions,
            "confidence_threshold": settings.low_confidence_threshold,
            "redis_degraded": runtime.redis_degraded,
            "status": "running",
        }

    def recall_memory(state: SchemaShiftState) -> dict[str, Any]:
        component(state, "memory", "active", "Recalling prior approved migration decisions")
        if runtime.memory_store is None:
            detail = runtime.redis_detail or "Semantic memory is unavailable"
            component(state, "memory", "error", detail)
            activity(state, "Memory unavailable", detail, level="warning")
            return {"memories": [], "redis_degraded": True}
        try:
            results = runtime.memory_store.recall(
                f"{state['request']}\n{state['original_sql']}",
                top_k=settings.retrieval_top_k,
                conversation_id=str(state["conversation_id"]),
            )
            memories = [_dump(item) for item in results]
        except Exception as exc:
            component(state, "memory", "error", "Redis memory recall failed")
            activity(state, "Memory recall failed", str(exc), level="warning")
            return {"memories": [], "redis_degraded": True}
        component(state, "memory", "done", f"Recalled {len(memories)} relevant memories")
        return {"memories": memories}

    def inspect_impact(state: SchemaShiftState) -> dict[str, Any]:
        component(state, "mcp", "active", "Parsing SQL and inspecting both schemas")
        parsed = call_tool_observation(state, "parse_sql", {"sql": state["original_sql"]})
        old_schema = call_tool_observation(
            state, "inspect_schema", {"source_id": state["old_schema_source_id"]}
        )
        new_schema = call_tool_observation(
            state, "inspect_schema", {"source_id": state["new_schema_source_id"]}
        )
        if not parsed.get("ok") or not old_schema.get("ok", True) or not new_schema.get("ok", True):
            component(state, "mcp", "error", "Schema or SQL inspection failed")
            detail = "Could not parse the original SQL or inspect the selected schemas"
            activity(state, "Preflight stopped", detail, level="error")
            return {
                "parsed_sql": parsed,
                "old_schema": old_schema,
                "new_schema": new_schema,
                "impact": {},
                "preflight_failed": True,
                "error": detail,
                "tool_observations": [
                    {"tool": "parse_sql", "result": parsed},
                    {"tool": "inspect_schema:old", "result": old_schema},
                    {"tool": "inspect_schema:new", "result": new_schema},
                ],
                "tool_call_count": int(state.get("tool_call_count", 0)) + 3,
            }
        component(state, "mcp", "done", "Parsed SQL and normalized the old/new schemas")
        component(state, "model", "active", "Assessing migration impact")
        impact = runtime.provider.structured(
            build_impact_messages(state["request"], parsed, old_schema, new_schema),
            ImpactAnalysis,
        )
        component(state, "model", "done", "Identified impacted tables and columns")
        value = _dump(impact)
        activity(
            state,
            "Impact analysis",
            value.get("summary") or f"Tables: {', '.join(value.get('impacted_tables', []))}",
        )
        return {
            "parsed_sql": parsed,
            "old_schema": old_schema,
            "new_schema": new_schema,
            "impact": value,
            "tool_call_count": int(state.get("tool_call_count", 0)) + 3,
            "model_call_count": int(state.get("model_call_count", 0)) + 1,
        }

    def retrieve_knowledge(state: SchemaShiftState) -> dict[str, Any]:
        component(state, "vector", "active", "Searching local migration documentation")
        if runtime.knowledge_store is None:
            detail = runtime.redis_detail or "Migration-document retrieval is unavailable"
            component(state, "vector", "error", detail)
            activity(state, "Retrieval unavailable", detail, level="warning")
            return {"evidence": [], "redis_degraded": True}
        try:
            query = f"{state['request']}\n{state['original_sql']}"
            document_ids = [
                str(value) for value in state.get("knowledge_document_ids", []) if value
            ]
            if document_ids:
                # Benchmark/evaluation runs can declare the exact evidence corpus so the
                # prompt-only and workflow arms see identical documents. Production runs
                # leave this empty and search the complete confirmed local corpus.
                scoped: dict[str, Any] = {}
                for document_id in document_ids:
                    for result in runtime.knowledge_store.search(
                        query,
                        top_k=settings.retrieval_top_k,
                        filters={"document_id": document_id},
                    ):
                        dumped = _dump(result)
                        chunk = dumped.get("chunk") or dumped
                        key = str(chunk.get("chunk_id", document_id))
                        scoped[key] = result
                results = sorted(
                    scoped.values(),
                    key=lambda item: float(_dump(item).get("score", 0.0)),
                    reverse=True,
                )[: settings.retrieval_top_k]
            else:
                results = runtime.knowledge_store.search(query, top_k=settings.retrieval_top_k)
            evidence = [_dump(item) for item in results]
        except Exception as exc:
            component(state, "vector", "error", "Redis vector retrieval failed")
            activity(state, "Retrieval failed", str(exc), level="warning")
            return {"evidence": [], "redis_degraded": True}
        component(state, "vector", "done", f"Retrieved {len(evidence)} evidence chunks")
        return {"evidence": evidence}

    def delegate_migration(state: SchemaShiftState) -> dict[str, Any]:
        component(state, "subagent", "active", "Migration specialist is generating a candidate")
        component(state, "model", "active", "Generating constrained migration SQL")
        memory_evidence = [
            {
                "kind": "approved_memory",
                "evidence_id": f"memory:{index}",
                "record": memory,
            }
            for index, memory in enumerate(state.get("memories") or [])
        ]
        briefing = {
            "conversation_id": state["conversation_id"],
            "session_id": state["session_id"],
            "run_id": state["run_id"],
            "request": state["request"],
            "original_sql": state["original_sql"],
            "old_schema": state.get("old_schema") or {},
            "new_schema": state.get("new_schema") or {},
            "evidence": [*(state.get("evidence") or []), *memory_evidence],
            "validation_feedback": state.get("validation_feedback") or [],
            "reviewer_feedback": state.get("reviewer_feedback", ""),
            "old_database_id": state["old_database_id"],
            "new_database_id": state["new_database_id"],
            "comparison_policy": state.get("comparison_policy") or {},
        }
        record_specialist(
            "Migration Subagent",
            role="subagent_call",
            content=briefing,
            metadata={"bounded_briefing": True},
        )
        try:
            result = migration_graph.invoke(briefing)
        except Exception as exc:
            record_specialist(
                "Migration Subagent",
                role="subagent_result",
                content={"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            )
            raise
        proposal = dict(result.get("proposal") or {})
        record_specialist(
            "Migration Subagent",
            role="subagent_result",
            content={
                "ok": True,
                "proposal": proposal,
                "tot_used": bool(result.get("tot_used")),
                "tot_depth": int(result.get("tot_depth", 0) or 0),
            },
        )
        component(state, "model", "done", "Migration candidate generated")
        component(state, "subagent", "done", "Migration specialist returned a bounded proposal")
        activity(
            state,
            "Migration candidate",
            "Candidate "
            f"{proposal.get('candidate_id', '')}; confidence "
            f"{float(proposal.get('overall_confidence', 0.0)):.2f}",
            metadata={
                "tot_used": bool(result.get("tot_used")),
                "tot_depth": result.get("tot_depth", 0),
                "tot_evaluation_count": result.get("tot_evaluation_count", 0),
            },
        )
        tot_evaluation_count = int(result.get("tot_evaluation_count", 0) or 0)
        return {
            "proposal": proposal,
            "migration_invocation_count": int(state.get("migration_invocation_count", 0)) + 1,
            "model_call_count": int(state.get("model_call_count", 0))
            + 1
            + int(result.get("tot_depth", 0)),
            "tool_call_count": int(state.get("tool_call_count", 0)) + 2 * tot_evaluation_count,
            "tot_evaluation_count": int(state.get("tot_evaluation_count", 0))
            + tot_evaluation_count,
        }

    def guardrail(state: SchemaShiftState) -> dict[str, Any]:
        component(state, "guardrail", "active", "Checking the candidate's read-only SQL policy")
        candidate_sql = str((state.get("proposal") or {}).get("candidate_sql", ""))
        parsed = call_tool_observation(state, "parse_sql", {"sql": candidate_sql})
        allowed = bool(parsed.get("ok") and parsed.get("read_only"))
        diagnostics = [
            dict(item) if isinstance(item, Mapping) else {"message": str(item)}
            for item in parsed.get("diagnostics", [])
        ]
        if not allowed and not diagnostics:
            raw_error = parsed.get("error") or "Candidate failed the read-only SQL policy"
            diagnostics.append(
                dict(raw_error)
                if isinstance(raw_error, Mapping)
                else {"code": "guardrail_failure", "message": str(raw_error)}
            )
        component(
            state,
            "guardrail",
            "done" if allowed else "error",
            "Candidate is read-only" if allowed else "Candidate failed the read-only policy",
        )
        if not allowed:
            activity(
                state,
                "Guardrail rejected candidate",
                "; ".join(str(item.get("message", item)) for item in diagnostics),
                level="error",
            )
        return {
            "guardrail_passed": allowed,
            "guardrail_diagnostics": diagnostics,
            "tool_call_count": int(state.get("tool_call_count", 0)) + 1,
        }

    def delegate_validation(state: SchemaShiftState) -> dict[str, Any]:
        component(
            state, "subagent", "active", "Validation specialist is independently checking SQL"
        )
        component(state, "mcp", "active", "Parsing, executing, and comparing controlled results")
        briefing = {
            "original_sql": state["original_sql"],
            "candidate_sql": (state.get("proposal") or {}).get("candidate_sql", ""),
            "old_database_id": state["old_database_id"],
            "new_database_id": state["new_database_id"],
            "new_schema_source_id": state["new_schema_source_id"],
            "comparison_policy": state.get("comparison_policy") or {},
        }
        record_specialist(
            "Validation Subagent",
            role="subagent_call",
            content=briefing,
            metadata={"bounded_briefing": True},
        )
        try:
            result = validation_graph.invoke(briefing)
        except Exception as exc:
            record_specialist(
                "Validation Subagent",
                role="subagent_result",
                content={"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            )
            raise
        report = dict(result.get("report") or {})
        record_specialist(
            "Validation Subagent",
            role="subagent_result",
            content={"ok": True, "report": report},
        )
        verdict = str(report.get("verdict", "revise"))
        tool_failed = any(
            isinstance(issue, Mapping) and issue.get("code") == "tool_failure"
            for issue in report.get("issues", [])
        )
        component(
            state,
            "mcp",
            "error" if tool_failed else "done",
            "Deterministic validation completed",
        )
        component(state, "subagent", "done", f"Validation verdict: {verdict}")
        comparison = report.get("execution_comparison") or {}
        activity(
            state,
            "Result comparison",
            f"Original={comparison.get('old_row_count')} rows; "
            f"migrated={comparison.get('new_row_count')} rows; verdict={verdict}",
            level="info" if verdict == "pass" else "warning",
        )
        return {
            "validation": report,
            "validation_invocation_count": int(state.get("validation_invocation_count", 0)) + 1,
            "tool_call_count": int(state.get("tool_call_count", 0)) + 3,
        }

    def revise(state: SchemaShiftState) -> dict[str, Any]:
        revision = int(state.get("revision_count", 0)) + 1
        feedback = [
            *(state.get("validation_feedback") or []),
            *_issue_feedback(state.get("validation") or {}),
        ]
        if not state.get("guardrail_passed", True):
            feedback.extend(
                {
                    "code": str(item.get("code", "guardrail_failure")),
                    "message": str(item.get("message", item)),
                }
                for item in state.get("guardrail_diagnostics", [])
            )
        human = state.get("human_decision") or {}
        reviewer_feedback = str(human.get("reviewer_feedback") or human.get("feedback") or "")
        activity(
            state,
            "Revision requested",
            f"Starting revision {revision} of {state.get('max_revisions', 10)}",
        )
        return {
            "revision_count": revision,
            "validation_feedback": feedback,
            "reviewer_feedback": reviewer_feedback,
            "human_decision": {},
            "needs_human_review": False,
        }

    def human_review(state: SchemaShiftState) -> Command[str]:
        report = state.get("validation") or {}
        structural = report.get("structural_checks") or {}
        execution = report.get("execution_comparison") or {}
        approvable = bool(
            state.get("guardrail_passed", True)
            and structural.get("parse_success")
            and structural.get("readonly_safe")
            and structural.get("schema_valid")
            and structural.get("tables_exist")
            and structural.get("columns_exist")
            and execution.get("execution_success")
        )
        review_id = _review_id(state)
        proposal = state.get("proposal") or {}
        reason = "Validation or migration evidence requires a human decision."
        if not state.get("guardrail_passed", True):
            reason = "The candidate failed the non-overridable read-only SQL guardrail."
        elif proposal.get("evidence_conflict"):
            reason = "Retrieved migration evidence conflicts."
        elif (
            float(proposal.get("overall_confidence", 0.0) or 0.0)
            < settings.low_confidence_threshold
        ):
            reason = "The selected mapping remains below the confidence threshold."
        elif int(state.get("revision_count", 0)) >= int(state.get("max_revisions", 10)):
            reason = "The bounded migration revision budget was exhausted."
        payload = {
            "type": "human_review_required",
            "review_id": review_id,
            "candidate_id": str(proposal.get("candidate_id", "")),
            "title": "Review proposed SQL migration",
            "reason": reason,
            "risk": "medium",
            "payload": {
                "candidate_sql": proposal.get("candidate_sql", ""),
                "alternatives": proposal.get("alternatives", []),
                "validation": report,
                "approvable": approvable,
            },
        }
        component(state, "agent", "done", "Parent agent is waiting for human input")
        component(state, "guardrail", "active", "Waiting for a Human Decision")
        emit(state, payload)
        decision = interrupt(payload)
        decision = dict(decision) if isinstance(decision, Mapping) else {"decision": str(decision)}
        decision.setdefault("review_id", review_id)
        if decision.get("decision") == "approve" and not approvable:
            decision = {
                **decision,
                "decision": "reject",
                "reviewer_feedback": (
                    "Candidate was not structurally executable and cannot be approved."
                ),
            }
        component(
            state, "guardrail", "done", f"Human decision: {decision.get('decision', 'unknown')}"
        )
        component(state, "agent", "active", "Resuming the parent agent after review")
        return Command(
            update={
                "review": payload,
                "human_decision": decision,
                "human_approved": decision.get("decision") == "approve",
                "needs_human_review": False,
            },
            goto=after_human_review({**state, "human_decision": decision}),
        )

    def answer(state: SchemaShiftState) -> dict[str, Any]:
        proposal = state.get("proposal") or {}
        report = state.get("validation") or {}
        artifact_path = ""
        can_write = str(report.get("verdict")) == "pass" or bool(state.get("human_approved"))
        if can_write and runtime.artifact_writer is not None:
            artifact_path = runtime.artifact_writer(
                {
                    "conversation_id": state["conversation_id"],
                    "run_id": state["run_id"],
                    "candidate_sql": proposal.get("candidate_sql", ""),
                    "source_stem": Path(state.get("old_schema_source_id", "migration")).stem,
                    "status": "human_approved" if state.get("human_approved") else "validated",
                }
            )
            emit(
                state,
                {
                    "type": "artifact_created",
                    "path": artifact_path,
                    "status": "human_approved" if state.get("human_approved") else "validated",
                    "candidate_id": proposal.get("candidate_id"),
                },
            )
        component(state, "model", "active", "Writing the evidence-backed final response")
        try:
            answer_text = runtime.provider.chat(
                build_answer_messages(
                    state["request"],
                    str(proposal.get("candidate_sql", "")),
                    str(proposal.get("rationale", "")),
                    report,
                    state.get("evidence") or [],
                    artifact_path=artifact_path or None,
                    human_approved=bool(state.get("human_approved")),
                )
            )
            component(state, "model", "done", "Final response generated")
        except Exception as exc:
            component(
                state, "model", "error", "Final model response failed; using deterministic summary"
            )
            activity(state, "Model response failed", str(exc), level="warning")
            label = (
                "human-approved with known differences"
                if state.get("human_approved")
                else "validated"
            )
            answer_text = (
                f"Migration status: {label}.\n\n```sql\n"
                f"{proposal.get('candidate_sql', '')}\n```\n\n"
                f"Validation verdict: {report.get('verdict', 'unknown')}."
            )
        for token in answer_text.splitlines(keepends=True):
            emit(state, {"type": "assistant_token", "text": token})
        return {
            "answer": answer_text,
            "artifact_path": artifact_path,
            "status": "human_approved" if state.get("human_approved") else "completed",
            "model_call_count": int(state.get("model_call_count", 0)) + 1,
        }

    def unresolved(state: SchemaShiftState) -> dict[str, Any]:
        report = state.get("validation") or {}
        if state.get("preflight_failed"):
            answer_text = (
                "SchemaShift stopped before model migration because a deterministic "
                "source or SQL preflight check failed. No migrated file was written.\n\n"
                f"Observation: {state.get('error', 'preflight failed')}"
            )
        else:
            answer_text = (
                "SchemaShift could not produce an approvable migration within the bounded "
                "revision budget. No migrated file was written. Review the validation "
                "issues and provide explicit mapping guidance.\n\n"
                f"Validation: `{report.get('verdict', 'unknown')}`"
            )
        emit(state, {"type": "assistant_token", "text": answer_text})
        return {"answer": answer_text, "status": "unresolved", "artifact_path": ""}

    def reflect(state: SchemaShiftState) -> dict[str, Any]:
        if runtime.memory_store is None:
            return {}
        human_approved = bool(state.get("human_approved"))
        explicit = str(state.get("request", "")).strip()
        writes: list[dict[str, Any]] = []
        if human_approved:
            proposal = state.get("proposal") or {}
            for item in proposal.get("mappings") or []:
                if not isinstance(item, Mapping):
                    continue
                old_reference = str(item.get("old_reference", "")).strip()
                new_expression = str(item.get("new_expression", "")).strip()
                if not old_reference or not new_expression:
                    continue
                writes.append(
                    {
                        "text": (
                            f"Approved migration mapping: {old_reference} -> {new_expression}"
                        ),
                        "memory_type": "migration_decision",
                        "source_type": "human_approval",
                        "metadata": {
                            "supersession_key": (
                                "migration_mapping:" + " ".join(old_reference.split()).casefold()
                            ),
                            "old_reference": old_reference,
                            "new_expression": new_expression,
                            "candidate_id": str(proposal.get("candidate_id", "")),
                        },
                    }
                )
        elif explicit.lower().startswith("remember:"):
            text = explicit.split(":", 1)[1].strip()
            if text:
                supersession_key, fact_subject = _user_fact_slot(text)
                writes.append(
                    {
                        "text": text,
                        "memory_type": "user_preference",
                        "source_type": "user_statement",
                        "metadata": {
                            "supersession_key": supersession_key,
                            "fact_subject": fact_subject,
                        },
                    }
                )
        if not writes:
            return {}
        component(state, "memory", "active", "Writing an explicitly durable memory")
        results = []
        for write in writes:
            result = runtime.memory_store.try_remember(
                write["text"],
                memory_type=write["memory_type"],
                provenance={
                    "source_type": write["source_type"],
                    "source_id": str(state["run_id"]),
                    "conversation_id": str(state["conversation_id"]),
                    "session_id": str(state["session_id"]),
                    "run_id": str(state["run_id"]),
                    "actor": "human" if human_approved else "user",
                    "human_approved": human_approved,
                },
                confidence=1.0,
                conversation_id=str(state["conversation_id"]),
                metadata=write["metadata"],
            )
            results.append(result)
        stored = all(result.stored for result in results)
        detail = (
            f"Stored {len(results)} explicit durable memory record(s)"
            if stored
            else "; ".join(result.reason for result in results if not result.stored)
        )
        component(state, "memory", "done" if stored else "error", detail)
        return {
            "memory_candidate": {"writes": [result.model_dump(mode="json") for result in results]}
        }

    def persist(state: SchemaShiftState) -> dict[str, Any]:
        component(state, "agent", "done", "Final response is ready for durable persistence")
        activity(state, "Run finished", f"Status: {state.get('status', 'completed')}")
        emit(
            state,
            {
                "type": "run_output",
                "status": state.get("status", "completed"),
                "answer": state.get("answer", ""),
                "artifact_path": state.get("artifact_path", ""),
                "metrics": {
                    "revision_count": state.get("revision_count", 0),
                    "tool_call_count": state.get("tool_call_count", 0),
                    "model_call_count": state.get("model_call_count", 0),
                    "migration_invocation_count": state.get("migration_invocation_count", 0),
                    "validation_invocation_count": state.get("validation_invocation_count", 0),
                    "memory_used": bool(state.get("memories")),
                },
            },
        )
        return {}

    graph = StateGraph(SchemaShiftState)
    for name, node in {
        "load_context": load_context,
        "recall_memory": recall_memory,
        "inspect_impact": inspect_impact,
        "retrieve_knowledge": retrieve_knowledge,
        "delegate_migration": delegate_migration,
        "guardrail": guardrail,
        "delegate_validation": delegate_validation,
        "revise": revise,
        "human_review": human_review,
        "answer": answer,
        "unresolved": unresolved,
        "reflect": reflect,
        "persist": persist,
    }.items():
        graph.add_node(name, node)
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "recall_memory")
    graph.add_edge("recall_memory", "inspect_impact")
    graph.add_conditional_edges(
        "inspect_impact",
        lambda state: "unresolved" if state.get("preflight_failed") else "retrieve_knowledge",
        {"unresolved": "unresolved", "retrieve_knowledge": "retrieve_knowledge"},
    )
    graph.add_edge("retrieve_knowledge", "delegate_migration")
    graph.add_edge("delegate_migration", "guardrail")
    graph.add_edge("guardrail", "delegate_validation")
    graph.add_conditional_edges(
        "delegate_validation",
        after_validation,
        {"answer": "answer", "revise": "revise", "human_review": "human_review"},
    )
    graph.add_edge("revise", "delegate_migration")
    # human_review returns a Command with its dynamic goto.
    graph.add_edge("answer", "reflect")
    graph.add_edge("unresolved", "persist")
    graph.add_edge("reflect", "persist")
    graph.add_edge("persist", END)
    return graph.compile(checkpointer=checkpointer)
