"""SchemaShift Gradio application with genuine backend event streaming."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import gradio as gr

from schemashift.domain import SourceRole
from schemashift.services import (
    ApplicationRuntime,
    MigrationRequest,
    StreamEnvelope,
    create_application_runtime,
)

from .activity import render_activity
from .decisions import render_decision
from .pipeline import apply_component_event, empty_pipeline, render_pipeline
from .styles import CSS, LAYOUT_JS, hero_html

ROLE_FIELD = {
    SourceRole.OLD_SCHEMA.value: "old_schema_source_id",
    SourceRole.NEW_SCHEMA.value: "new_schema_source_id",
    SourceRole.SOURCE_SQL.value: "source_sql_source_id",
}


def _conversation_choices(
    runtime: ApplicationRuntime, current_id: str | None = None
) -> list[tuple[str, str]]:
    """Show useful history without deleting saved application records.

    Page loads create an empty conversation for uploads and correlation IDs.
    Only the current empty draft belongs in the picker. Older default-titled
    conversations get a label from their source file or first user request.
    """

    choices: list[tuple[str, str]] = []
    for item in runtime.repository.list_conversations(limit=100):
        identifier = str(item.conversation_id)
        title = (item.title or "").strip()
        if title.startswith("Benchmark ") and identifier != current_id:
            continue
        if title.casefold() in {"", "new migration", "new conversation"}:
            sources = runtime.repository.list_sources(item.conversation_id, limit=100)
            runs = runtime.repository.list_runs(item.conversation_id, limit=1)
            messages = runtime.repository.list_messages(item.conversation_id, limit=1)
            if not (sources or runs or messages):
                if identifier == current_id:
                    choices.insert(0, ("New conversation (current)", identifier))
                continue
            sql_source = next(
                (source for source in sources if source.role == SourceRole.SOURCE_SQL), None
            )
            request = next((message.content for message in messages if message.role == "user"), "")
            title = (
                sql_source.original_name
                if sql_source
                else request or ("Uploaded files" if sources else "Migration")
            )
            if runs and runs[0].status == "waiting_human":
                title = f"Review needed: {title}"
        title = " ".join(title.split())
        if len(title) > 64:
            title = title[:61].rstrip() + "..."
        choices.append((f"{title} · {identifier[:8]}", identifier))
    if current_id and not any(identifier == current_id for _, identifier in choices):
        choices.insert(0, ("New conversation (current)", current_id))
    return choices


def _artifact_choices(runtime: ApplicationRuntime, conversation_id: str) -> list[tuple[str, str]]:
    if not conversation_id:
        return []
    choices: list[tuple[str, str]] = []
    for item in runtime.repository.list_artifacts(UUID(conversation_id)):
        parts = [str(item.run_id)[:8], item.file_name, item.status.value]
        differences = getattr(item, "known_differences", None)
        if isinstance(differences, Mapping) and differences:
            missing = int(differences.get("missing_row_count", 0) or 0)
            extra = int(differences.get("extra_row_count", 0) or 0)
            issues = differences.get("issues") or []
            summary = f"known differences: missing={missing}, extra={extra}"
            if issues and isinstance(issues[0], Mapping):
                summary += f", {issues[0].get('message', issues[0].get('code', 'issue'))}"
            parts.append(summary)
        elif differences:
            parts.append(f"known differences: {len(differences)}")
        choices.append((" | ".join(parts), str(item.local_path)))
    return choices


def _event_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    metadata = dict(record.get("metadata") or {})
    return {
        "type": record.get("event_type"),
        "component": record.get("component"),
        "status": record.get("status"),
        "label": record.get("label"),
        "detail": record.get("detail"),
        "level": metadata.get("level", "info"),
        "timestamp": record.get("created_at"),
        "conversation_id": record.get("conversation_id"),
        "session_id": record.get("session_id"),
        "run_id": record.get("run_id"),
        "metadata": metadata,
    }


def _default_sql(runtime: ApplicationRuntime) -> str:
    source = runtime.settings.data_root / "sql" / "customer_active.sql"
    return source.read_text(encoding="utf-8") if source.is_file() else ""


def _has_database(runtime: ApplicationRuntime, database_id: str) -> bool:
    try:
        runtime.registry.get_database(database_id)
    except LookupError:
        return False
    return True


def _fresh_state(
    runtime: ApplicationRuntime,
    conversation_id: UUID,
    session_id: UUID,
) -> dict[str, Any]:
    old_database = "customer_v1" if _has_database(runtime, "customer_v1") else ""
    new_database = "customer_v2" if _has_database(runtime, "customer_v2") else ""
    return {
        "conversation_id": str(conversation_id),
        "session_id": str(session_id),
        "statuses": empty_pipeline(),
        "events": [],
        "review": None,
        "busy": False,
        "resuming_review_id": None,
        "uploaded_input_mode": False,
        "old_schema_source_id": "customer_v1_schema",
        "new_schema_source_id": "customer_v2_schema",
        "source_sql_source_id": "customer_active_sql",
        "old_database_id": old_database,
        "new_database_id": new_database,
        "original_sql": _default_sql(runtime),
    }


def _restore_state(
    runtime: ApplicationRuntime,
    conversation_id: UUID,
    session_id: UUID,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    history = runtime.migrations.reconstruct_conversation(conversation_id)
    state = _fresh_state(runtime, conversation_id, session_id)
    events = [_event_from_record(item) for item in history["events"]]
    statuses = empty_pipeline()
    for event in events:
        if event.get("type") == "component_status":
            statuses = apply_component_event(statuses, event)
    state["events"] = events
    state["statuses"] = statuses
    pending = history.get("pending_review")
    open_runs = [
        item
        for item in history.get("runs", [])
        if item.get("status") in {"pending", "active", "waiting_human"}
    ]
    state["busy"] = bool(open_runs)
    if pending:
        state["review"] = {
            "type": "human_review_required",
            "review_id": pending["review_id"],
            "candidate_id": pending["candidate_id"],
            "title": pending["title"],
            "reason": pending["reason"],
            "risk": pending["risk"],
            "payload": pending["payload"],
        }
        # Waiting is not active computation: enable only the decision controls.
        state["busy"] = False
    scoped_sources = list(history["sources"])
    custom_inputs = any(
        str(source.get("role") or "")
        in {
            SourceRole.OLD_SCHEMA.value,
            SourceRole.NEW_SCHEMA.value,
            SourceRole.OLD_DATA.value,
            SourceRole.NEW_DATA.value,
        }
        and source.get("status") in {"confirmed", "indexed"}
        for source in scoped_sources
    )
    if custom_inputs:
        state["uploaded_input_mode"] = True
        for key in (
            "old_schema_source_id",
            "new_schema_source_id",
            "old_database_id",
            "new_database_id",
        ):
            state[key] = ""
    for source in scoped_sources:
        role = str(source.get("role") or "")
        if role in ROLE_FIELD and source.get("status") in {"confirmed", "indexed"}:
            state[ROLE_FIELD[role]] = str(source["source_id"])
        metadata = source.get("metadata") or {}
        database_id = str(metadata.get("database_id") or "")
        if role == SourceRole.OLD_DATA.value and database_id:
            state["old_database_id"] = database_id
        elif role == SourceRole.NEW_DATA.value and database_id:
            state["new_database_id"] = database_id
    chat: list[dict[str, str]] = []
    for message in history["messages"]:
        role = str(message.get("role") or "")
        if role == "user":
            chat.append({"role": "user", "content": str(message["content"])})
            stored_sql = str((message.get("metadata") or {}).get("original_sql") or "")
            if stored_sql:
                state["original_sql"] = stored_sql
        elif role == "assistant":
            chat.append({"role": "assistant", "content": str(message["content"])})
        elif role == "human_decision":
            metadata = message.get("metadata") or {}
            feedback = str(metadata.get("reviewer_feedback") or "").strip()
            decision = str(message.get("content") or "Decision").title()
            chat.append(
                {
                    "role": "user",
                    "content": f"{decision} candidate" + (f": {feedback}" if feedback else "."),
                }
            )
    return state, chat


def _source_summary(state: Mapping[str, Any]) -> str:
    return (
        "**Confirmed run inputs**  \n"
        f"Old schema: `{state.get('old_schema_source_id') or 'missing'}`  \n"
        f"New schema: `{state.get('new_schema_source_id') or 'missing'}`  \n"
        f"Old data: `{state.get('old_database_id') or 'missing'}`  \n"
        f"New data: `{state.get('new_database_id') or 'missing'}`"
    )


def _review_updates(state: Mapping[str, Any]) -> tuple[Any, Any]:
    review = state.get("review")
    busy = bool(state.get("busy"))
    approvable = False
    if isinstance(review, Mapping):
        payload = review.get("payload")
        approvable = bool(isinstance(payload, Mapping) and payload.get("approvable"))
    return (
        gr.update(visible=bool(review), interactive=bool(review) and approvable and not busy),
        gr.update(visible=bool(review), interactive=bool(review) and not busy),
    )


def _review_feedback_update(state: Mapping[str, Any]) -> Any:
    review = bool(state.get("review"))
    return gr.update(visible=review, interactive=review and not bool(state.get("busy")))


def _run_view(
    runtime: ApplicationRuntime,
    state: dict[str, Any],
    chat: list[dict[str, str]],
) -> tuple[Any, ...]:
    approve, reject = _review_updates(state)
    artifacts = _artifact_choices(runtime, str(state.get("conversation_id", "")))
    return (
        state,
        chat,
        render_pipeline(state.get("statuses")),
        render_activity(state.get("events", [])),
        render_decision(state.get("review")),
        approve,
        reject,
        gr.update(interactive=not state.get("busy") and not state.get("review")),
        gr.update(choices=artifacts, value=artifacts[0][1] if artifacts else None),
        artifacts[0][1] if artifacts else None,
        _review_feedback_update(state),
    )


def _apply_stream_event(
    state: dict[str, Any],
    chat: list[dict[str, str]],
    envelope: StreamEnvelope,
) -> None:
    event = dict(envelope.event)
    event_type = str(event.get("type", ""))
    for key in ("conversation_id", "session_id", "run_id"):
        if event.get(key):
            state[key] = str(event[key])
    if event_type == "component_status":
        state["statuses"] = apply_component_event(state.get("statuses"), event)
    elif event_type in {"activity", "human_review_required"}:
        state.setdefault("events", []).append(event)
    if event_type == "human_review_required":
        previous_review_id = str(state.get("resuming_review_id") or "")
        state["review"] = event
        if str(event.get("review_id") or "") != previous_review_id:
            state["busy"] = False
            state["resuming_review_id"] = None
    elif event_type == "assistant_token":
        if not chat or chat[-1].get("role") != "assistant":
            chat.append({"role": "assistant", "content": ""})
        chat[-1]["content"] += str(event.get("text", ""))
    elif event_type == "run_output":
        answer = str(event.get("answer") or "")
        if answer:
            if not chat or chat[-1].get("role") != "assistant":
                chat.append({"role": "assistant", "content": answer})
            else:
                chat[-1]["content"] = answer
        state["review"] = None
        state["busy"] = False
        state["resuming_review_id"] = None


def _mark_ui_failure(state: dict[str, Any], *, label: str, detail: str) -> None:
    """Render a failed stream without inventing an uncorrelated runtime event."""

    statuses = dict(state.get("statuses") or empty_pipeline())
    for name, status in statuses.items():
        if status == "active":
            statuses[name] = "error"
    state["statuses"] = statuses
    already_delivered = any(
        event.get("type") == "activity" and event.get("label") == label
        for event in state.get("events", [])
    )
    correlation = [state.get(key) for key in ("conversation_id", "session_id", "run_id")]
    if not already_delivered and all(correlation):
        state.setdefault("events", []).append(
            {
                "type": "activity",
                "level": "error",
                "label": label,
                "detail": detail,
                "metadata": {"origin": "ui_stream_fallback"},
                "conversation_id": correlation[0],
                "session_id": correlation[1],
                "run_id": correlation[2],
            }
        )


def build_app(runtime: ApplicationRuntime | None = None) -> gr.Blocks:
    """Build the Blocks UI; browser state is isolated in ``gr.State``."""

    application = runtime or create_application_runtime()

    def initialize():
        conversation_id, session_id = application.migrations.create_conversation("New migration")
        state = _fresh_state(application, conversation_id, session_id)
        choices = _conversation_choices(application, str(conversation_id))
        approve, reject = _review_updates(state)
        return (
            state,
            gr.update(choices=choices, value=str(conversation_id)),
            [],
            render_pipeline(state["statuses"]),
            render_activity([]),
            render_decision(None),
            approve,
            reject,
            gr.update(interactive=True),
            state["original_sql"],
            _source_summary(state),
            gr.update(choices=[], value=None),
            None,
            _review_feedback_update(state),
        )

    def new_conversation():
        return initialize()

    def select_conversation(selected: str):
        if not selected:
            return initialize()
        conversation_id = UUID(selected)
        session_id = application.migrations.open_session(conversation_id)
        state, chat = _restore_state(application, conversation_id, session_id)
        choices = _conversation_choices(application, selected)
        approve, reject = _review_updates(state)
        artifacts = _artifact_choices(application, selected)
        return (
            state,
            gr.update(choices=choices, value=selected),
            chat,
            render_pipeline(state["statuses"]),
            render_activity(state["events"]),
            render_decision(state.get("review")),
            approve,
            reject,
            gr.update(interactive=not bool(state.get("review")) and not state.get("busy")),
            state["original_sql"],
            _source_summary(state),
            gr.update(choices=artifacts, value=artifacts[0][1] if artifacts else None),
            artifacts[0][1] if artifacts else None,
            _review_feedback_update(state),
        )

    def refresh_conversations(state: Mapping[str, Any]):
        current_id = str(state.get("conversation_id") or "") or None
        return gr.update(
            choices=_conversation_choices(application, current_id), value=current_id
        )

    def stage_uploads(files: Any, state: dict[str, Any]):
        if not files:
            return state, [], "Choose one or more accepted local files."
        values = files if isinstance(files, list) else [files]
        paths = [Path(getattr(item, "name", item)) for item in values]
        staged = application.ingestion.stage_files(
            UUID(state["conversation_id"]),
            UUID(state["session_id"]),
            paths,
        )
        return (
            state,
            [item.table_row() for item in staged],
            "Review every inferred role, correct it if needed, then confirm.",
        )

    def confirm_uploads(table: Any, state: dict[str, Any]):
        rows = table.values.tolist() if hasattr(table, "values") else list(table or [])
        selections = [
            {
                "source_id": row[0],
                "role": row[2],
                "table_name": row[3] or None,
            }
            for row in rows
            if row and row[0]
        ]
        if not selections:
            return (
                state,
                "No staged sources to confirm.",
                _source_summary(state),
                state.get("original_sql", ""),
            )
        confirmed = application.ingestion.confirm_sources(
            UUID(state["conversation_id"]),
            UUID(state["session_id"]),
            selections,
        )
        controlled_roles = {
            SourceRole.OLD_SCHEMA,
            SourceRole.NEW_SCHEMA,
            SourceRole.OLD_DATA,
            SourceRole.NEW_DATA,
        }
        if any(item.role in controlled_roles for item in confirmed):
            if not state.get("uploaded_input_mode"):
                for key in (
                    "old_schema_source_id",
                    "new_schema_source_id",
                    "old_database_id",
                    "new_database_id",
                ):
                    state[key] = ""
            state["uploaded_input_mode"] = True
        if any(item.role in {SourceRole.OLD_DATA, SourceRole.NEW_DATA} for item in confirmed):
            state["old_database_id"] = ""
            state["new_database_id"] = ""
        for item in confirmed:
            role = item.role.value if item.role else ""
            if role in ROLE_FIELD:
                state[ROLE_FIELD[role]] = str(item.source_id)
            if role == SourceRole.SOURCE_SQL.value:
                result = application.tool_gateway.call_tool(
                    "read_source", {"source_id": str(item.source_id)}
                )
                content = result.get("content")
                content = content if isinstance(content, Mapping) else {}
                state["original_sql"] = str(
                    content.get("text") or result.get("text") or state.get("original_sql", "")
                )
        databases = application.ingestion.build_data_databases(
            UUID(state["conversation_id"]), UUID(state["session_id"])
        )
        state.update(databases)
        return (
            state,
            f"Confirmed {len(confirmed)} source(s).",
            _source_summary(state),
            state.get("original_sql", ""),
        )

    def run_migration(
        prompt: str,
        original_sql: str,
        state: dict[str, Any],
        chat: list[dict[str, str]] | None,
    ):
        chat = list(chat or [])
        if state.get("review"):
            raise gr.Error("Resolve the pending Human Decision before starting another run.")
        if state.get("busy"):
            raise gr.Error("This conversation already has an active run.")
        required = [
            "old_schema_source_id",
            "new_schema_source_id",
            "old_database_id",
            "new_database_id",
        ]
        missing = [name for name in required if not state.get(name)]
        if missing:
            raise gr.Error("Confirm both schemas and paired old/new datasets first.")
        prompt = prompt.strip()
        original_sql = original_sql.strip()
        if not prompt or not original_sql:
            raise gr.Error("A request and original SELECT query are required.")
        chat.append({"role": "user", "content": prompt})
        state["busy"] = True
        state["original_sql"] = original_sql
        yield _run_view(application, state, chat)
        request = MigrationRequest(
            request=prompt,
            original_sql=original_sql,
            old_schema_source_id=state["old_schema_source_id"],
            new_schema_source_id=state["new_schema_source_id"],
            old_database_id=state["old_database_id"],
            new_database_id=state["new_database_id"],
            comparison_policy={
                "absolute_tolerance": application.settings.float_absolute_tolerance,
                "relative_tolerance": application.settings.float_relative_tolerance,
            },
        )
        try:
            for envelope in application.migrations.start_run(
                UUID(state["conversation_id"]),
                UUID(state["session_id"]),
                request,
            ):
                _apply_stream_event(state, chat, envelope)
                yield _run_view(application, state, chat)
        except Exception as exc:
            state["busy"] = False
            _mark_ui_failure(state, label="Run failed", detail=str(exc))
            chat.append(
                {
                    "role": "assistant",
                    "content": f"SchemaShift could not complete this run: {exc}",
                }
            )
            yield _run_view(application, state, chat)

    def review_decision(
        choice: str,
        feedback: str,
        state: dict[str, Any],
        chat: list[dict[str, str]] | None,
    ):
        chat = list(chat or [])
        review = state.get("review")
        if not isinstance(review, Mapping):
            raise gr.Error("There is no pending Human Decision.")
        state["busy"] = True
        state["resuming_review_id"] = str(review["review_id"])
        chat.append(
            {
                "role": "user",
                "content": f"{choice.title()} candidate"
                + (f": {feedback.strip()}" if feedback.strip() else "."),
            }
        )
        yield _run_view(application, state, chat)
        try:
            for envelope in application.migrations.resume_run(
                UUID(state["conversation_id"]),
                UUID(state["session_id"]),
                review_id=UUID(str(review["review_id"])),
                decision=choice,
                reviewer_feedback=feedback,
            ):
                _apply_stream_event(state, chat, envelope)
                yield _run_view(application, state, chat)
        except Exception as exc:
            state["busy"] = False
            state["resuming_review_id"] = None
            state["review"] = dict(review)
            if (
                chat
                and chat[-1].get("role") == "user"
                and chat[-1].get("content", "").startswith(choice.title())
            ):
                chat.pop()
            _mark_ui_failure(state, label="Review resume failed", detail=str(exc))
            yield _run_view(application, state, chat)

    def approve(feedback: str, state: dict[str, Any], chat: Any):
        yield from review_decision("approve", feedback, state, chat)

    def reject(feedback: str, state: dict[str, Any], chat: Any):
        yield from review_decision("reject", feedback, state, chat)

    def select_artifact(path: str, state: Mapping[str, Any]):
        if not path:
            return None
        allowed = {
            item.local_path
            for item in application.repository.list_artifacts(UUID(str(state["conversation_id"])))
        }
        resolved = str(Path(path).resolve())
        if resolved not in {str(Path(item).resolve()) for item in allowed}:
            raise gr.Error("That artifact is not registered to this conversation.")
        return resolved

    with gr.Blocks(analytics_enabled=False, title="SchemaShift", fill_width=True) as demo:
        demo.load(fn=None, js=LAYOUT_JS)
        browser_state = gr.State({})
        gr.HTML(hero_html("Local | PostgreSQL + Redis Stack"), elem_id="workspace_header")
        with gr.Row(elem_id="conversation_toolbar"):
            conversation = gr.Dropdown(
                label="Conversation history",
                choices=[],
                elem_id="conversation_selector",
                scale=5,
            )
            new_button = gr.Button(
                "New Conversation", elem_id="new_conversation_btn", size="sm", scale=1
            )
        pipeline = gr.HTML(render_pipeline(), elem_id="pipeline_rail")
        with gr.Tabs(elem_id="workspace_tabs"):
            with gr.Tab("Chat"), gr.Row(elem_id="chat_workspace"):
                with gr.Column(scale=5, min_width=320, elem_id="conversation_column"):
                    chatbot = gr.Chatbot(
                        label="Migration conversation",
                        placeholder="Describe your migration and select Migrate SQL to begin.",
                        height="var(--ss-workspace-height)",
                        elem_id="chat_history",
                    )
                with gr.Column(scale=4, min_width=320, elem_id="query_column"):
                    prompt = gr.Textbox(
                        label="Migration request",
                        placeholder="Describe how this query should work on the new schema…",
                        lines=1,
                        max_lines=3,
                        elem_id="chat_input",
                    )
                    original_sql = gr.Code(
                        label="Original SELECT SQL",
                        language="sql",
                        lines=8,
                        elem_id="original_sql",
                    )
                    send = gr.Button(
                        "Migrate SQL", variant="primary", size="sm", elem_id="send_btn"
                    )
                with gr.Column(
                    scale=3, min_width=240, elem_id="activity_column", elem_classes="ss-panel"
                ):
                    gr.Markdown("### Agent Activity")
                    activity = gr.HTML(render_activity([]), elem_id="activity_log")
            with gr.Tab("Human Decision"):
                decision_panel = gr.HTML(render_decision(None), elem_id="human_decision")
                reviewer_feedback = gr.Textbox(
                    label="Reviewer feedback",
                    placeholder="Explain a rejection or record approval context…",
                    lines=2,
                    max_lines=4,
                    visible=False,
                    interactive=False,
                    elem_id="reviewer_feedback",
                )
                with gr.Row():
                    approve_button = gr.Button(
                        "Approve displayed candidate",
                        size="sm",
                        visible=False,
                        elem_id="approve_btn",
                    )
                    reject_button = gr.Button(
                        "Reject / request revision",
                        size="sm",
                        visible=False,
                        elem_id="reject_btn",
                    )
            with gr.Tab("Add Files"):
                upload = gr.File(
                    label="SQL, schema, migration knowledge, CSV, or Parquet",
                    file_count="multiple",
                    type="filepath",
                    file_types=[".sql", ".md", ".json", ".csv", ".parquet"],
                    elem_id="source_upload",
                )
                role_table = gr.Dataframe(
                    headers=["Source ID", "File", "Role", "Table", "Confidence", "Reason"],
                    datatype=["str", "str", "str", "str", "str", "str"],
                    interactive=True,
                    wrap=True,
                    elem_id="role_confirmation",
                )
                upload_status = gr.Markdown("Upload files to infer provisional roles.")
                confirm_button = gr.Button(
                    "Confirm roles and ingest",
                    variant="primary",
                    size="sm",
                    elem_id="confirm_files_btn",
                )
                source_summary = gr.Markdown("No source selection loaded.")
            with gr.Tab("View Migrated Files"):
                artifact_select = gr.Dropdown(
                    label="Validated or human-approved artifact",
                    choices=[],
                    elem_id="artifact_selector",
                )
                artifact_download = gr.File(
                    label="Download selected migration",
                    interactive=False,
                    elem_id="artifact_download",
                )
        initialization_outputs = [
            browser_state,
            conversation,
            chatbot,
            pipeline,
            activity,
            decision_panel,
            approve_button,
            reject_button,
            send,
            original_sql,
            source_summary,
            artifact_select,
            artifact_download,
            reviewer_feedback,
        ]
        run_outputs = [
            browser_state,
            chatbot,
            pipeline,
            activity,
            decision_panel,
            approve_button,
            reject_button,
            send,
            artifact_select,
            artifact_download,
            reviewer_feedback,
        ]
        demo.load(initialize, outputs=initialization_outputs, api_name="initialize")
        new_button.click(
            new_conversation,
            outputs=initialization_outputs,
            api_name="new_conversation",
        )
        # Only a user selection should open a session. Updating the choices or
        # their labels must not re-enter this callback or reset a running UI.
        conversation.input(
            select_conversation,
            inputs=conversation,
            outputs=initialization_outputs,
            api_name="load_conversation",
        )
        upload.upload(
            stage_uploads,
            inputs=[upload, browser_state],
            outputs=[browser_state, role_table, upload_status],
            api_name="upload_sources",
        )
        confirm_button.click(
            confirm_uploads,
            inputs=[role_table, browser_state],
            outputs=[browser_state, upload_status, source_summary, original_sql],
            api_name="confirm_sources",
        ).then(
            refresh_conversations, inputs=browser_state, outputs=conversation, api_name=False
        )
        send.click(
            run_migration,
            inputs=[prompt, original_sql, browser_state, chatbot],
            outputs=run_outputs,
            api_name="send",
        ).then(
            refresh_conversations, inputs=browser_state, outputs=conversation, api_name=False
        )
        prompt.submit(
            run_migration,
            inputs=[prompt, original_sql, browser_state, chatbot],
            outputs=run_outputs,
        ).then(
            refresh_conversations, inputs=browser_state, outputs=conversation, api_name=False
        )
        approve_button.click(
            approve,
            inputs=[reviewer_feedback, browser_state, chatbot],
            outputs=run_outputs,
            api_name="approve_review",
        ).then(
            refresh_conversations, inputs=browser_state, outputs=conversation, api_name=False
        )
        reject_button.click(
            reject,
            inputs=[reviewer_feedback, browser_state, chatbot],
            outputs=run_outputs,
            api_name="reject_review",
        ).then(
            refresh_conversations, inputs=browser_state, outputs=conversation, api_name=False
        )
        artifact_select.change(
            select_artifact,
            inputs=[artifact_select, browser_state],
            outputs=artifact_download,
            api_name="download_artifact",
        )

    return demo.queue(default_concurrency_limit=4)


def main() -> None:
    runtime = create_application_runtime()
    app = build_app(runtime)
    app.launch(
        server_name=runtime.settings.gradio_host,
        server_port=runtime.settings.gradio_port,
        share=False,
        show_error=True,
        enable_monitoring=False,
        max_file_size=runtime.settings.upload_max_bytes,
        css=CSS,
    )


if __name__ == "__main__":
    main()


__all__ = ["build_app", "main"]
