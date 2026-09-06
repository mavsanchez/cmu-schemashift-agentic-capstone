from __future__ import annotations

import re

import pytest

from schemashift.ui.app import _apply_stream_event, _review_updates
from schemashift.ui.decisions import render_decision
from schemashift.ui.pipeline import (
    COMPONENT_IDS,
    apply_component_event,
    empty_pipeline,
    render_pipeline,
)
from schemashift.ui.styles import CSS

EXPECTED_COMPONENTS = (
    "mcp",
    "memory",
    "vector",
    "agent",
    "subagent",
    "model",
    "guardrail",
)


def _transition(component: str, status: str) -> dict[str, str]:
    return {
        "type": "component_status",
        "component": component,
        "status": status,
    }


def test_pipeline_renders_exactly_seven_real_dom_and_data_ids() -> None:
    rendered = render_pipeline()

    assert COMPONENT_IDS == EXPECTED_COMPONENTS
    assert len(re.findall(r"class='ss-stage idle'", rendered)) == 7
    for component in EXPECTED_COMPONENTS:
        assert f"id='{component}' data-component='{component}'" in rendered
        assert "aria-label='" in rendered


def test_pipeline_reducer_supports_repeated_component_spans_without_mutation() -> None:
    original = empty_pipeline()
    first = apply_component_event(original, _transition("mcp", "active"))
    second = apply_component_event(first, _transition("mcp", "done"))
    third = apply_component_event(second, _transition("mcp", "active"))
    fourth = apply_component_event(third, _transition("subagent", "active"))
    final = apply_component_event(fourth, _transition("subagent", "done"))

    assert original == {component: "idle" for component in EXPECTED_COMPONENTS}
    assert first["mcp"] == "active"
    assert second["mcp"] == "done"
    assert third["mcp"] == "active"
    assert final["mcp"] == "active"
    assert final["subagent"] == "done"


@pytest.mark.parametrize(
    ("component", "status"),
    [("filesystem", "active"), ("mcp", "running"), ("", "done")],
)
def test_pipeline_reducer_rejects_unknown_components_and_statuses(
    component: str,
    status: str,
) -> None:
    with pytest.raises(ValueError, match="Invalid pipeline transition"):
        apply_component_event(empty_pipeline(), _transition(component, status))


def test_pipeline_markup_and_css_define_exact_status_classes_and_colors() -> None:
    statuses = empty_pipeline()
    for component, status in zip(
        EXPECTED_COMPONENTS,
        ("idle", "active", "done", "error", "active", "done", "idle"),
        strict=True,
    ):
        statuses = apply_component_event(statuses, _transition(component, status))
    rendered = render_pipeline(statuses)

    assert "id='mcp' data-component='mcp' class='ss-stage idle'" in rendered
    assert "id='memory' data-component='memory' class='ss-stage active'" in rendered
    assert "id='vector' data-component='vector' class='ss-stage done'" in rendered
    assert "id='agent' data-component='agent' class='ss-stage error'" in rendered
    assert "--ss-idle:#9c968f" in CSS
    assert "--ss-active:#9ed48f" in CSS
    assert "--ss-done:#477db3" in CSS
    assert "--ss-error:#a44a3f" in CSS
    for selector in (".ss-stage.active", ".ss-stage.done", ".ss-stage.error"):
        assert selector in CSS


def test_decision_panel_shows_candidate_alternatives_and_validation_evidence() -> None:
    review = {
        "candidate_id": "candidate-17",
        "title": "Review <ambiguous> mapping",
        "reason": "Two mappings conflict & need a decision.",
        "risk": "high",
        "payload": {
            "candidate_sql": "SELECT '<unsafe-looking>' AS value",
            "alternatives": [
                {
                    "candidate_id": "candidate-18",
                    "candidate_sql": "SELECT value FROM target",
                    "confidence": 0.74,
                }
            ],
            "validation": {
                "verdict": "human_review",
                "issues": [{"code": "conflicting_evidence"}],
            },
            "approvable": True,
        },
    }

    rendered = render_decision(review)

    assert "High risk" in rendered
    assert "Candidate candidate-17" in rendered
    assert "Alternatives considered" in rendered
    assert "Independent validation evidence" in rendered
    assert "human_review" in rendered
    assert "conflicting_evidence" in rendered
    assert "SELECT &#x27;&lt;unsafe-looking&gt;&#x27; AS value" in rendered
    assert "<ambiguous>" not in rendered
    assert "Review &lt;ambiguous&gt; mapping" in rendered


def test_review_controls_stay_hidden_until_interrupt_and_obey_approvability() -> None:
    approve, reject = _review_updates({"review": None, "busy": False})
    assert approve["visible"] is False
    assert approve["interactive"] is False
    assert reject["visible"] is False
    assert reject["interactive"] is False


def test_human_review_stream_event_releases_busy_state_for_durable_resume() -> None:
    review_event = {
        "type": "human_review_required",
        "review_id": "8e16b94b-39dc-4916-b76b-4a35bd9ce96a",
        "candidate_id": "1a073c74-157a-48e2-a330-7a3521a0bbd9",
        "title": "Resolve an ambiguous mapping",
        "reason": "Two documented mappings remain plausible.",
        "risk": "medium",
        "payload": {
            "candidate_sql": "SELECT customer_id FROM customers",
            "approvable": True,
        },
    }
    state = {
        "busy": True,
        "review": None,
        "statuses": empty_pipeline(),
        "events": [],
    }

    _apply_stream_event(state, [], type("Envelope", (), {"event": review_event})())
    approve, reject = _review_updates(state)

    assert state["busy"] is False
    assert state["review"] == review_event
    assert state["events"] == [review_event]
    assert approve["visible"] is True
    assert approve["interactive"] is True
    assert reject["visible"] is True
    assert reject["interactive"] is True

    review = {"payload": {"approvable": False}}
    approve, reject = _review_updates({"review": review, "busy": False})
    assert approve == {"visible": True, "interactive": False, "__type__": "update"}
    assert reject == {"visible": True, "interactive": True, "__type__": "update"}

    review = {"payload": {"approvable": True}}
    approve, reject = _review_updates({"review": review, "busy": True})
    assert approve["visible"] is True
    assert approve["interactive"] is False
    assert reject["visible"] is True
    assert reject["interactive"] is False


def test_replayed_interrupt_remains_busy_and_disables_second_decision() -> None:
    review_id = "8e16b94b-39dc-4916-b76b-4a35bd9ce96a"
    review_event = {
        "type": "human_review_required",
        "review_id": review_id,
        "candidate_id": "1a073c74-157a-48e2-a330-7a3521a0bbd9",
        "payload": {"approvable": True},
    }
    state = {
        "busy": True,
        "resuming_review_id": review_id,
        "review": review_event,
        "statuses": empty_pipeline(),
        "events": [],
    }

    _apply_stream_event(state, [], type("Envelope", (), {"event": review_event})())
    approve, reject = _review_updates(state)

    assert state["busy"] is True
    assert state["resuming_review_id"] == review_id
    assert approve["interactive"] is False
    assert reject["interactive"] is False


def test_css_has_responsive_focus_and_reduced_motion_contracts() -> None:
    assert "button:focus-visible" in CSS
    assert "outline:3px solid #477db3" in CSS
    assert "@media(max-width:1180px)" in CSS
    assert "grid-template-columns:repeat(4,1fr)" in CSS
    assert "@media(max-width:940px)" in CSS
    assert "grid-template-columns:repeat(2,1fr)" in CSS
    assert "@media(max-width:560px)" in CSS
    assert "grid-template-columns:1fr" in CSS
    assert "@media(prefers-reduced-motion:reduce)" in CSS
    assert ".ss-stage.active:after{animation:none!important}" in CSS
