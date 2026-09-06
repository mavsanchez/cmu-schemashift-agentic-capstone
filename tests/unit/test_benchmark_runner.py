from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from schemashift.benchmark import BenchmarkArm, BenchmarkRunner, build_cases, smoke_cases
from schemashift.benchmark.fixtures import create_demo_data
from schemashift.benchmark.reporting import render_markdown
from schemashift.benchmark.runner import _consume_service_envelopes, _terminal_status
from schemashift.config import Settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mock_benchmark(tmp_path_factory: pytest.TempPathFactory):
    temporary = tmp_path_factory.mktemp("mock-benchmark")
    generated = temporary / "generated"
    create_demo_data(generated)
    runner = BenchmarkRunner(
        ROOT,
        settings=Settings(model_provider="mock"),
        manifest_path=generated / "demo_manifest.json",
        output_root=temporary / "results",
    )
    return runner.run(mode="mock", update_latest=False)


def test_mock_benchmark_runs_all_50_cases_in_both_arms(mock_benchmark) -> None:
    report = mock_benchmark.report

    assert report.case_count == 50
    assert report.arms == [BenchmarkArm.BASELINE, BenchmarkArm.WORKFLOW]
    assert len(report.results) == 100
    assert all(not result.error for result in report.results)
    assert all(result.parse_success for result in report.results)
    assert all(result.schema_valid for result in report.results)
    assert all(result.execution_success for result in report.results)
    assert all(result.equivalent for result in report.results)


def test_scripted_workflow_has_expected_routing_retrieval_and_recovery(
    mock_benchmark,
) -> None:
    cases = {case.case_id: case for case in build_cases()}
    workflow = [
        result for result in mock_benchmark.report.results if result.arm is BenchmarkArm.WORKFLOW
    ]

    assert len(workflow) == 50
    assert all(result.candidate_sql == cases[result.case_id].gold_sql for result in workflow)
    assert all(result.expected_route_matched for result in workflow)
    assert all(result.retrieval_recall == 1.0 for result in workflow)
    assert sum(result.intervention for result in workflow) == 5
    assert {result.case_id for result in workflow if result.intervention} == {
        f"m10_{form.value}" for form in cases["m10_projection"].sql_form.__class__
    }
    assert {result.case_id for result in workflow if result.recovery} == {
        "m03_aggregate",
        "m08_join",
    }
    assert all(result.tool_calls > 0 for result in workflow)
    assert all(result.migration_specialist_invocations >= 1 for result in workflow)
    assert all(result.validation_specialist_invocations >= 1 for result in workflow)
    reviews = [result for result in workflow if result.intervention]
    assert all(result.route == "human_review" for result in reviews)
    assert all(result.verdict == "pass" for result in reviews)
    assert all(result.terminal_status == "human_approved" for result in reviews)
    assert all(result.embedding_calls == 0 for result in workflow)


def test_prompt_only_baseline_has_no_agent_tools_or_specialists(mock_benchmark) -> None:
    baseline = [
        result for result in mock_benchmark.report.results if result.arm is BenchmarkArm.BASELINE
    ]

    assert len(baseline) == 50
    assert all(result.tool_calls == 0 for result in baseline)
    assert all(result.model_calls == 1 for result in baseline)
    assert all(result.migration_specialist_invocations == 0 for result in baseline)
    assert all(result.validation_specialist_invocations == 0 for result in baseline)
    assert sum(result.expected_route_matched for result in baseline) == 45


def test_report_writes_timestamped_jsonl_csv_json_and_markdown(mock_benchmark) -> None:
    output = mock_benchmark.output_directory
    expected = {"results.jsonl", "results.csv", "report.json", "report.md"}

    assert {path.name for path in output.iterdir()} == expected
    jsonl = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    with (output / "results.csv").open(newline="", encoding="utf-8") as handle:
        csv_rows = list(csv.DictReader(handle))
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))

    assert len(jsonl) == len(csv_rows) == len(report["results"]) == 100
    assert "Workflow minus baseline" in (output / "report.md").read_text(encoding="utf-8")
    assert not (output.parent / "latest.json").exists()
    assert not (output.parent / "latest.md").exists()


def test_mock_results_are_deterministic_apart_from_time_measurements(
    mock_benchmark, tmp_path: Path
) -> None:
    generated = tmp_path / "generated"
    create_demo_data(generated)
    runner = BenchmarkRunner(
        ROOT,
        settings=Settings(model_provider="mock"),
        manifest_path=generated / "demo_manifest.json",
        output_root=tmp_path / "results",
    )
    first = runner.run(cases=smoke_cases(), mode="mock", update_latest=False)
    second = runner.run(cases=smoke_cases(), mode="mock", update_latest=False)

    def stable_results(output):
        return [
            result.model_dump(mode="json", exclude={"latency_ms"})
            for result in output.report.results
        ]

    assert stable_results(first) == stable_results(second)
    assert mock_benchmark.report.summaries["workflow"].equivalence_rate == 1.0
    assert mock_benchmark.report.summaries["workflow"].expected_route_accuracy == 1.0
    assert mock_benchmark.report.summaries["workflow"].retrieval_recall == 1.0


def test_smoke_slice_is_one_case_from_every_motif() -> None:
    selected = smoke_cases()

    assert len(selected) == 10
    assert {case.motif_id for case in selected} == set(range(1, 11))
    assert {case.sql_form.value for case in selected} == {"projection"}


def test_latest_rejects_non_live_or_partial_result(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    create_demo_data(generated)
    runner = BenchmarkRunner(
        ROOT,
        settings=Settings(model_provider="mock"),
        manifest_path=generated / "demo_manifest.json",
        output_root=tmp_path / "results",
    )

    with pytest.raises(ValueError, match="full 50-case live comparison"):
        runner.run(cases=smoke_cases(), mode="mock", update_latest=True)


def test_latest_rejects_duplicate_reordered_or_tampered_50_case_sets(
    tmp_path: Path,
) -> None:
    runner = BenchmarkRunner(ROOT, output_root=tmp_path / "results")
    canonical = list(build_cases())
    duplicate = [canonical[0], *canonical[:-1]]
    tampered = [
        canonical[0].model_copy(update={"prompt": "tampered"}),
        *canonical[1:],
    ]

    for invalid in (duplicate, list(reversed(canonical)), tampered):
        with pytest.raises(ValueError, match="50-case live"):
            runner.run(cases=invalid, mode="live", update_latest=True)


def test_explicit_empty_case_selection_is_rejected(tmp_path: Path) -> None:
    runner = BenchmarkRunner(ROOT, output_root=tmp_path / "results")

    with pytest.raises(ValueError, match="at least one benchmark case"):
        runner.run(cases=[], mode="mock", update_latest=False)


def test_markdown_never_relabels_differences_as_validated(mock_benchmark) -> None:
    markdown = render_markdown(mock_benchmark.report)

    assert "human approval never changes" in markdown


def test_live_service_consumer_ignores_only_the_resumed_review_replay() -> None:
    run_id = uuid4()
    decided_review_id = uuid4()
    next_review_id = uuid4()

    replay_only = [
        SimpleNamespace(
            event={
                "type": "human_review_required",
                "run_id": str(run_id),
                "review_id": str(decided_review_id),
            }
        )
    ]
    observed_run, pending, saw_new_review = _consume_service_envelopes(
        replay_only,
        ignored_review_id=decided_review_id,
    )

    assert observed_run == run_id
    assert pending is None
    assert not saw_new_review

    replay_then_new = [
        *replay_only,
        SimpleNamespace(
            event={
                "type": "human_review_required",
                "run_id": str(run_id),
                "review_id": str(next_review_id),
            }
        ),
    ]
    _, pending, saw_new_review = _consume_service_envelopes(
        replay_then_new,
        ignored_review_id=decided_review_id,
    )

    assert pending == next_review_id
    assert saw_new_review


def test_benchmark_error_overrides_stale_running_checkpoint_status() -> None:
    assert _terminal_status({"status": "running"}, "ModelProviderError: exhausted") == "failed"
    assert _terminal_status({"status": "human_approved"}, "") == "human_approved"
