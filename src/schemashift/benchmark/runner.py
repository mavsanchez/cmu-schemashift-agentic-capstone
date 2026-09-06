"""Run prompt-only and full-graph SchemaShift benchmark arms."""

from __future__ import annotations

import json
import platform
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from schemashift.config import Settings
from schemashift.mcp import DatabaseRecord, InMemorySourceRegistry, SourceRecord, ToolContext
from schemashift.mcp.tools import compare_results, inspect_schema, parse_sql
from schemashift.model import MockProvider, ModelProvider, OllamaProvider

from .cases import BENCHMARK_SEED, BenchmarkCase, build_cases
from .models import BenchmarkArm, BenchmarkMode, BenchmarkReport, BenchmarkResult
from .reporting import build_report, write_report

_RECOVERY_CASES = frozenset({"m03_aggregate", "m08_join"})


def _consume_service_envelopes(
    envelopes: Any,
    *,
    ignored_review_id: UUID | None = None,
) -> tuple[UUID | None, UUID | None, bool]:
    """Extract correlation IDs without treating a replayed interrupt as new.

    LangGraph may replay the interrupt event that was just resumed before it
    emits the terminal events (or a genuinely new interrupt).  The benchmark
    must not submit a second decision for that already-decided review.
    """

    run_id: UUID | None = None
    pending_review_id: UUID | None = None
    saw_new_review = False
    for envelope in envelopes:
        event = envelope.event
        if event.get("run_id"):
            run_id = UUID(str(event["run_id"]))
        if event.get("type") != "human_review_required":
            continue
        candidate_review_id = UUID(str(event["review_id"]))
        if candidate_review_id == ignored_review_id:
            continue
        saw_new_review = True
        pending_review_id = candidate_review_id
    return run_id, pending_review_id, saw_new_review


def _runtime_versions() -> dict[str, str]:
    values = {"python": platform.python_version()}
    for distribution in ("duckdb", "gradio", "langgraph", "mcp", "redis"):
        try:
            values[distribution] = version(distribution)
        except PackageNotFoundError:  # pragma: no cover - lock installs all runtime deps
            values[distribution] = "not-installed"
    return values


def _terminal_status(state: Mapping[str, Any], error: str) -> str:
    """Return a truthful terminal state when execution raised after checkpointing.

    A LangGraph checkpoint can legitimately retain ``status=running`` at the
    node that raised.  Once the benchmark has observed that exception, the
    case is terminally failed even though that stale checkpoint value remains.
    """

    if error:
        return "failed"
    return str(state.get("status") or "completed")


@dataclass(frozen=True, slots=True)
class BenchmarkRunOutput:
    report: BenchmarkReport
    output_directory: Path


class _CountingProvider:
    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider
        self.chat_calls = 0
        self.structured_calls = 0
        self.embedding_calls = 0

    @property
    def model_calls(self) -> int:
        return self.chat_calls + self.structured_calls

    def chat(self, messages: Any, **kwargs: Any) -> str:
        self.chat_calls += 1
        return self.provider.chat(messages, **kwargs)

    def structured(self, messages: Any, response_model: Any, **kwargs: Any) -> Any:
        self.structured_calls += 1
        return self.provider.structured(messages, response_model, **kwargs)

    def embed(self, text: str) -> list[float]:
        self.embedding_calls += 1
        return self.provider.embed(text)

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedding_calls += 1
        return self.provider.embed_many(texts)


class _CountingMCPClient:
    """Count graph-visible calls through runtime-discovered MCP v2 tools."""

    def __init__(self, context: ToolContext) -> None:
        from schemashift.mcp import SynchronousMCPClient

        self.client = SynchronousMCPClient(context=context, raise_exceptions=True)
        self.discovered = tuple(sorted(self.client.discover_tools()))
        self.calls = 0

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name not in self.discovered:
            raise ValueError(f"Tool was not discovered from the benchmark MCP server: {name}")
        self.calls += 1
        return dict(self.client.call_tool(name, dict(arguments)))


class _FixtureKnowledgeStore:
    def __init__(self, case: BenchmarkCase, repository_root: Path) -> None:
        self.case = case
        self.repository_root = repository_root

    def search(self, _query: str, *, top_k: int = 3) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for path, document_id in zip(
            self.case.source_paths.knowledge,
            self.case.expected_evidence_ids,
            strict=True,
        ):
            source = self.repository_root / path
            results.append(
                {
                    "chunk_id": f"{document_id}:benchmark",
                    "document_id": document_id,
                    "document": source.name,
                    "text": source.read_text(encoding="utf-8"),
                    "score": 1.0,
                    "distance": 0.0,
                }
            )
        return results[:top_k]


class _NoopMemoryStore:
    def recall(self, _query: str, **_kwargs: Any) -> list[Any]:
        return []

    def try_remember(self, *_args: Any, **_kwargs: Any) -> Any:
        class Result:
            stored = False
            reason = "Benchmark memory writes are isolated"

            @staticmethod
            def model_dump(**_options: Any) -> dict[str, Any]:
                return {"stored": False, "reason": "Benchmark memory writes are isolated"}

        return Result()


@dataclass(slots=True)
class _LiveApplication:
    runtime: Any
    provider: _CountingProvider

    def close(self) -> None:
        close = getattr(self.runtime, "close", None)
        if callable(close):
            close()


def _evidence_ids(evidence: Sequence[Any]) -> list[str]:
    identifiers: list[str] = []
    for value in evidence:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        if not isinstance(value, Mapping):
            continue
        chunk = value.get("chunk")
        if hasattr(chunk, "model_dump"):
            chunk = chunk.model_dump(mode="json")
        candidate = chunk if isinstance(chunk, Mapping) else value
        identifier = candidate.get("document_id") or candidate.get("chunk_id")
        if identifier:
            identifiers.append(str(identifier))
    return identifiers


def _retrieval_recall(expected: Sequence[str], observed: Sequence[str]) -> float:
    if not expected:
        return 1.0
    observed_set = set(observed)
    return len(set(expected) & observed_set) / len(set(expected))


def _extract_sql(response: str) -> str:
    value = response.strip()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, Mapping):
        for key in ("candidate_sql", "sql", "query"):
            if isinstance(decoded.get(key), str):
                return decoded[key].strip()
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", value, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    start = re.search(r"\b(?:WITH|SELECT)\b", value, flags=re.IGNORECASE)
    if start:
        candidate = value[start.start() :].strip()
        if ";" in candidate:
            candidate = candidate[: candidate.rfind(";") + 1]
        return candidate
    return value


def _schema_table_names(schema: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("name"))
        for item in schema.get("tables", [])
        if isinstance(item, Mapping) and item.get("name")
    }


def _evaluate(
    case: BenchmarkCase,
    candidate_sql: str,
    context: ToolContext,
    *,
    new_schema_source_id: str,
) -> dict[str, Any]:
    parsed = parse_sql(candidate_sql)
    schema = inspect_schema(new_schema_source_id, context=context)
    comparison = compare_results(
        case.old_database_id,
        case.original_sql,
        case.new_database_id,
        candidate_sql,
        case.comparison_policy,
        context=context,
    )
    referenced = {str(value) for value in parsed.get("tables", [])}
    schema_valid = bool(schema.get("ok", True)) and referenced <= _schema_table_names(schema)
    new_execution = comparison.get("new_execution")
    new_execution = new_execution if isinstance(new_execution, Mapping) else {}
    execution_success = bool(comparison.get("execution_ok")) and bool(
        new_execution.get("ok", False)
    )
    equivalent = bool(comparison.get("equivalent"))
    return {
        "parse_success": bool(parsed.get("ok") and parsed.get("read_only")),
        "schema_valid": schema_valid and execution_success,
        "execution_success": execution_success,
        "equivalent": equivalent,
        "verdict": "pass" if equivalent else "revise",
        "comparison": comparison,
    }


def _baseline_messages(case: BenchmarkCase, repository_root: Path) -> list[tuple[str, str]]:
    old_schema = (repository_root / case.source_paths.old_schema).read_text(encoding="utf-8")
    new_schema = (repository_root / case.source_paths.new_schema).read_text(encoding="utf-8")
    evidence = [
        (repository_root / path).read_text(encoding="utf-8") for path in case.source_paths.knowledge
    ]
    payload = {
        "request": case.prompt,
        "old_schema": old_schema,
        "new_schema": new_schema,
        "migration_evidence": evidence,
        "original_sql": case.original_sql,
    }
    return [
        (
            "system",
            "Migrate the supplied read-only DuckDB SELECT to the new schema. Preserve "
            "output names, types, nulls, row multiplicity, and meaning. Return only SQL. "
            "Use only the supplied synthetic/local schema and evidence.",
        ),
        ("human", json.dumps(payload)),
    ]


def _proposal(case: BenchmarkCase, *, sql: str, review: bool) -> dict[str, Any]:
    columns = case.expected_impact.get("columns", [])
    old_reference = columns[0] if columns else case.motif
    new_expression = columns[-1] if columns else "migrated expression"
    candidate_id = uuid5(NAMESPACE_URL, f"schemashift:{case.case_id}:{sql}")
    alternatives: list[dict[str, Any]] = []
    if review:
        alternatives.append(
            {
                "candidate_id": str(uuid5(NAMESPACE_URL, f"{case.case_id}:ambiguous")),
                "candidate_sql": "SELECT 1 AS unresolved_mapping",
                "rationale": "A deliberately inferior branch makes the ambiguity explicit.",
                "confidence": 0.72,
            }
        )
    return {
        "candidate_id": str(candidate_id),
        "candidate_sql": sql,
        "mappings": [
            {
                "old_reference": old_reference,
                "new_expression": new_expression,
                "change_type": case.motif,
                "rationale": case.prompt,
                "confidence": 0.75 if review else 0.99,
                "evidence_ids": case.expected_evidence_ids,
            }
        ],
        "rationale": "Scripted benchmark proposal backed by the declared case evidence.",
        "evidence_ids": case.expected_evidence_ids,
        "alternatives": alternatives,
        "overall_confidence": 0.75 if review else 0.99,
        "requires_human_review": review,
        "evidence_conflict": review,
        "ambiguity_flags": ["conflicting_evidence"] if review else [],
    }


def _mock_workflow_provider(case: BenchmarkCase) -> MockProvider:
    impact = {
        "impacted_tables": case.expected_impact.get("tables", []),
        "impacted_columns": case.expected_impact.get("columns", []),
        "expression_changes": case.expected_impact.get("expressions", []),
        "summary": case.prompt,
        "requires_human_review": case.expected_human_review,
    }
    structured: list[dict[str, Any]] = [impact]
    if case.case_id in _RECOVERY_CASES:
        structured.append(_proposal(case, sql="SELECT 1 AS wrong_result", review=False))
    structured.append(_proposal(case, sql=case.gold_sql, review=case.expected_human_review))
    if case.expected_human_review:
        structured.append(
            {
                "branches": [
                    {
                        "candidate_sql": case.gold_sql,
                        "rationale": "The executable gold branch preserves the legacy contract.",
                        "evidence_ids": case.expected_evidence_ids,
                        "confidence": 0.79,
                    }
                ]
            }
        )
    return MockProvider(
        structured_responses=structured,
        chat_responses=[f"Benchmark migration complete.\n```sql\n{case.gold_sql}\n```"],
        strict=True,
    )


def _registry_from_manifest(
    repository_root: Path,
    settings: Settings,
    manifest_path: Path,
) -> tuple[InMemorySourceRegistry, ToolContext, str, str]:
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Demo manifest does not exist: {manifest_path}. Run scripts/create_demo_data.py first."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_path = Path(manifest["old_database_path"]).resolve()
    new_path = Path(manifest["new_database_path"]).resolve()
    allowed_roots = {
        (repository_root / "data").resolve(),
        (repository_root / "benchmark").resolve(),
        old_path.parent,
        new_path.parent,
    }
    registry = InMemorySourceRegistry(sorted(allowed_roots, key=str))
    registry.register_database(
        DatabaseRecord(database_id="customer_v1", path=old_path, role="old_data")
    )
    registry.register_database(
        DatabaseRecord(database_id="customer_v2", path=new_path, role="new_data")
    )
    old_schema_id = "benchmark-customer-v1-schema"
    new_schema_id = "benchmark-customer-v2-schema"
    registry.register_source(
        SourceRecord(
            source_id=old_schema_id,
            conversation_id="benchmark",
            session_id="benchmark",
            path=repository_root / "data/schemas/customer_v1.sql",
            role="old_schema",
        )
    )
    registry.register_source(
        SourceRecord(
            source_id=new_schema_id,
            conversation_id="benchmark",
            session_id="benchmark",
            path=repository_root / "data/schemas/customer_v2.sql",
            role="new_schema",
        )
    )
    return (
        registry,
        ToolContext.from_settings(registry, settings),
        old_schema_id,
        new_schema_id,
    )


def _live_application(
    settings: Settings,
    repository_root: Path,
    manifest_path: Path,
    run_namespace: UUID,
) -> _LiveApplication:
    """Create the same durable service container used by the Gradio app."""

    from schemashift.retrieval import KnowledgeIngestor
    from schemashift.services import create_application_runtime

    isolation = run_namespace.hex
    configured = settings.model_copy(
        update={
            "model_provider": "ollama",
            "data_root": repository_root / "data",
            "upload_root": repository_root / "data/generated/uploads",
            "artifact_root": repository_root / "data/generated/migrations",
            "redis_checkpoint_prefix": (
                f"{settings.redis_checkpoint_prefix}:benchmark:{isolation}"
            ),
            "redis_memory_prefix": f"{settings.redis_memory_prefix}:benchmark:{isolation}",
            "redis_knowledge_prefix": (f"{settings.redis_knowledge_prefix}:benchmark:{isolation}"),
            "redis_memory_index": f"schemashift-benchmark-memory-{isolation}",
            "redis_knowledge_index": f"schemashift-benchmark-knowledge-{isolation}",
        }
    )
    provider = _CountingProvider(OllamaProvider.from_settings(configured))
    runtime = create_application_runtime(
        configured,
        provider=provider,
        allow_redis_degraded=False,
    )
    if (
        runtime.checkpointer.degraded
        or runtime.memory_store is None
        or runtime.knowledge_store is None
    ):
        runtime.close()
        raise RuntimeError(
            "The live workflow benchmark requires Redis checkpoints, memory, and retrieval"
        )
    KnowledgeIngestor.from_settings(runtime.knowledge_store, configured).ingest_directory(
        repository_root / "data/knowledge"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest.get("databases", []):
        if isinstance(item, Mapping):
            runtime.registry.register_database(item)
    return _LiveApplication(runtime=runtime, provider=provider)


class BenchmarkRunner:
    """Own the fair two-arm evaluation while keeping scoring deterministic."""

    def __init__(
        self,
        repository_root: str | Path,
        *,
        settings: Settings | None = None,
        manifest_path: str | Path | None = None,
        output_root: str | Path | None = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.settings = settings or Settings()
        self.manifest_path = (
            Path(manifest_path).resolve()
            if manifest_path is not None
            else self.repository_root / "data/generated/demo_manifest.json"
        )
        self.output_root = (
            Path(output_root).resolve()
            if output_root is not None
            else self.repository_root / "benchmark/results"
        )

    def _baseline_provider(self, mode: BenchmarkMode, case: BenchmarkCase) -> _CountingProvider:
        if mode is BenchmarkMode.MOCK:
            provider: ModelProvider = MockProvider(
                chat_responses=[f"```sql\n{case.gold_sql}\n```"], strict=True
            )
        else:
            # The prompt-only baseline deliberately gets a single attempt.
            provider = OllamaProvider(
                chat_model=self.settings.chat_model,
                embedding_model=self.settings.embedding_model,
                base_url=self.settings.ollama_base_url,
                temperature=self.settings.temperature,
                keep_alive=self.settings.ollama_keep_alive,
                max_attempts=1,
                embedding_dimensions=self.settings.embedding_dimensions,
            )
        return _CountingProvider(provider)

    def _run_baseline(
        self,
        case: BenchmarkCase,
        mode: BenchmarkMode,
        context: ToolContext,
        new_schema_id: str,
    ) -> BenchmarkResult:
        started = perf_counter()
        provider = self._baseline_provider(mode, case)
        candidate = ""
        error = ""
        try:
            candidate = _extract_sql(
                provider.chat(
                    _baseline_messages(case, self.repository_root),
                    temperature=self.settings.temperature,
                )
            )
            evaluation = _evaluate(case, candidate, context, new_schema_source_id=new_schema_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            evaluation = {
                "parse_success": False,
                "schema_valid": False,
                "execution_success": False,
                "equivalent": False,
                "verdict": "error",
            }
        verdict = str(evaluation["verdict"])
        terminal_status = "failed" if error else "completed"
        return BenchmarkResult(
            case_id=case.case_id,
            motif_id=case.motif_id,
            motif=case.motif,
            sql_form=case.sql_form.value,
            arm=BenchmarkArm.BASELINE,
            mode=mode,
            candidate_sql=candidate,
            parse_success=bool(evaluation["parse_success"]),
            schema_valid=bool(evaluation["schema_valid"]),
            execution_success=bool(evaluation["execution_success"]),
            equivalent=bool(evaluation["equivalent"]),
            model_calls=provider.model_calls,
            embedding_calls=provider.embedding_calls,
            latency_ms=(perf_counter() - started) * 1000,
            verdict=verdict,
            route=verdict,
            terminal_status=terminal_status,
            expected_verdict=case.expected_verdict,
            expected_human_review=case.expected_human_review,
            expected_equivalent=case.expected_equivalent,
            expected_route_matched=verdict == case.expected_verdict,
            error=error,
        )

    def _run_mock_workflow(
        self,
        case: BenchmarkCase,
        context: ToolContext,
        old_schema_id: str,
        new_schema_id: str,
        run_namespace: UUID,
    ) -> BenchmarkResult:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.types import Command

        from schemashift.agents import OrchestratorRuntime
        from schemashift.graph import build_main_graph

        started = perf_counter()
        raw_provider: ModelProvider = _mock_workflow_provider(case)
        provider = _CountingProvider(raw_provider)
        tools = _CountingMCPClient(context)
        knowledge_store = _FixtureKnowledgeStore(case, self.repository_root)
        memory_store = _NoopMemoryStore()
        checkpointer = InMemorySaver()
        runtime = OrchestratorRuntime(
            settings=self.settings,
            provider=provider,
            tool_context=context,
            tool_client=tools,
            memory_store=memory_store,
            knowledge_store=knowledge_store,
            redis_degraded=False,
            artifact_writer=None,
        )
        graph = build_main_graph(runtime, checkpointer=checkpointer)
        conversation_id = uuid5(run_namespace, f"conversation:{case.case_id}")
        session_id = uuid5(run_namespace, f"session:{case.case_id}")
        run_id = uuid5(run_namespace, f"run:{case.case_id}")
        config = {"configurable": {"thread_id": str(conversation_id)}}
        state: dict[str, Any] = {}
        intervention = False
        error = ""
        try:
            state = graph.invoke(
                {
                    "conversation_id": str(conversation_id),
                    "session_id": str(session_id),
                    "run_id": str(run_id),
                    "turn_id": str(run_id),
                    "request": case.prompt,
                    "original_sql": case.original_sql,
                    "old_schema_source_id": old_schema_id,
                    "new_schema_source_id": new_schema_id,
                    "old_database_id": case.old_database_id,
                    "new_database_id": case.new_database_id,
                    "comparison_policy": case.comparison_policy,
                },
                config=config,
            )
            interrupts = state.get("__interrupt__", ())
            review_count = 0
            while interrupts:
                intervention = True
                review_count += 1
                if review_count > self.settings.max_candidate_attempts + 1:
                    raise RuntimeError("Graph exceeded the bounded human-review loop")
                review_value = getattr(interrupts[0], "value", {})
                review_id = (
                    review_value.get("review_id", "") if isinstance(review_value, Mapping) else ""
                )
                state = graph.invoke(
                    Command(
                        resume={
                            "decision": "approve",
                            "reviewer_feedback": "Approved by the benchmark review fixture.",
                            "review_id": review_id,
                            "run_id": str(run_id),
                            "session_id": str(session_id),
                        }
                    ),
                    config=config,
                )
                interrupts = state.get("__interrupt__", ())
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        proposal = state.get("proposal") or {}
        candidate = str(proposal.get("candidate_sql", ""))
        try:
            evaluation = _evaluate(case, candidate, context, new_schema_source_id=new_schema_id)
        except Exception as exc:
            if not error:
                error = f"{type(exc).__name__}: {exc}"
            evaluation = {
                "parse_success": False,
                "schema_valid": False,
                "execution_success": False,
                "equivalent": False,
                "verdict": "error",
            }
        observed_evidence = _evidence_ids(state.get("evidence") or [])
        report = state.get("validation") or {}
        verdict = str(report.get("verdict") or evaluation["verdict"])
        route = "human_review" if intervention else verdict
        terminal_status = _terminal_status(state, error)
        revision_count = int(state.get("revision_count", 0) or 0)
        equivalent = bool(evaluation["equivalent"])
        return BenchmarkResult(
            case_id=case.case_id,
            motif_id=case.motif_id,
            motif=case.motif,
            sql_form=case.sql_form.value,
            arm=BenchmarkArm.WORKFLOW,
            mode=BenchmarkMode.MOCK,
            candidate_sql=candidate,
            parse_success=bool(evaluation["parse_success"]),
            schema_valid=bool(evaluation["schema_valid"]),
            execution_success=bool(evaluation["execution_success"]),
            equivalent=equivalent,
            retrieval_top_k=observed_evidence,
            retrieval_recall=_retrieval_recall(case.expected_evidence_ids, observed_evidence),
            recovery=revision_count > 0 and equivalent,
            intervention=intervention,
            tool_calls=tools.calls,
            model_calls=provider.model_calls,
            embedding_calls=provider.embedding_calls,
            latency_ms=(perf_counter() - started) * 1000,
            memory_used=bool(state.get("memories")),
            migration_specialist_invocations=int(state.get("migration_invocation_count", 0) or 0),
            validation_specialist_invocations=int(state.get("validation_invocation_count", 0) or 0),
            verdict=verdict,
            route=route,
            terminal_status=terminal_status,
            revision_count=revision_count,
            expected_verdict=case.expected_verdict,
            expected_human_review=case.expected_human_review,
            expected_equivalent=case.expected_equivalent,
            expected_route_matched=route == case.expected_verdict,
            error=error,
        )

    def _run_live_workflow(
        self,
        case: BenchmarkCase,
        context: ToolContext,
        new_schema_id: str,
        live: _LiveApplication,
    ) -> BenchmarkResult:
        """Run through the durable application service, not a benchmark shortcut."""

        from schemashift.services import MigrationRequest

        runtime = live.runtime
        started = perf_counter()
        model_calls_before = live.provider.model_calls
        embedding_calls_before = live.provider.embedding_calls
        state: dict[str, Any] = {}
        intervention = False
        error = ""
        conversation_id = None
        run_id = None
        try:
            conversation_id, session_id = runtime.migrations.create_conversation(
                title=f"Benchmark {case.case_id}"
            )
            migration = MigrationRequest(
                request=case.prompt,
                original_sql=case.original_sql,
                old_schema_source_id="customer_v1_schema",
                new_schema_source_id="customer_v2_schema",
                old_database_id=case.old_database_id,
                new_database_id=case.new_database_id,
                comparison_policy=case.comparison_policy,
                knowledge_document_ids=case.expected_evidence_ids,
            )

            def consume(envelopes: Any, *, ignored_review_id: UUID | None = None) -> UUID | None:
                nonlocal intervention, run_id
                observed_run_id, pending, saw_new_review = _consume_service_envelopes(
                    envelopes,
                    ignored_review_id=ignored_review_id,
                )
                if observed_run_id is not None:
                    run_id = observed_run_id
                if saw_new_review:
                    intervention = True
                return pending

            review_id = consume(
                runtime.migrations.start_run(conversation_id, session_id, migration)
            )
            review_count = 0
            while review_id is not None:
                review_count += 1
                if review_count > self.settings.max_candidate_attempts + 1:
                    raise RuntimeError("Service exceeded the bounded human-review loop")
                # Resume from a fresh UI session to exercise the durable contract.
                decision_session = runtime.migrations.open_session(conversation_id)
                review_record = runtime.repository.get_review(review_id)
                approvable = bool(
                    review_record is not None and review_record.payload.get("approvable", False)
                )
                decided_review_id = review_id
                review_id = consume(
                    runtime.migrations.resume_run(
                        conversation_id,
                        decision_session,
                        review_id=decided_review_id,
                        decision="approve" if approvable else "reject",
                        reviewer_feedback=(
                            "Approved by the live benchmark review fixture."
                            if approvable
                            else "Repair the deterministic parse, schema, or execution failure."
                        ),
                    ),
                    ignored_review_id=decided_review_id,
                )
            snapshot = runtime.graph.get_state(
                {"configurable": {"thread_id": str(conversation_id)}}
            )
            state = dict(snapshot.values or {})
            if state.get("run_id"):
                run_id = UUID(str(state["run_id"]))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if conversation_id is not None:
                try:
                    snapshot = runtime.graph.get_state(
                        {"configurable": {"thread_id": str(conversation_id)}}
                    )
                    state = dict(snapshot.values or {})
                except Exception:
                    pass

        proposal = state.get("proposal") or {}
        candidate = str(proposal.get("candidate_sql", ""))
        try:
            evaluation = _evaluate(case, candidate, context, new_schema_source_id=new_schema_id)
        except Exception as exc:
            if not error:
                error = f"{type(exc).__name__}: {exc}"
            evaluation = {
                "parse_success": False,
                "schema_valid": False,
                "execution_success": False,
                "equivalent": False,
                "verdict": "error",
            }

        messages = []
        if conversation_id is not None and run_id is not None:
            try:
                messages = runtime.repository.list_messages(
                    conversation_id, run_id=run_id, limit=10_000
                )
            except Exception as exc:
                if not error:
                    error = f"{type(exc).__name__}: {exc}"
        persisted_model_calls = sum(message.role == "model_result" for message in messages)
        tool_calls = sum(message.role == "tool_call" for message in messages)
        observed_evidence = _evidence_ids(state.get("evidence") or [])
        report = state.get("validation") or {}
        verdict = str(report.get("verdict") or evaluation["verdict"])
        route = "human_review" if intervention else verdict
        terminal_status = _terminal_status(state, error)
        revision_count = int(state.get("revision_count", 0) or 0)
        equivalent = bool(evaluation["equivalent"])
        model_calls = live.provider.model_calls - model_calls_before
        embedding_calls = live.provider.embedding_calls - embedding_calls_before
        if persisted_model_calls > model_calls:
            # Persistence is the ground truth if a custom provider records more
            # fine-grained operations than the standard wrapper.
            model_calls = persisted_model_calls
        return BenchmarkResult(
            case_id=case.case_id,
            motif_id=case.motif_id,
            motif=case.motif,
            sql_form=case.sql_form.value,
            arm=BenchmarkArm.WORKFLOW,
            mode=BenchmarkMode.LIVE,
            candidate_sql=candidate,
            parse_success=bool(evaluation["parse_success"]),
            schema_valid=bool(evaluation["schema_valid"]),
            execution_success=bool(evaluation["execution_success"]),
            equivalent=equivalent,
            retrieval_top_k=observed_evidence,
            retrieval_recall=_retrieval_recall(case.expected_evidence_ids, observed_evidence),
            recovery=revision_count > 0 and equivalent,
            intervention=intervention,
            tool_calls=tool_calls,
            model_calls=model_calls,
            embedding_calls=embedding_calls,
            latency_ms=(perf_counter() - started) * 1000,
            memory_used=bool(state.get("memories")),
            migration_specialist_invocations=int(state.get("migration_invocation_count", 0) or 0),
            validation_specialist_invocations=int(state.get("validation_invocation_count", 0) or 0),
            verdict=verdict,
            route=route,
            terminal_status=terminal_status,
            revision_count=revision_count,
            expected_verdict=case.expected_verdict,
            expected_human_review=case.expected_human_review,
            expected_equivalent=case.expected_equivalent,
            expected_route_matched=route == case.expected_verdict,
            error=error,
        )

    def run(
        self,
        *,
        cases: Sequence[BenchmarkCase] | None = None,
        mode: BenchmarkMode | str = BenchmarkMode.MOCK,
        arms: Sequence[BenchmarkArm | str] = (
            BenchmarkArm.BASELINE,
            BenchmarkArm.WORKFLOW,
        ),
        update_latest: bool | None = None,
    ) -> BenchmarkRunOutput:
        canonical_cases = list(build_cases())
        selected = list(canonical_cases if cases is None else cases)
        if not selected:
            raise ValueError("at least one benchmark case is required")
        selected_mode = BenchmarkMode(mode)
        selected_arms = list(dict.fromkeys(BenchmarkArm(arm) for arm in arms))
        if not selected_arms:
            raise ValueError("at least one benchmark arm is required")
        if (
            selected_mode is BenchmarkMode.LIVE
            and len(selected) == len(canonical_cases)
            and selected != canonical_cases
        ):
            raise ValueError(
                "A 50-case live comparison requires the exact canonical cases and order"
            )
        full_live_comparison = (
            selected_mode is BenchmarkMode.LIVE
            and selected == canonical_cases
            and set(selected_arms) == {BenchmarkArm.BASELINE, BenchmarkArm.WORKFLOW}
        )
        if update_latest is True and not full_live_comparison:
            raise ValueError(
                "latest.json/latest.md may only represent a full 50-case live comparison"
            )
        should_update_latest = full_live_comparison if update_latest is None else update_latest

        _, context, old_schema_id, new_schema_id = _registry_from_manifest(
            self.repository_root, self.settings, self.manifest_path
        )
        started_at = datetime.now(UTC)
        run_namespace = uuid4()
        results: list[BenchmarkResult] = []
        live: _LiveApplication | None = None
        if selected_mode is BenchmarkMode.LIVE and BenchmarkArm.WORKFLOW in selected_arms:
            live = _live_application(
                self.settings,
                self.repository_root,
                self.manifest_path,
                run_namespace,
            )
        try:
            for case in selected:
                if BenchmarkArm.BASELINE in selected_arms:
                    results.append(self._run_baseline(case, selected_mode, context, new_schema_id))
                if BenchmarkArm.WORKFLOW in selected_arms:
                    if selected_mode is BenchmarkMode.MOCK:
                        results.append(
                            self._run_mock_workflow(
                                case,
                                context,
                                old_schema_id,
                                new_schema_id,
                                run_namespace,
                            )
                        )
                    else:
                        if live is None:  # pragma: no cover - guarded during setup
                            raise RuntimeError("live application runtime was not initialized")
                        results.append(
                            self._run_live_workflow(
                                case,
                                context,
                                new_schema_id,
                                live,
                            )
                        )
        finally:
            if live is not None:
                live.close()

        completed_at = datetime.now(UTC)
        report = build_report(
            benchmark_seed=BENCHMARK_SEED,
            mode=selected_mode.value,
            started_at=started_at,
            completed_at=completed_at,
            model=self.settings.chat_model,
            embedding_model=self.settings.embedding_model,
            temperature=self.settings.temperature,
            settings={
                "provider": "ollama" if selected_mode is BenchmarkMode.LIVE else "mock",
                "workflow_runtime": (
                    "application-service/postgresql/redis/langgraph/mcp-v2"
                    if selected_mode is BenchmarkMode.LIVE
                    else "langgraph/mcp-v2/fixture-retrieval"
                ),
                "model_call_metric": "chat_plus_structured",
                "embedding_calls_recorded_separately": True,
                "runtime_versions": _runtime_versions(),
                "model_attempts_workflow": self.settings.model_max_attempts,
                "model_attempts_baseline": 1,
                "retrieval_top_k": self.settings.retrieval_top_k,
                "max_migration_revisions": self.settings.max_migration_revisions,
                "tot_branching_factor": self.settings.tot_branching_factor,
                "tot_beam_width": self.settings.tot_beam_width,
                "tot_max_depth": self.settings.tot_max_depth,
                "absolute_tolerance": self.settings.float_absolute_tolerance,
                "relative_tolerance": self.settings.float_relative_tolerance,
                "seed": BENCHMARK_SEED,
            },
            case_count=len(selected),
            arms=[arm.value for arm in selected_arms],
            results=results,
        )
        destination = write_report(
            report,
            self.output_root,
            update_latest=bool(should_update_latest),
        )
        return BenchmarkRunOutput(report=report, output_directory=destination)


def smoke_cases(cases: Sequence[BenchmarkCase] | None = None) -> tuple[BenchmarkCase, ...]:
    """Return one projection case per motif as the fast ten-case slice."""

    values = cases or build_cases()
    return tuple(case for case in values if case.sql_form.value == "projection")


__all__ = ["BenchmarkRunOutput", "BenchmarkRunner", "smoke_cases"]
