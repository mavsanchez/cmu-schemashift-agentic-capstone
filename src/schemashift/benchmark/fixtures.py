"""Deterministic paired retail databases used by demos and benchmarks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = REPOSITORY_ROOT / "data" / "schemas"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data" / "generated"
SEED = 20260905

CUSTOMERS = [
    (
        1,
        "Alice Adams",
        "Active",
        "West",
        "2024-01-10",
        250_000,
        "premium",
        "alice@example.test",
        True,
        "OPEN",
        None,
    ),
    (
        2,
        "Bob Brown",
        "Inactive",
        "East",
        "2023-05-03",
        100_000,
        "standard",
        "bob@example.test",
        False,
        "CLOSED",
        "2025-01-01",
    ),
    (
        3,
        "Carla Cruz",
        "Pending",
        "West",
        "2024-07-15",
        None,
        None,
        "carla@example.test",
        False,
        "REVIEW",
        None,
    ),
    (
        4,
        "Dan Diaz",
        "Active",
        "North",
        "2022-12-31",
        50_000,
        "standard",
        "dan@example.test",
        True,
        "OPEN",
        None,
    ),
    (5, "Eve Evans", None, "South", "2025-02-28", 0, "basic", None, False, "HOLD", None),
    (
        6,
        "Fran Fox",
        "Active",
        None,
        "2024-02-29",
        175_050,
        None,
        "fran@example.test",
        True,
        "OPEN",
        None,
    ),
]

ORDERS = [
    (101, 1, "2025-01-05", 12_599, "Shipped"),
    (102, 1, "2025-02-10", 2_500, "Pending"),
    (103, 2, "2024-12-01", 9_999, "Cancelled"),
    (104, 3, "2025-03-15", 0, "Pending"),
    (105, 4, "2025-03-15", 100_001, "Shipped"),
    (106, 4, "2025-03-16", 3_333, "Shipped"),
    (107, 5, "2025-04-01", 750, "Pending"),
    (108, 6, "2025-04-02", 42_042, "Shipped"),
    # X has no normalized lookup entry, deliberately exercising unmatched codes.
    (109, 2, "2025-04-03", 1, "Manual"),
]

PRODUCTS = [
    (201, "Widget", "Hardware", 1_999),
    (202, "Support plan", "Service", 10_000),
    (203, "Cable", "Hardware", 500),
    (204, "Free sample", "Promotion", 0),
]


def _registered_source(path: Path, *, source_id: str, role: str) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "source_id": source_id,
        "conversation_id": "00000000-0000-0000-0000-000000000000",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "path": str(path.resolve()),
        "original_name": path.name,
        "role": role,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "status": "confirmed",
    }


def _reset(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    return duckdb.connect(str(path))


def create_old_database(path: Path) -> None:
    with _reset(path) as connection:
        connection.execute((SCHEMAS / "customer_v1.sql").read_text(encoding="utf-8"))
        connection.executemany(
            "INSERT INTO customer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", CUSTOMERS
        )
        connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", ORDERS)
        connection.executemany("INSERT INTO product VALUES (?, ?, ?, ?)", PRODUCTS)


def create_new_database(path: Path) -> None:
    with _reset(path) as connection:
        connection.execute((SCHEMAS / "customer_v2.sql").read_text(encoding="utf-8"))
        status_codes = {"Active": "A", "Inactive": "I", "Pending": "P"}
        connection.executemany(
            "INSERT INTO customers VALUES "
            "(?, ?, ?, CAST(? AS TIMESTAMP), ? / 100.0, ?, CAST(? AS TIMESTAMP))",
            [(c[0], c[1], status_codes.get(c[2]), c[4], c[5], c[9], c[10]) for c in CUSTOMERS],
        )
        connection.executemany(
            "INSERT INTO customer_status VALUES (?, ?, ?)",
            [("A", "Active", True), ("I", "Inactive", False), ("P", "Pending", False)],
        )
        regions = [(10, "West"), (20, "East"), (30, "North"), (40, "South")]
        connection.executemany("INSERT INTO regions VALUES (?, ?)", regions)
        region_ids = {name: identifier for identifier, name in regions}
        connection.executemany(
            "INSERT INTO customer_profile VALUES (?, ?, ?)",
            [
                (c[0], region_ids.get(c[3]), c[6] if c[6] is not None else "UNKNOWN")
                for c in CUSTOMERS
            ],
        )
        connection.executemany(
            "INSERT INTO customer_contacts VALUES (?, ?, ?, ?, ?)",
            [
                (1001, 1, "email", "alice@example.test", True),
                (1002, 2, "email", "bob@example.test", True),
                (1003, 3, "email", "carla@example.test", True),
                (1004, 4, "email", "dan@example.test", True),
                (1005, 4, "email", "dan.secondary@example.test", False),
                (1006, 4, "phone", "+1-555-0104", True),
                (1007, 6, "email", "fran@example.test", True),
            ],
        )
        order_codes = {"Shipped": "S", "Pending": "P", "Cancelled": "C", "Manual": "X"}
        connection.executemany(
            "INSERT INTO order_states VALUES (?, ?)",
            [("S", "Shipped"), ("P", "Pending"), ("C", "Cancelled"), ("R", "Returned")],
        )
        connection.executemany(
            "INSERT INTO sales_orders VALUES (?, ?, CAST(? AS TIMESTAMP), ? / 100.0, ?)",
            [(o[0], o[1], o[2], o[3], order_codes[o[4]]) for o in ORDERS],
        )
        connection.executemany("INSERT INTO catalog_products VALUES (?, ?, ?, ? / 100.0)", PRODUCTS)


def create_demo_data(output_dir: str | Path = DEFAULT_OUTPUT) -> dict[str, object]:
    """Rebuild both databases and an MCP-compatible trusted registry manifest."""

    destination = Path(output_dir).resolve()
    old_path = destination / "databases" / "customer_v1.duckdb"
    new_path = destination / "databases" / "customer_v2.duckdb"
    create_old_database(old_path)
    create_new_database(new_path)
    logical_payload = json.dumps(
        {"customers": CUSTOMERS, "orders": ORDERS, "products": PRODUCTS},
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    manifest: dict[str, object] = {
        "format_version": 1,
        "seed": SEED,
        "logical_sha256": hashlib.sha256(logical_payload).hexdigest(),
        "fixture_features": [
            "nulls",
            "one_to_many_contact_matches",
            "duplicate_lookup_matches",
            "unmatched_lookup_code",
            "boundary_timestamps",
            "empty_lookup_group",
            "repeated_projection_rows",
            "numeric_conversion_edges",
        ],
        "old_database_id": "customer_v1",
        "old_database_path": str(old_path),
        "new_database_id": "customer_v2",
        "new_database_path": str(new_path),
        "databases": [
            {
                "database_id": "customer_v1",
                "path": str(old_path),
                "role": "old_data",
                "metadata": {"schema_version": "customer_v1", "seed": SEED},
            },
            {
                "database_id": "customer_v2",
                "path": str(new_path),
                "role": "new_data",
                "metadata": {"schema_version": "customer_v2", "seed": SEED},
            },
        ],
        "sources": [
            _registered_source(
                SCHEMAS / "customer_v1.sql",
                source_id="benchmark-schema-customer-v1",
                role="old_schema",
            ),
            _registered_source(
                SCHEMAS / "customer_v2.sql",
                source_id="benchmark-schema-customer-v2",
                role="new_schema",
            ),
            *[
                _registered_source(
                    path,
                    source_id=f"benchmark-knowledge-{path.stem}",
                    role="migration_knowledge",
                )
                for path in sorted((REPOSITORY_ROOT / "data" / "knowledge").glob("*"))
                if path.suffix.lower() in {".md", ".json"}
            ],
        ],
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "demo_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


__all__ = [
    "CUSTOMERS",
    "DEFAULT_OUTPUT",
    "ORDERS",
    "PRODUCTS",
    "SEED",
    "create_demo_data",
    "create_new_database",
    "create_old_database",
]
