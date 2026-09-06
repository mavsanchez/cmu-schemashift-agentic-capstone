"""Command-line entry point for ``python -m schemashift.benchmark``."""

from __future__ import annotations

import argparse
from pathlib import Path

from schemashift.config import Settings

from .cases import build_cases, case_index
from .models import BenchmarkArm, BenchmarkMode
from .runner import BenchmarkRunner, smoke_cases

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=[item.value for item in BenchmarkMode], default="mock")
    parser.add_argument(
        "--arm", choices=["both", *[item.value for item in BenchmarkArm]], default="both"
    )
    parser.add_argument("--smoke", action="store_true", help="Run one case per motif")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument(
        "--no-update-latest",
        action="store_true",
        help="Do not update latest.json/latest.md after a full live comparison",
    )
    args = parser.parse_args()

    cases = build_cases()
    if args.case_id:
        indexed = case_index(cases)
        unknown = sorted(set(args.case_id) - set(indexed))
        if unknown:
            parser.error("unknown case ID(s): " + ", ".join(unknown))
        cases = tuple(indexed[value] for value in args.case_id)
    elif args.smoke:
        cases = smoke_cases(cases)

    arms = list(BenchmarkArm) if args.arm == "both" else [BenchmarkArm(args.arm)]
    runner = BenchmarkRunner(
        args.repository_root,
        settings=Settings(),
        manifest_path=args.manifest,
        output_root=args.output_root,
    )
    output = runner.run(
        cases=cases,
        mode=args.mode,
        arms=arms,
        update_latest=False if args.no_update_latest else None,
    )
    print(output.output_directory)
    for arm, summary in output.report.summaries.items():
        print(
            f"{arm}: equivalence={summary.equivalence_rate:.1%}, "
            f"route_accuracy={summary.expected_route_accuracy:.1%}"
        )


if __name__ == "__main__":
    main()
