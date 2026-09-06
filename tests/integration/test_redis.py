from __future__ import annotations

import json
import os
import uuid
from contextlib import suppress
from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from schemashift.memory import (
    MemoryProvenance,
    MemorySourceType,
    MemoryType,
    SemanticMemoryStore,
    create_redis_client,
    setup_checkpointer,
)
from schemashift.model import MockProvider
from schemashift.retrieval import KnowledgeStore, chunk_document

pytestmark = pytest.mark.integration


@pytest.fixture
def redis_client():
    redis_url = os.getenv("SCHEMASHIFT_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("set SCHEMASHIFT_TEST_REDIS_URL to run Redis Stack integration tests")
    client = create_redis_client(redis_url)
    yield client
    client.close()


def _drop(client, index_name: str, key_prefix: str) -> None:
    with suppress(Exception):
        client.ft(index_name).dropindex(delete_documents=True)
    keys = list(client.scan_iter(match=f"{key_prefix}*"))
    if keys:
        client.delete(*keys)


def test_langgraph_redis_saver_setup() -> None:
    redis_url = os.getenv("SCHEMASHIFT_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("set SCHEMASHIFT_TEST_REDIS_URL to run Redis Stack integration tests")
    prefix = f"test:schemashift:checkpoint:{uuid.uuid4().hex}"

    setup = setup_checkpointer(
        redis_url,
        checkpoint_prefix=prefix,
        allow_degraded=False,
    )
    try:
        assert setup.backend == "redis"
        assert not setup.degraded
        assert setup.saver._checkpoint_prefix == prefix
        assert setup.saver._checkpoint_write_prefix == f"{prefix}:write"
    finally:
        if setup.client is not None:
            with suppress(Exception):
                setup.client.ft(prefix).dropindex(delete_documents=True)
            with suppress(Exception):
                setup.client.ft(f"{prefix}:write").dropindex(delete_documents=True)
        setup.close()


def test_redis_checkpoint_survives_saver_restart_and_cross_session_resume() -> None:
    redis_url = os.getenv("SCHEMASHIFT_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("set SCHEMASHIFT_TEST_REDIS_URL to run Redis Stack integration tests")
    prefix = f"test:schemashift:checkpoint-resume:{uuid.uuid4().hex}"
    conversation_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    origin_session = str(uuid.uuid4())
    resumed_session = str(uuid.uuid4())
    config = {"configurable": {"thread_id": conversation_id}}

    class ResumeState(TypedDict, total=False):
        conversation_id: str
        session_id: str
        run_id: str
        decision: str

    def gate(state: ResumeState) -> dict[str, str]:
        value = interrupt({"run_id": state["run_id"], "session_id": state["session_id"]})
        return {"decision": str(value["decision"]), "session_id": str(value["session_id"])}

    def compiled(saver):
        builder = StateGraph(ResumeState)
        builder.add_node("human_decision", gate)
        builder.add_edge(START, "human_decision")
        builder.add_edge("human_decision", END)
        return builder.compile(checkpointer=saver)

    first = setup_checkpointer(redis_url, checkpoint_prefix=prefix, allow_degraded=False)
    second = None
    try:
        interrupted = compiled(first.saver).invoke(
            {
                "conversation_id": conversation_id,
                "session_id": origin_session,
                "run_id": run_id,
            },
            config=config,
        )
        assert interrupted["run_id"] == run_id
        assert interrupted["__interrupt__"]
        first.close()

        second = setup_checkpointer(redis_url, checkpoint_prefix=prefix, allow_degraded=False)
        resumed = compiled(second.saver).invoke(
            Command(
                resume={
                    "decision": "approve",
                    "run_id": run_id,
                    "session_id": resumed_session,
                },
                update={"session_id": resumed_session},
            ),
            config=config,
        )

        assert resumed["conversation_id"] == conversation_id
        assert resumed["run_id"] == run_id
        assert resumed["session_id"] == resumed_session
        assert resumed["decision"] == "approve"
    finally:
        active = second or first
        if active.client is not None:
            with suppress(Exception):
                active.client.ft(prefix).dropindex(delete_documents=True)
            with suppress(Exception):
                active.client.ft(f"{prefix}:write").dropindex(delete_documents=True)
        active.close()


def test_knowledge_ingest_is_idempotent_and_searchable(redis_client) -> None:
    suffix = uuid.uuid4().hex
    index_name = f"test-schemashift-knowledge-{suffix}"
    key_prefix = f"test:schemashift:knowledge:{suffix}:"
    provider = MockProvider(strict=False)
    store = KnowledgeStore(
        redis_client,
        provider,
        index_name=index_name,
        key_prefix=key_prefix,
    )
    try:
        store.ensure_index()
        chunks = chunk_document(
            "# Customer status\n\nMap customer_status through status_lookup.",
            "customer.md",
            metadata={"topic": "customers"},
        )

        first = store.ingest(chunks)
        second = store.ingest(chunks)
        # The mock embedder is deterministic, not linguistically semantic; use
        # identical text here so this test isolates Redis vector round-tripping.
        matches = store.search(chunks[0].text, top_k=1, filters={"topic": "customers"})

        assert first.inserted == 1
        assert second.inserted == 0
        assert second.unchanged == 1
        assert matches[0].chunk.chunk_id == chunks[0].chunk_id

        metadata_change = [chunks[0].model_copy(update={"metadata": {"topic": "customer-status"}})]
        third = store.ingest(metadata_change)
        refreshed = store.search(
            chunks[0].text,
            top_k=1,
            filters={"topic": "customer-status"},
        )
        assert third.updated == 1
        assert refreshed[0].chunk.metadata["topic"] == "customer-status"
    finally:
        _drop(redis_client, index_name, key_prefix)


def test_semantic_memory_round_trip(redis_client) -> None:
    suffix = uuid.uuid4().hex
    index_name = f"test-schemashift-memory-{suffix}"
    key_prefix = f"test:schemashift:memory:{suffix}:"
    provider = MockProvider(strict=False)
    store = SemanticMemoryStore(
        redis_client,
        provider,
        index_name=index_name,
        key_prefix=key_prefix,
    )
    try:
        store.ensure_index()
        record = store.remember(
            "Use status_lookup.status_description for customer_status in schema v2.",
            memory_type=MemoryType.MIGRATION_DECISION,
            provenance=MemoryProvenance(
                source_type=MemorySourceType.HUMAN_APPROVAL,
                source_id="run-123",
                run_id="run-123",
            ),
            conversation_id="conversation-123",
        )
        matches = store.recall(
            "Use status_lookup.status_description for customer_status in schema v2.",
            conversation_id="conversation-123",
            top_k=1,
        )

        assert matches[0].memory.memory_id == record.memory_id
        assert matches[0].score > 0.99
    finally:
        _drop(redis_client, index_name, key_prefix)


def test_conflicting_memory_supersedes_with_provenance_and_is_not_recalled(
    redis_client,
) -> None:
    suffix = uuid.uuid4().hex
    index_name = f"test-schemashift-memory-supersession-{suffix}"
    key_prefix = f"test:schemashift:memory:supersession:{suffix}:"
    provider = MockProvider(strict=False)
    store = SemanticMemoryStore(
        redis_client,
        provider,
        index_name=index_name,
        key_prefix=key_prefix,
    )
    conversation_id = "conversation-supersession"
    supersession_key = "migration_mapping:legacy_customers.status"
    try:
        store.ensure_index()
        first = store.remember(
            "Approved migration mapping: legacy_customers.status -> customers.status_text",
            memory_type=MemoryType.MIGRATION_DECISION,
            provenance=MemoryProvenance(
                source_type=MemorySourceType.HUMAN_APPROVAL,
                source_id="run-1",
                run_id="run-1",
                human_approved=True,
            ),
            conversation_id=conversation_id,
            metadata={"supersession_key": supersession_key},
        )
        second = store.remember(
            "Approved migration mapping: legacy_customers.status -> status_lookup.label",
            memory_type=MemoryType.MIGRATION_DECISION,
            provenance=MemoryProvenance(
                source_type=MemorySourceType.HUMAN_APPROVAL,
                source_id="run-2",
                run_id="run-2",
                human_approved=True,
            ),
            conversation_id=conversation_id,
            metadata={"supersession_key": supersession_key},
        )
        third = store.remember(
            "Approved migration mapping: legacy_customers.name -> customers.display_name",
            memory_type=MemoryType.MIGRATION_DECISION,
            provenance=MemoryProvenance(
                source_type=MemorySourceType.HUMAN_APPROVAL,
                source_id="run-3",
                run_id="run-3",
                human_approved=True,
            ),
            conversation_id=conversation_id,
            metadata={"supersession_key": "migration_mapping:legacy_customers.name"},
        )

        old_fields = redis_client.hgetall(f"{key_prefix}{first.memory_id}")
        superseded_by = old_fields[b"superseded_by"].decode("utf-8")
        provenance = json.loads(old_fields[b"superseded_provenance_json"])
        matches = store.recall(second.text, conversation_id=conversation_id, top_k=2)
        recalled_ids = {match.memory.memory_id for match in matches}

        assert superseded_by == second.memory_id
        assert old_fields[b"superseded_at"]
        assert provenance["source_id"] == "run-2"
        assert second.metadata["supersedes"] == [first.memory_id]
        assert recalled_ids == {second.memory_id, third.memory_id}
    finally:
        _drop(redis_client, index_name, key_prefix)
