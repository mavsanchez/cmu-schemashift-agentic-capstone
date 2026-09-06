"""Create deterministic local DuckDB databases for the SchemaShift demo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from schemashift.benchmark.fixtures import DEFAULT_OUTPUT, create_demo_data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(create_demo_data(args.output_dir), indent=2))


if __name__ == "__main__":
    main()
