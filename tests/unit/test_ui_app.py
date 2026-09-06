from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import gradio as gr
import pytest

from schemashift.domain import SourceRole
from schemashift.ui.app import _apply_stream_event, _conversation_choices, build_app
from schemashift.ui.inspectors import PANEL_NAMES, InspectorPanels
from schemashift.ui.pipeline import empty_pipeline


@dataclass(frozen=True)
class _Artifact:
    conversation_id: UUID
    run_id: UUID
    file_name: str
    local_path: str
    status: Any
    known_differences: Any = None


class _Repository:
    def __init__(self, conversation_id: UUID, artifact: _Artifact) -> None:
        self.conversation_id = conversation_id
        self.artifact = artifact

    def list_conversations(self, *, limit: int = 100) -> list[Any]:
        assert limit == 100
        return [
            SimpleNamespace(
                conversation_id=self.conversation_id,
                title="Existing migration",
            )
        ]

    def list_artifacts(self, conversation_id: UUID) -> list[_Artifact]:
        if conversation_id == self.artifact.conversation_id:
            return [self.artifact]
        return []


class _Migrations:
    def __init__(self, conversation_id: UUID, opened_session_id: UUID) -> None:
        self.conversation_id = conversation_id
        self.opened_session_id = opened_session_id
        self.created: list[tuple[UUID, UUID]] = []
        self.opened: list[UUID] = []
        self.start_envelopes: list[Any] = []
        self.resume_envelopes: list[Any] = []
        self.started: tuple[UUID, UUID, Any] | None = None
        self.resumed: dict[str, Any] | None = None
        self.open_run_status: str | None = None

    def create_conversation(self, title: str) -> tuple[UUID, UUID]:
        assert title == "New migration"
        pair = (uuid4(), uuid4())
        self.created.append(pair)
        return pair

    def open_session(self, conversation_id: UUID) -> UUID:
        self.opened.append(conversation_id)
        return self.opened_session_id

    def reconstruct_conversation(self, conversation_id: UUID) -> dict[str, Any]:
        assert conversation_id == self.conversation_id
        return {
            "messages": [
                {
                    "role": "user",
                    "content": "Migrate my report",
                    "metadata": {"original_sql": "SELECT legacy_id FROM legacy_customer"},
                },
                {"role": "tool_result", "content": "not shown in chat"},
                {"role": "assistant", "content": "Review is needed."},
            ],
            "events": [
                {
                    "event_type": "component_status",
                    "component": "mcp",
                    "status": "done",
                    "label": None,
                    "detail": "Parsed source query",
                    "metadata": {},
                    "created_at": "2026-09-05T12:30:00+00:00",
                    "conversation_id": str(conversation_id),
                    "session_id": str(uuid4()),
                    "run_id": str(uuid4()),
                },
                {
                    "event_type": "component_status",
                    "component": "guardrail",
                    "status": "active",
                    "label": None,
                    "detail": "Waiting for human decision",
                    "metadata": {},
                    "created_at": "2026-09-05T12:30:01+00:00",
                    "conversation_id": str(conversation_id),
                    "session_id": str(uuid4()),
                    "run_id": str(uuid4()),
                },
            ],
            "pending_review": {
                "review_id": str(uuid4()),
                "candidate_id": str(uuid4()),
                "title": "Resolve customer status mapping",
                "reason": "The migration notes conflict.",
                "risk": "medium",
                "payload": {
                    "candidate_sql": "SELECT customer_id FROM customers",
                    "validation": {"verdict": "human_review"},
                    "alternatives": [{"candidate_sql": "SELECT id FROM customers"}],
                    "approvable": True,
                },
            },
            "runs": (
                [{"status": self.open_run_status}] if self.open_run_status is not None else []
            ),
            "sources": [
                {
                    "source_id": "old-schema-upload",
                    "role": "old_schema",
                    "status": "confirmed",
                    "metadata": {},
                },
                {
                    "source_id": "new-schema-upload",
                    "role": "new_schema",
                    "status": "indexed",
                    "metadata": {},
                },
                {
                    "source_id": "old-data-upload",
                    "role": "old_data",
                    "status": "indexed",
                    "metadata": {"database_id": "uploaded-old-db"},
                },
                {
                    "source_id": "new-data-upload",
                    "role": "new_data",
                    "status": "indexed",
                    "metadata": {"database_id": "uploaded-new-db"},
                },
            ],
        }

    def start_run(
        self,
        conversation_id: UUID,
        session_id: UUID,
        request: Any,
    ):
        self.started = (conversation_id, session_id, request)
        yield from self.start_envelopes

    def resume_run(
        self,
        conversation_id: UUID,
        session_id: UUID,
        *,
        review_id: UUID,
        decision: str,
        reviewer_feedback: str,
    ):
        self.resumed = {
            "conversation_id": conversation_id,
            "session_id": session_id,
            "review_id": review_id,
            "decision": decision,
            "reviewer_feedback": reviewer_feedback,
        }
        yield from self.resume_envelopes


class _Registry:
    def get_database(self, database_id: str) -> object:
        if database_id not in {"customer_v1", "customer_v2"}:
            raise LookupError(database_id)
        return object()


class _Staged:
    def __init__(self, row: list[str]) -> None:
        self.row = row

    def table_row(self) -> list[str]:
        return self.row


class _Ingestion:
    def __init__(self) -> None:
        self.staged_paths: list[Path] = []
        self.selections: list[dict[str, Any]] = []

    def stage_files(
        self,
        conversation_id: UUID,
        session_id: UUID,
        paths: list[Path],
    ) -> list[_Staged]:
        assert isinstance(conversation_id, UUID)
        assert isinstance(session_id, UUID)
        self.staged_paths = paths
        return [
            _Staged(
                [
                    str(uuid4()),
                    paths[0].name,
                    "source_sql",
                    "",
                    "0.98",
                    "SQL contains a query expression",
                ]
            )
        ]

    def confirm_sources(
        self,
        conversation_id: UUID,
        session_id: UUID,
        selections: list[dict[str, Any]],
    ) -> list[Any]:
        assert isinstance(conversation_id, UUID)
        assert isinstance(session_id, UUID)
        self.selections = selections
        return [
            SimpleNamespace(
                source_id=UUID(str(item["source_id"])),
                role=SourceRole(str(item["role"])),
            )
            for item in selections
        ]

    def build_data_databases(
        self,
        conversation_id: UUID,
        session_id: UUID,
    ) -> dict[str, str]:
        assert isinstance(conversation_id, UUID)
        assert isinstance(session_id, UUID)
        return {
            "old_database_id": "uploaded-old-db",
            "new_database_id": "uploaded-new-db",
        }


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def discover_tools(self) -> tuple[str, ...]:
        return ("parse_sql", "compare_results")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return {
            "ok": True,
            "content": {
                "kind": "text",
                "text": "SELECT customer_id FROM legacy_customers",
                "truncated": False,
            },
        }


@pytest.fixture
def fake_application(tmp_path: Path) -> Any:
    data_root = tmp_path / "data"
    sql_root = data_root / "sql"
    sql_root.mkdir(parents=True)
    (sql_root / "customer_active.sql").write_text(
        "SELECT customer_id FROM customers",
        encoding="utf-8",
    )
    conversation_id = uuid4()
    session_id = uuid4()
    artifact_path = tmp_path / "approved.sql"
    artifact_path.write_text("SELECT 1", encoding="utf-8")
    artifact = _Artifact(
        conversation_id=conversation_id,
        run_id=uuid4(),
        file_name=artifact_path.name,
        local_path=str(artifact_path),
        status=SimpleNamespace(value="human_approved"),
    )
    return SimpleNamespace(
        settings=SimpleNamespace(
            data_root=data_root,
            float_absolute_tolerance=1e-6,
            float_relative_tolerance=1e-6,
        ),
        repository=_Repository(conversation_id, artifact),
        migrations=_Migrations(conversation_id, session_id),
        registry=_Registry(),
        ingestion=_Ingestion(),
        tool_gateway=_Gateway(),
        _conversation_id=conversation_id,
        _session_id=session_id,
        _artifact=artifact,
    )


def _registered_function(app: gr.Blocks, api_name: str):
    functions = [item.fn for item in app.fns.values() if item.api_name == api_name]
    assert len(functions) == 1, f"Expected one registered /{api_name} endpoint"
    return functions[0]


def _envelope(event: dict[str, Any]) -> Any:
    return SimpleNamespace(event=event)


def _ready_state(application: Any) -> dict[str, Any]:
    return {
        "conversation_id": str(application._conversation_id),
        "session_id": str(application._session_id),
        "statuses": empty_pipeline(),
        "events": [],
        "review": None,
        "busy": False,
        "old_schema_source_id": "old-schema",
        "new_schema_source_id": "new-schema",
        "source_sql_source_id": "source-sql",
        "old_database_id": "old-db",
        "new_database_id": "new-db",
        "original_sql": "SELECT legacy_id FROM legacy_customer",
    }


def test_blocks_configuration_has_required_controls_and_named_apis(
    fake_application: Any,
) -> None:
    app = build_app(fake_application)
    config = app.config
    components = {
        item.get("props", {}).get("elem_id"): item
        for item in config["components"]
        if item.get("props", {}).get("elem_id")
    }
    expected_ids = {
        "conversation_selector",
        "new_conversation_btn",
        "pipeline_rail",
        "chat_history",
        "chat_input",
        "original_sql",
        "send_btn",
        "activity_log",
        "human_decision",
        "reviewer_feedback",
        "approve_btn",
        "reject_btn",
        "source_upload",
        "role_confirmation",
        "confirm_files_btn",
        "artifact_selector",
        "artifact_download",
    }
    api_names = {item.get("api_name") for item in config["dependencies"]}

    assert config["title"] == "SchemaShift"
    assert config["analytics_enabled"] is False
    tabs = [item["props"]["label"] for item in config["components"] if item["type"] == "tabitem"]
    assert tabs == ["Chat", "Human Decision", "Add Files", "View Migrated Files", *PANEL_NAMES]
    for name in PANEL_NAMES:
        assert f"{name.lower()}_inspector" in components
    assert expected_ids <= components.keys()
    assert {
        "initialize",
        "new_conversation",
        "load_conversation",
        "upload_sources",
        "confirm_sources",
        "send",
        "approve_review",
        "reject_review",
        "download_artifact",
    } <= api_names
    assert components["approve_btn"]["props"]["visible"] is False
    assert components["reject_btn"]["props"]["visible"] is False
    assert components["reviewer_feedback"]["props"]["visible"] is False
    assert components["reviewer_feedback"]["props"]["interactive"] is False
    assert components["source_upload"]["props"]["file_types"] == [
        ".sql",
        ".md",
        ".json",
        ".csv",
        ".parquet",
    ]
    assert components["role_confirmation"]["props"]["interactive"] is True


def test_load_conversation_opens_new_session_and_restores_durable_state(
    fake_application: Any,
) -> None:
    app = build_app(fake_application)
    load_conversation = _registered_function(app, "load_conversation")

    output = load_conversation(str(fake_application._conversation_id))
    state, conversation_update, chat = output[:3]
    pipeline_html, activity_html, decision_html = output[3:6]
    approve_update, reject_update, send_update = output[6:9]
    artifact_update, selected_artifact = output[11:13]

    assert fake_application.migrations.opened == [fake_application._conversation_id]
    assert state["conversation_id"] == str(fake_application._conversation_id)
    assert state["session_id"] == str(fake_application._session_id)
    assert state["old_schema_source_id"] == "old-schema-upload"
    assert state["new_schema_source_id"] == "new-schema-upload"
    assert state["old_database_id"] == "uploaded-old-db"
    assert state["new_database_id"] == "uploaded-new-db"
    assert state["original_sql"] == "SELECT legacy_id FROM legacy_customer"
    assert state["statuses"]["mcp"] == "done"
    assert state["statuses"]["guardrail"] == "active"
    assert chat == [
        {"role": "user", "content": "Migrate my report"},
        {"role": "assistant", "content": "Review is needed."},
    ]
    assert conversation_update["value"] == str(fake_application._conversation_id)
    assert "id='mcp' data-component='mcp' class='ss-stage done'" in pipeline_html
    assert "Parsed source query" in activity_html
    assert "Resolve customer status mapping" in decision_html
    assert "Independent validation evidence" in decision_html
    assert approve_update["visible"] is True
    assert approve_update["interactive"] is True
    assert reject_update["visible"] is True
    assert reject_update["interactive"] is True
    assert send_update["interactive"] is False
    assert artifact_update["value"] == fake_application._artifact.local_path
    assert "known differences" not in artifact_update["choices"][0][0]
    assert selected_artifact == fake_application._artifact.local_path


def test_conversation_picker_hides_empty_drafts_and_benchmarks_but_keeps_real_work() -> None:
    current, abandoned, uploaded, waiting, completed, benchmark = [uuid4() for _ in range(6)]
    sources = {uploaded: [SimpleNamespace(role=SourceRole.SOURCE_SQL, original_name="orders.sql")]}
    runs = {
        waiting: [SimpleNamespace(status="waiting_human")],
        completed: [SimpleNamespace(status="completed")],
    }
    messages = {
        waiting: [SimpleNamespace(role="user", content="Migrate the customer report")],
        completed: [SimpleNamespace(role="user", content="Migrate the customer report")],
    }
    repository = SimpleNamespace(
        list_conversations=lambda **_: [
            SimpleNamespace(
                conversation_id=identifier,
                title="Benchmark m01_projection" if identifier == benchmark else "New migration",
            )
            for identifier in (current, abandoned, uploaded, waiting, completed, benchmark)
        ],
        list_sources=lambda identifier, **_: sources.get(identifier, []),
        list_runs=lambda identifier, **_: runs.get(identifier, []),
        list_messages=lambda identifier, **_: messages.get(identifier, []),
    )

    choices = _conversation_choices(SimpleNamespace(repository=repository), str(current))
    labels = {identifier: title for title, identifier in choices}

    assert set(labels) == {str(current), str(uploaded), str(waiting), str(completed)}
    assert labels[str(current)] == "New conversation (current)"
    assert "orders.sql" in labels[str(uploaded)]
    assert "Review needed: Migrate the customer report" in labels[str(waiting)]
    assert labels[str(waiting)] != labels[str(completed)]
    # Filtering is read-only: the repository deliberately exposes no mutation API.


def test_initialize_picker_includes_current_draft_even_outside_history_page(
    fake_application: Any,
) -> None:
    initialize = _registered_function(build_app(fake_application), "initialize")

    state, picker, *_ = initialize()

    assert picker["value"] == state["conversation_id"]
    assert ("New conversation (current)", state["conversation_id"]) in picker["choices"]


def test_picker_label_refresh_cannot_trigger_conversation_reload(fake_application: Any) -> None:
    config = build_app(fake_application).config
    dependency = next(
        item for item in config["dependencies"] if item.get("api_name") == "load_conversation"
    )

    assert [target[1] for target in dependency["targets"]] == ["input"]


def test_reload_of_active_run_keeps_new_submission_disabled(fake_application: Any) -> None:
    fake_application.migrations.open_run_status = "active"
    # An active run has no decision to resolve yet.
    original_reconstruct = fake_application.migrations.reconstruct_conversation

    def without_review(conversation_id: UUID) -> dict[str, Any]:
        result = original_reconstruct(conversation_id)
        result["pending_review"] = None
        return result

    fake_application.migrations.reconstruct_conversation = without_review
    app = build_app(fake_application)
    load_conversation = _registered_function(app, "load_conversation")

    output = load_conversation(str(fake_application._conversation_id))

    assert output[0]["busy"] is True
    assert output[8]["interactive"] is False


def test_upload_and_confirm_handlers_are_exposed_and_update_confirmed_inputs(
    fake_application: Any,
    tmp_path: Path,
) -> None:
    app = build_app(fake_application)
    upload_sources = _registered_function(app, "upload_sources")
    confirm_sources = _registered_function(app, "confirm_sources")
    upload_path = tmp_path / "report.sql"
    upload_path.write_text("SELECT * FROM old_report", encoding="utf-8")
    state = {
        "conversation_id": str(fake_application._conversation_id),
        "session_id": str(fake_application._session_id),
        "original_sql": "",
    }

    staged_state, table_rows, status = upload_sources([str(upload_path)], state)

    assert staged_state is state
    assert fake_application.ingestion.staged_paths == [upload_path]
    assert table_rows[0][1:] == [
        "report.sql",
        "source_sql",
        "",
        "0.98",
        "SQL contains a query expression",
    ]
    assert "correct it if needed" in status

    source_sql_id = table_rows[0][0]
    old_schema_id = str(uuid4())
    corrected_rows = [
        [source_sql_id, "report.sql", "source_sql", "", "0.98", "query"],
        [old_schema_id, "old.sql", "old_schema", "", "0.92", "filename"],
    ]
    updated_state, confirmed, summary, original_sql = confirm_sources(
        corrected_rows,
        state,
    )

    assert fake_application.ingestion.selections == [
        {"source_id": source_sql_id, "role": "source_sql", "table_name": None},
        {"source_id": old_schema_id, "role": "old_schema", "table_name": None},
    ]
    assert updated_state["source_sql_source_id"] == source_sql_id
    assert updated_state["old_schema_source_id"] == old_schema_id
    assert updated_state["old_database_id"] == "uploaded-old-db"
    assert updated_state["new_database_id"] == "uploaded-new-db"
    assert original_sql == "SELECT customer_id FROM legacy_customers"
    assert confirmed == "Confirmed 2 source(s)."
    assert "uploaded-old-db" in summary
    assert fake_application.tool_gateway.calls == [("read_source", {"source_id": source_sql_id})]


def test_custom_input_confirmation_clears_unrepresented_demo_database_side(
    fake_application: Any,
) -> None:
    fake_application.ingestion.build_data_databases = lambda *_args: {
        "new_database_id": "custom-new-db"
    }
    app = build_app(fake_application)
    confirm_sources = _registered_function(app, "confirm_sources")
    state = _ready_state(fake_application)
    state["old_database_id"] = "customer_v1"
    state["new_database_id"] = "customer_v2"
    new_schema_id = str(uuid4())

    updated, *_rest = confirm_sources(
        [[new_schema_id, "schema.sql", "new_schema", "", "0.97", "content version"]],
        state,
    )

    assert updated["uploaded_input_mode"] is True
    assert updated["new_schema_source_id"] == new_schema_id
    assert updated["old_schema_source_id"] == ""
    assert updated["old_database_id"] == ""
    assert updated["new_database_id"] == "custom-new-db"


def test_reload_renders_persisted_human_decision_as_user_chat(fake_application: Any) -> None:
    original_reconstruct = fake_application.migrations.reconstruct_conversation

    def with_decision(conversation_id: UUID) -> dict[str, Any]:
        history = original_reconstruct(conversation_id)
        history["messages"].insert(
            1,
            {
                "role": "human_decision",
                "content": "approve",
                "metadata": {"reviewer_feedback": "Evidence accepted"},
            },
        )
        return history

    fake_application.migrations.reconstruct_conversation = with_decision
    load_conversation = _registered_function(build_app(fake_application), "load_conversation")

    chat = load_conversation(str(fake_application._conversation_id))[2]

    assert {"role": "user", "content": "Approve candidate: Evidence accepted"} in chat


def test_artifact_choice_exposes_known_difference_summary(fake_application: Any) -> None:
    object.__setattr__(
        fake_application._artifact,
        "known_differences",
        {
            "missing_row_count": 1,
            "extra_row_count": 2,
            "issues": [{"message": "Status semantics differ"}],
        },
    )
    load_conversation = _registered_function(build_app(fake_application), "load_conversation")

    artifact_update = load_conversation(str(fake_application._conversation_id))[11]

    label = artifact_update["choices"][0][0]
    assert "known differences: missing=1, extra=2" in label
    assert "Status semantics differ" in label


def test_send_endpoint_streams_real_envelopes_into_chat_and_pipeline(
    fake_application: Any,
) -> None:
    fake_application.migrations.start_envelopes = [
        _envelope(
            {
                "type": "component_status",
                "component": "model",
                "status": "active",
                "detail": "Generating a candidate",
            }
        ),
        _envelope({"type": "assistant_token", "text": "Migration completed."}),
        _envelope(
            {
                "type": "run_output",
                "status": "completed",
                "answer": "Migration completed.",
            }
        ),
    ]
    app = build_app(fake_application)
    send = _registered_function(app, "send")
    state = _ready_state(fake_application)

    stream = send(
        "Migrate the customer report",
        state["original_sql"],
        state,
        [],
    )
    initial = next(stream)
    assert initial[0]["busy"] is True
    updates = list(stream)
    final = updates[-1]
    final_state, final_chat, pipeline_html = final[:3]

    assert fake_application.migrations.started is not None
    conversation_id, session_id, request = fake_application.migrations.started
    assert conversation_id == fake_application._conversation_id
    assert session_id == fake_application._session_id
    assert request.request == "Migrate the customer report"
    assert request.original_sql == "SELECT legacy_id FROM legacy_customer"
    assert request.old_schema_source_id == "old-schema"
    assert request.new_schema_source_id == "new-schema"
    assert request.old_database_id == "old-db"
    assert request.new_database_id == "new-db"
    assert request.comparison_policy == {
        "absolute_tolerance": 1e-6,
        "relative_tolerance": 1e-6,
    }
    assert final_state["busy"] is False
    assert final_state["review"] is None
    assert final_state["statuses"]["model"] == "active"
    assert final_chat == [
        {"role": "user", "content": "Migrate the customer report"},
        {"role": "assistant", "content": "Migration completed."},
    ]
    assert "id='model' data-component='model' class='ss-stage active'" in pipeline_html
    assert final[7]["interactive"] is True


def test_send_interrupt_exposes_review_controls_without_unlocking_new_submissions(
    fake_application: Any,
) -> None:
    review_id = uuid4()
    fake_application.migrations.start_envelopes = [
        _envelope(
            {
                "type": "human_review_required",
                "review_id": str(review_id),
                "candidate_id": str(uuid4()),
                "title": "Choose the documented status mapping",
                "reason": "The two evidence sources conflict.",
                "risk": "medium",
                "payload": {
                    "candidate_sql": "SELECT status FROM customer_status",
                    "validation": {"verdict": "human_review"},
                    "alternatives": [],
                    "approvable": True,
                },
            }
        )
    ]
    app = build_app(fake_application)
    send = _registered_function(app, "send")
    state = _ready_state(fake_application)

    outputs = list(
        send(
            "Migrate ambiguous status logic",
            state["original_sql"],
            state,
            [],
        )
    )
    final = outputs[-1]

    assert final[0]["busy"] is False
    assert final[0]["review"]["review_id"] == str(review_id)
    assert "Independent validation evidence" in final[4]
    assert final[5]["visible"] is True
    assert final[5]["interactive"] is True
    assert final[6]["visible"] is True
    assert final[6]["interactive"] is True
    assert final[7]["interactive"] is False


def test_approve_endpoint_resumes_pending_run_in_current_browser_session(
    fake_application: Any,
) -> None:
    review_id = uuid4()
    state = _ready_state(fake_application)
    state["run_id"] = str(uuid4())
    state["review"] = {
        "type": "human_review_required",
        "review_id": str(review_id),
        "candidate_id": str(uuid4()),
        "payload": {"approvable": True},
    }
    fake_application.migrations.resume_envelopes = [
        _envelope({"type": "assistant_token", "text": "Approved migration."}),
        _envelope(
            {
                "type": "run_output",
                "status": "completed",
                "answer": "Approved migration.",
            }
        ),
    ]
    app = build_app(fake_application)
    approve = _registered_function(app, "approve_review")

    stream = approve("Validation evidence accepted", state, [])
    first = next(stream)
    assert first[0]["busy"] is True
    final = list(stream)[-1]

    assert fake_application.migrations.resumed == {
        "conversation_id": fake_application._conversation_id,
        "session_id": fake_application._session_id,
        "review_id": review_id,
        "decision": "approve",
        "reviewer_feedback": "Validation evidence accepted",
    }
    assert final[0]["busy"] is False
    assert final[0]["review"] is None
    assert final[1] == [
        {"role": "user", "content": "Approve candidate: Validation evidence accepted"},
        {"role": "assistant", "content": "Approved migration."},
    ]
    assert final[5]["visible"] is False
    assert final[6]["visible"] is False
    assert final[7]["interactive"] is True


def test_failed_review_resume_keeps_decision_retryable_in_ui(fake_application: Any) -> None:
    review_id = uuid4()
    state = _ready_state(fake_application)
    state["run_id"] = str(uuid4())
    state["review"] = {
        "type": "human_review_required",
        "review_id": str(review_id),
        "candidate_id": str(uuid4()),
        "payload": {"approvable": True},
    }

    def failed_resume(*_args: Any, **_kwargs: Any):
        raise RuntimeError("checkpoint temporarily unavailable")
        yield  # pragma: no cover - make this a generator

    fake_application.migrations.resume_run = failed_resume
    approve = _registered_function(build_app(fake_application), "approve_review")

    output = list(approve("Retry me", state, []))[-1]

    assert output[0]["busy"] is False
    assert output[0]["review"]["review_id"] == str(review_id)
    assert output[0]["resuming_review_id"] is None
    assert output[1] == []
    assert output[5]["visible"] is True
    assert output[6]["visible"] is True
    assert any(event.get("label") == "Review resume failed" for event in output[0]["events"])


def test_artifact_download_allows_only_files_registered_to_current_conversation(
    fake_application: Any,
    tmp_path: Path,
) -> None:
    app = build_app(fake_application)
    download_artifact = _registered_function(app, "download_artifact")
    state = {"conversation_id": str(fake_application._conversation_id)}

    allowed = download_artifact(fake_application._artifact.local_path, state)

    assert allowed == str(Path(fake_application._artifact.local_path).resolve())

    unregistered = tmp_path / "unregistered.sql"
    unregistered.write_text("SELECT secret", encoding="utf-8")
    with pytest.raises(gr.Error, match="not registered to this conversation"):
        download_artifact(str(unregistered), state)

    with pytest.raises(gr.Error, match="not registered to this conversation"):
        download_artifact(
            fake_application._artifact.local_path,
            {"conversation_id": str(uuid4())},
        )


def test_inspectors_show_scoped_checkpoint_memory_and_subagent_records(
    fake_application: Any,
) -> None:
    state = _ready_state(fake_application)
    state["run_id"] = str(uuid4())
    values = {
        **state,
        "status": "validated",
        "memories": [{"memory": {"text": "Preserve repeated customer IDs", "memory_type": "rule"}}],
        "proposal": {"candidate_sql": "SELECT customer_id FROM orders"},
        "validation": {"verdict": "pass"},
    }

    def get_state(config):
        assert config["configurable"]["thread_id"] == state["conversation_id"]
        return SimpleNamespace(values=values)

    def list_messages(conversation_id, *, run_id, limit, ascending):
        assert str(conversation_id) == state["conversation_id"]
        assert str(run_id) == state["run_id"]
        assert limit == 100 and ascending is False
        return [
            {
                "actor_type": "subagent",
                "actor_name": "Validation Subagent",
                "role": "subagent_result",
                "content": "<script>not HTML</script>",
            },
            {
                "actor_type": "tool",
                "tool_name": "parse_sql",
                "role": "tool_call",
                "content": "SELECT customer_id",
                "tool_call_id": "call-17",
            },
        ]

    fake_application.graph = SimpleNamespace(get_state=get_state)
    fake_application.repository.list_messages = list_messages
    panels = InspectorPanels(fake_application).render(state, [])
    assert len(panels) == 6
    assert state["conversation_id"] in panels[0]
    assert "Preserve repeated customer IDs" in panels[1]
    assert "call-17" in panels[2]
    assert "Validation Subagent" in panels[4]
    assert "&lt;script&gt;not HTML&lt;/script&gt;" in panels[4]
    assert "<script>" not in "".join(panels)


@pytest.mark.parametrize("mismatch", ["run_id", "conversation_id"])
def test_inspectors_do_not_show_a_checkpoint_from_another_run_or_conversation(
    fake_application: Any,
    mismatch: str,
) -> None:
    state = _ready_state(fake_application)
    state["run_id"] = str(uuid4())
    values = {**state, mismatch: str(uuid4()), "request": "OTHER RUN PRIVATE CONTEXT"}
    fake_application.graph = SimpleNamespace(
        get_state=lambda config: SimpleNamespace(values=values)
    )
    panels = InspectorPanels(fake_application)
    output = panels.render(state, [])
    assert "OTHER RUN PRIVATE CONTEXT" not in "".join(output)
    assert "first checkpoint" in output[0]
    assert "Start a migration" in panels.render({}, [])[0]


def test_trace_keeps_status_transitions_but_not_individual_answer_tokens() -> None:
    state: dict[str, Any] = {"statuses": empty_pipeline(), "events": []}
    chat: list[dict[str, str]] = []
    status = {"type": "component_status", "component": "subagent", "status": "active"}
    _apply_stream_event(state, chat, _envelope(status))
    _apply_stream_event(state, chat, _envelope({"type": "assistant_token", "text": "Done"}))
    assert state["trace"] == [status]
    assert state["events"] == []
    assert chat == [{"role": "assistant", "content": "Done"}]
    for _ in range(210):
        _apply_stream_event(state, chat, _envelope(status))
    assert len(state["trace"]) == 200


def test_inspectors_refresh_after_final_checkpoint_and_reset_on_new_conversation(
    fake_application: Any,
) -> None:
    state = _ready_state(fake_application)
    run_id = str(uuid4())
    values: dict[str, Any] = {**state, "run_id": run_id}
    fake_application.graph = SimpleNamespace(
        get_state=lambda config: SimpleNamespace(values=values)
    )

    def start_run(conversation_id, session_id, request):
        yield _envelope({"type": "run_output", "run_id": run_id, "answer": "Finished"})
        # Graph checkpoints finish committing after the custom output is delivered.
        values["memories"] = [{"memory": {"text": "Final checkpoint memory"}}]

    fake_application.migrations.start_run = start_run
    app = build_app(fake_application)
    send = _registered_function(app, "send")
    updates = list(send("Migrate this", "SELECT 1", state, []))
    assert "Final checkpoint memory" in updates[-1][12]
    reset = _registered_function(app, "new_conversation")()
    assert len(reset) == 20
    assert "Final checkpoint memory" not in "".join(reset[-6:])
    assert "No subagent has run" in reset[-2]


def test_graph_panel_renders_the_compiled_topology_locally(fake_application: Any) -> None:
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(dict)
    graph.add_node("inspect_schema", lambda state: state)
    graph.add_edge(START, "inspect_schema")
    graph.add_edge("inspect_schema", END)
    fake_application.graph = graph.compile()
    rendered = InspectorPanels(fake_application).render({}, [])[3]
    assert "<svg" in rendered
    assert "inspect schema</text>" in rendered
    assert "__start__ → inspect_schema" in rendered
    assert "https://" not in rendered
