"""Materialize SchemaShift's canonical 50-case benchmark fixtures."""

from __future__ import annotations

import argparse
from pathlib import Path

from schemashift.benchmark.cases import materialize_cases

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    manifest = materialize_cases(args.root)
    print(f"Wrote 50 deterministic cases to {manifest}")


if __name__ == "__main__":
    main()
