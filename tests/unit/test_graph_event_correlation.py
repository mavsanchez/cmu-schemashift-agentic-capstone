from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import TypeAdapter

from schemashift.domain import RuntimeEvent
from schemashift.graph.events import activity, component, correlated


def _state() -> dict[str, str]:
    run_id = str(uuid4())
    return {
        "conversation_id": str(uuid4()),
        "session_id": str(uuid4()),
        "run_id": run_id,
        "turn_id": run_id,
    }


def test_every_meaningful_graph_event_is_correlated_and_wire_valid() -> None:
    state = _state()
    payloads = [
        component(state, "mcp", "active", "Inspecting registered source"),
        activity(state, "Schema inspected", "One table", metadata={"tables": 1}),
    ]

    adapter = TypeAdapter(RuntimeEvent)
    for payload in payloads:
        assert payload["conversation_id"] == state["conversation_id"]
        assert payload["session_id"] == state["session_id"]
        assert payload["run_id"] == state["run_id"]
        assert datetime.fromisoformat(payload["timestamp"]).utcoffset() is not None
        adapter.validate_python(payload)


def test_correlation_cannot_overwrite_an_explicit_event_identity() -> None:
    state = _state()
    explicit_run = str(uuid4())

    payload = correlated(state, {"type": "activity", "run_id": explicit_run})

    assert payload["run_id"] == explicit_run
