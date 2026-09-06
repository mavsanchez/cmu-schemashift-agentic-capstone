from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from schemashift.domain import MigrationProposal, RunContext
from schemashift.persistence import ActorType
from schemashift.services.instrumentation import (
    PersistingModelProvider,
    PersistingToolGateway,
    instrument_run,
)


class RecordingInteractions:
    def __init__(self) -> None:
        self.values: list[tuple[RunContext, dict[str, Any]]] = []

    def add_tool_interaction(self, context: RunContext, **values: Any) -> None:
        self.values.append((context, values))


class SuccessfulGateway:
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "name": name, "arguments": arguments}


class FailingGateway:
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        del name, arguments
        raise TimeoutError("fixture timeout")


class RecordingMessages:
    def __init__(self) -> None:
        self.values: list[tuple[RunContext, dict[str, Any]]] = []

    def add_message(self, context: RunContext, **values: Any) -> None:
        self.values.append((context, values))


class FailingModel:
    def chat(self, *_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("model unavailable")

    def structured(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("model unavailable")

    def embed(self, _text: str) -> list[float]:
        return []

    def embed_many(self, _texts: Any) -> list[list[float]]:
        return []


def test_tool_request_and_result_share_one_stable_call_id() -> None:
    context = RunContext.create()
    repository = RecordingInteractions()
    gateway = PersistingToolGateway(SuccessfulGateway(), repository)  # type: ignore[arg-type]

    with instrument_run(context):
        result = gateway.call_tool("parse_sql", {"sql": "SELECT 1"})

    assert result["ok"] is True
    assert len(repository.values) == 1
    stored_context, interaction = repository.values[0]
    assert stored_context == context
    assert interaction["tool_name"] == "parse_sql"
    UUID(interaction["tool_call_id"])
    assert '"sql": "SELECT 1"' in interaction["request_content"]
    assert '"ok": true' in interaction["result_content"]


def test_tool_transport_failure_becomes_one_persisted_observation_without_retry() -> None:
    context = RunContext.create()
    repository = RecordingInteractions()
    gateway = PersistingToolGateway(FailingGateway(), repository)  # type: ignore[arg-type]

    with instrument_run(context):
        result = gateway.call_tool("inspect_schema", {"source_id": "registered-id"})

    assert result["ok"] is False
    assert "TimeoutError" in result["error"]
    assert "fixture timeout" in result["errors"]
    assert len(repository.values) == 1
    assert repository.values[0][1]["result_metadata"] == {"ok": False}


def test_failed_model_call_is_persisted_before_error_propagates() -> None:
    context = RunContext.create()
    repository = RecordingMessages()
    provider = PersistingModelProvider(FailingModel(), repository)  # type: ignore[arg-type]

    with instrument_run(context), pytest.raises(RuntimeError, match="model unavailable"):
        provider.structured([("human", "migrate")], MigrationProposal)

    assert len(repository.values) == 1
    stored_context, message = repository.values[0]
    assert stored_context == context
    assert message["actor_type"] is ActorType.SUBAGENT
    assert message["actor_name"] == "Migration Subagent"
    assert message["role"] == "model_error"
    assert message["metadata"]["success"] is False
    assert "model unavailable" in message["content"]


def test_explicit_validation_specialist_span_is_stored_as_subagent_message() -> None:
    context = RunContext.create()
    repository = RecordingMessages()
    provider = PersistingModelProvider(FailingModel(), repository)  # type: ignore[arg-type]

    with instrument_run(context):
        provider.record_subagent_interaction(
            "Validation Subagent",
            role="subagent_result",
            content={"verdict": "revise"},
            metadata={"bounded_briefing": True},
        )

    message = repository.values[0][1]
    assert message["actor_type"] is ActorType.SUBAGENT
    assert message["actor_name"] == "Validation Subagent"
    assert message["role"] == "subagent_result"
