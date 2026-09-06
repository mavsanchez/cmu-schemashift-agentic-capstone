"""Read-only inspection panels for the selected conversation and migration run."""

from __future__ import annotations

import html
import inspect
import json
from collections import deque
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from schemashift.mcp import tools

from .activity import render_activity

PANEL_NAMES = ("Context", "Memory", "Tools", "Graph", "Subagent", "Trace")


def _escape(value: Any) -> str:
    return html.escape(str(value))


def _empty(text: str) -> str:
    return f"<p class='ss-empty'>{_escape(text)}</p>"


def _panel(title: str, description: str, body: str) -> str:
    return (
        f"<section class='ss-inspector'><header><h2>{_escape(title)}</h2>"
        f"<p>{_escape(description)}</p></header>{body}</section>"
    )


def _card(title: str, body: str) -> str:
    return f"<article class='ss-inspector-card'><h3>{_escape(title)}</h3>{body}</article>"


def _fields(values: Mapping[str, Any]) -> str:
    return (
        "<dl class='ss-inspector-fields'>"
        + "".join(
            f"<div><dt>{_escape(key)}</dt><dd>{_escape(value)}</dd></div>"
            for key, value in values.items()
        )
        + "</dl>"
    )


def _code(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, indent=2, default=str)
    if len(text) > 12_000:
        text = text[:12_000] + "\n… Preview truncated to 12,000 characters."
    return f"<pre><code>{_escape(text)}</code></pre>"


def _details(title: str, value: Any) -> str:
    return f"<details><summary>{_escape(title)}</summary>{_code(value)}</details>"


def _record(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


def render_graph(graph: Any) -> str:
    """Draw the compiled topology locally, without a Mermaid image service."""
    if graph is None:
        return _empty("The compiled graph is unavailable.")
    try:
        topology = graph.get_graph()
        nodes = list(topology.nodes)
        edges = list(topology.edges)
    except Exception as exc:
        return _empty(f"The graph could not be read: {exc}")
    if not nodes:
        return _empty("The compiled graph has no nodes.")

    # Breadth-first layers keep branches side by side and leave loop edges intact.
    start = "__start__" if "__start__" in nodes else nodes[0]
    levels = {start: 0}
    pending = deque([start])
    while pending:
        source = pending.popleft()
        for edge in edges:
            if edge.source == source and edge.target not in levels:
                levels[edge.target] = levels[source] + 1
                pending.append(edge.target)
    for name in nodes:
        if name not in levels:
            levels[name] = max(levels.values()) + 1
    layers: dict[int, list[str]] = {}
    for name in nodes:
        layers.setdefault(levels[name], []).append(name)
    width = max(620, max(map(len, layers.values())) * 230 + 80)
    height = (max(layers) + 1) * 88 + 40
    positions = {
        name: ((width - len(layer) * 230) / 2 + index * 230 + 115, level * 88 + 42)
        for level, layer in layers.items()
        for index, name in enumerate(layer)
    }
    svg = [
        f"<svg class='ss-graph' viewBox='0 0 {width} {height}' role='img' "
        "aria-label='Compiled SchemaShift workflow graph'>",
        "<defs><marker id='ss-graph-arrow' markerWidth='8' markerHeight='8' "
        "refX='7' refY='4' orient='auto'><path d='M0 0 L8 4 L0 8 Z' "
        "fill='currentColor'/></marker></defs>",
    ]
    for edge in edges:
        x1, y1 = positions[edge.source]
        x2, y2 = positions[edge.target]
        if y2 > y1:
            middle = (y1 + y2) / 2
            path = f"M{x1},{y1 + 22} C{x1},{middle} {x2},{middle} {x2},{y2 - 26}"
        else:
            side = max(x1, x2) + 112
            path = f"M{x1 + 100},{y1} C{side},{y1} {side},{y2} {x2 + 104},{y2}"
        dash = " stroke-dasharray='5 4'" if edge.conditional else ""
        svg.append(
            f"<path d='{path}' fill='none' stroke='currentColor' stroke-width='1.5'"
            f"{dash} marker-end='url(#ss-graph-arrow)'><title>"
            f"{_escape(edge.source)} → {_escape(edge.target)}</title></path>"
        )
    for name, (x, y) in positions.items():
        label = name.strip("_").replace("_", " ")
        svg.append(
            f"<g><rect x='{x - 100}' y='{y - 22}' width='200' height='44' rx='9'/>"
            f"<text x='{x}' y='{y + 5}' text-anchor='middle'>{_escape(label)}</text></g>"
        )
    svg.append("</svg>")
    return (
        "<div class='ss-graph-wrap'>"
        + "".join(svg)
        + "</div>"
        + _details(
            "All compiled edges",
            [
                f"{edge.source} → {edge.target}" + (" (conditional)" if edge.conditional else "")
                for edge in edges
            ],
        )
    )


class InspectorPanels:
    """Only static topology and tool names are cached; run data stays session-scoped."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.graph_html = render_graph(getattr(runtime, "graph", None))
        self.tools_html = self._tool_catalog()

    def _tool_catalog(self) -> str:
        discover = getattr(self.runtime.tool_gateway, "discover_tools", None)
        if not callable(discover):
            return _empty("MCP tool discovery is unavailable.")
        try:
            names = discover()
        except Exception as exc:
            return _empty(f"MCP tool discovery failed: {exc}")
        cards = []
        for name in names:
            function = getattr(tools, name, None)
            description = (inspect.getdoc(function) or "Registered MCP tool.").splitlines()[0]
            parameters = (
                ", ".join(key for key in inspect.signature(function).parameters if key != "context")
                if callable(function)
                else ""
            )
            cards.append(_card(name, f"<p>{_escape(description)}</p>" + _code(parameters)))
        return "<div class='ss-inspector-grid'>" + "".join(cards) + "</div>"

    def _snapshot(self, state: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        if not state.get("run_id"):
            return {}, "Start a migration to inspect its checkpoint."
        graph = getattr(self.runtime, "graph", None)
        if graph is None:
            return {}, "Checkpoint inspection is unavailable."
        try:
            snapshot = graph.get_state(
                {"configurable": {"thread_id": str(state["conversation_id"])}}
            )
            values = dict(snapshot.values or {})
            if str(values.get("conversation_id")) != str(state["conversation_id"]) or str(
                values.get("run_id")
            ) != str(state["run_id"]):
                return {}, "Waiting for this run's first checkpoint."
            return values, "Latest saved checkpoint; updates as graph nodes finish."
        except Exception as exc:
            return {}, f"Checkpoint unavailable: {exc}"

    def _messages(self, state: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
        read = getattr(self.runtime.repository, "list_messages", None)
        if not state.get("run_id") or not callable(read):
            return [], "No recorded interactions for this run yet."
        try:
            records = read(
                UUID(str(state["conversation_id"])),
                run_id=UUID(str(state["run_id"])),
                limit=100,
                ascending=False,
            )
            return [_record(item) for item in reversed(records)], "Latest 100 run interactions."
        except Exception as exc:
            return [], f"Recorded interactions unavailable: {exc}"

    def render(self, state: Mapping[str, Any], chat: list[dict[str, str]]) -> tuple[str, ...]:
        snapshot, checkpoint_note = self._snapshot(state)
        records, record_note = self._messages(state)
        identity = _fields(
            {
                "Conversation": state.get("conversation_id") or "Not started",
                "Session": state.get("session_id") or "Not started",
                "Run": state.get("run_id") or "Not started",
                "Status": "Review needed"
                if state.get("review")
                else (
                    "Running"
                    if state.get("busy")
                    else state.get("run_status") or snapshot.get("status", "Ready")
                ),
            }
        )
        inputs = _fields(
            {
                "Old schema": state.get("old_schema_source_id") or "Missing",
                "New schema": state.get("new_schema_source_id") or "Missing",
                "Old dataset": state.get("old_database_id") or "Missing",
                "New dataset": state.get("new_database_id") or "Missing",
            }
        )
        context = _panel(
            "Context",
            "Inputs and saved state for the selected migration.",
            "<div class='ss-inspector-grid'>"
            + _card("Current run", identity)
            + _card("Selected sources", inputs)
            + "</div>"
            + _card("Original SQL", _code(state.get("original_sql") or "No SQL selected."))
            + _card(
                "Checkpoint",
                _empty(checkpoint_note)
                + _details(
                    "Request and gathered context",
                    {
                        key: snapshot[key]
                        for key in (
                            "request",
                            "history",
                            "sources",
                            "impact",
                            "evidence",
                            "parsed_sql",
                        )
                        if key in snapshot
                    },
                ),
            )
            + _details(f"Conversation transcript · {len(chat)} messages", chat),
        )
        memory_status = (
            "Semantic memory available"
            if getattr(self.runtime, "memory_store", None) is not None
            else "Semantic memory unavailable"
        )
        recalled = snapshot.get("memories") or []
        memory_body = _card(
            memory_status, _empty(getattr(self.runtime, "redis_detail", "") or checkpoint_note)
        )
        for hit in recalled:
            record = hit.get("memory", hit)
            memory_body += _card(
                str(record.get("memory_type", "Recalled memory")),
                f"<p>{_escape(record.get('text', ''))}</p>" + _details("Provenance and score", hit),
            )
        if not recalled:
            memory_body += _empty("No recalled memories in this run's checkpoint.")
        writes = (snapshot.get("memory_candidate") or {}).get("writes") or []
        memory_body += _details(f"Memory write decisions · {len(writes)}", writes)
        memory = _panel(
            "Memory",
            "Durable facts recalled for this run and memory writes from its reflection step.",
            memory_body,
        )

        tool_records = [item for item in records if item.get("actor_type") == "tool"]
        tool_body = self.tools_html + _card(
            "Tool calls and results",
            _empty(record_note)
            + self._interactions(tool_records, "No tool calls recorded for this run yet."),
        )
        tool_panel = _panel(
            "Tools", "Runtime-discovered MCP tools and their recorded results.", tool_body
        )
        graph_panel = _panel(
            "Graph",
            "Compiled parent workflow. Dashed arrows are conditional routes; review resumes "
            "through a dynamic decision.",
            self.graph_html,
        )

        specialists = [item for item in records if item.get("actor_type") == "subagent"]
        sub_body = "<div class='ss-inspector-grid'>"
        for name, key, description in (
            ("Migration", "proposal", "Generates SQL from a bounded schema and evidence briefing."),
            ("Validation", "validation", "Independently checks SQL and compares old/new results."),
        ):
            output = snapshot.get(key) or {}
            sub_body += _card(
                name,
                f"<p>{_escape(description)}</p>"
                + (
                    _details("Latest returned result", output)
                    if output
                    else _empty("No result in this run's checkpoint yet.")
                ),
            )
        sub_body += "</div>" + _card(
            "Briefings and returned work",
            _empty(record_note)
            + self._interactions(specialists, "No subagent has run in this migration yet."),
        )
        sub_panel = _panel(
            "Subagent",
            "Isolated Migration and Validation specialists, scoped to the selected run.",
            sub_body,
        )
        trace_events = state.get("trace", state.get("events", []))
        trace = _panel(
            "Trace",
            "Recent backend events for this conversation, oldest to newest.",
            identity
            + render_activity(trace_events)
            + _details("Event metadata and correlation IDs · latest 200", trace_events[-200:]),
        )
        return context, memory, tool_panel, graph_panel, sub_panel, trace

    @staticmethod
    def _interactions(records: list[dict[str, Any]], empty: str) -> str:
        if not records:
            return _empty(empty)
        return "".join(
            _details(
                " · ".join(
                    str(item.get(key) or "")
                    for key in (
                        "actor_name",
                        "tool_name",
                        "role",
                        "created_at",
                    )
                    if item.get(key)
                ),
                {
                    key: item[key]
                    for key in (
                        "content",
                        "tool_call_id",
                        "run_id",
                        "metadata",
                    )
                    if key in item
                },
            )
            for item in records
        )
