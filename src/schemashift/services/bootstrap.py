"""Build the local SchemaShift application dependency graph."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from schemashift.agents import OrchestratorRuntime
from schemashift.agents.orchestrator import default_artifact_writer
from schemashift.config import Settings, get_settings
from schemashift.graph import build_main_graph
from schemashift.mcp import SourceRecord, ToolContext
from schemashift.memory import (
    CheckpointerSetup,
    SemanticMemoryStore,
    create_redis_client,
    setup_checkpointer_from_settings,
)
from schemashift.model import ModelProvider, create_model_provider
from schemashift.persistence import PostgresRepository
from schemashift.retrieval import KnowledgeIngestor, KnowledgeStore

from .ingestion import IngestionService
from .instrumentation import PersistingModelProvider, PersistingToolGateway
from .mcp_gateway import SynchronousMCPGateway
from .migration import MigrationService
from .source_registry import ApplicationSourceRegistry


@dataclass(slots=True)
class ApplicationRuntime:
    settings: Settings
    repository: PostgresRepository
    registry: ApplicationSourceRegistry
    provider: ModelProvider
    tool_context: ToolContext
    tool_gateway: Any
    checkpointer: CheckpointerSetup
    vector_client: Any | None
    memory_store: SemanticMemoryStore | None
    knowledge_store: KnowledgeStore | None
    ingestion: IngestionService
    migrations: MigrationService
    graph: Any
    redis_detail: str

    def close(self) -> None:
        self.checkpointer.close()
        if self.vector_client is not None:
            with suppress(Exception):
                self.vector_client.close()
        self.repository.close()


def _register_bundled_sources(registry: ApplicationSourceRegistry, data_root: Path) -> None:
    bundled = {
        "customer_v1_schema": (data_root / "schemas" / "customer_v1.sql", "old_schema"),
        "customer_v2_schema": (data_root / "schemas" / "customer_v2.sql", "new_schema"),
        "customer_active_sql": (data_root / "sql" / "customer_active.sql", "source_sql"),
    }
    for source_id, (path, role) in bundled.items():
        if not path.is_file():
            continue
        registry.register_source(
            SourceRecord(
                source_id=source_id,
                conversation_id="",
                session_id="",
                path=path,
                role=role,
                original_name=path.name,
                status="confirmed",
                metadata={"bundled": True},
            )
        )


def create_application_runtime(
    settings: Settings | None = None,
    *,
    repository: PostgresRepository | None = None,
    provider: ModelProvider | None = None,
    allow_redis_degraded: bool = True,
) -> ApplicationRuntime:
    """Initialize required PostgreSQL and optional degraded Redis facilities.

    PostgreSQL failures are intentionally fatal: accepting a run without durable
    application history is outside the project contract. Redis can fall back to
    a visibly labelled, process-local checkpointer for one explicit-source run.
    """

    configured = settings or get_settings()
    durable = repository or PostgresRepository(configured)
    durable.initialize()
    if not durable.health_check():
        raise RuntimeError("PostgreSQL is required before SchemaShift can accept runs")

    data_root = configured.data_root.resolve()
    registry = ApplicationSourceRegistry((data_root,), repository=durable)
    _register_bundled_sources(registry, data_root)
    registry.load_database_manifests()

    raw_provider = provider or create_model_provider(configured)
    checkpoint = setup_checkpointer_from_settings(
        configured,
        allow_degraded=allow_redis_degraded,
    )
    memory_store: SemanticMemoryStore | None = None
    knowledge_store: KnowledgeStore | None = None
    knowledge_ingestor: KnowledgeIngestor | None = None
    redis_detail = checkpoint.detail
    if not checkpoint.degraded:
        vector_client = None
        try:
            # Probe first so an incompatible embedding model never creates a bad index.
            probe = raw_provider.embed("SchemaShift embedding dimension probe")
            if len(probe) != configured.embedding_dimensions:
                raise ValueError(
                    f"Embedding probe returned {len(probe)} dimensions; "
                    f"expected {configured.embedding_dimensions}"
                )
            vector_client = create_redis_client(configured.redis_url, verify=True)
            memory_store = SemanticMemoryStore.from_settings(
                vector_client, raw_provider, configured
            )
            knowledge_store = KnowledgeStore.from_settings(vector_client, raw_provider, configured)
            memory_store.ensure_index()
            knowledge_store.ensure_index()
            knowledge_ingestor = KnowledgeIngestor.from_settings(knowledge_store, configured)
            redis_detail = "Redis checkpoints, semantic memory, and retrieval are active"
        except Exception as exc:
            if vector_client is not None:
                with suppress(Exception):
                    vector_client.close()
                vector_client = None
            memory_store = None
            knowledge_store = None
            redis_detail = (
                "DEGRADED VECTOR MODE: checkpoints remain durable, but semantic memory "
                f"and retrieval are unavailable ({type(exc).__name__}: {exc})"
            )

    tool_context = ToolContext.from_settings(registry, configured)
    mcp_gateway = SynchronousMCPGateway(tool_context)
    mcp_gateway.discover_tools()
    instrumented_gateway = PersistingToolGateway(mcp_gateway, durable)
    instrumented_provider = PersistingModelProvider(raw_provider, durable)
    graph_runtime = OrchestratorRuntime(
        settings=configured,
        provider=instrumented_provider,
        tool_context=tool_context,
        tool_client=instrumented_gateway,
        memory_store=memory_store,
        knowledge_store=knowledge_store,
        redis_degraded=checkpoint.degraded or memory_store is None or knowledge_store is None,
        redis_detail=redis_detail,
        artifact_writer=default_artifact_writer(configured.artifact_root),
    )
    graph = build_main_graph(graph_runtime, checkpointer=checkpoint.saver)
    migrations = MigrationService(durable, graph)
    ingestion = IngestionService(
        configured,
        durable,
        registry,
        knowledge_ingestor=knowledge_ingestor,
    )
    return ApplicationRuntime(
        settings=configured,
        repository=durable,
        registry=registry,
        provider=raw_provider,
        tool_context=tool_context,
        tool_gateway=instrumented_gateway,
        checkpointer=checkpoint,
        vector_client=vector_client if not checkpoint.degraded else None,
        memory_store=memory_store,
        knowledge_store=knowledge_store,
        ingestion=ingestion,
        migrations=migrations,
        graph=graph,
        redis_detail=redis_detail,
    )


__all__ = ["ApplicationRuntime", "create_application_runtime"]
