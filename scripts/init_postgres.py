"""Initialize SchemaShift's local PostgreSQL schema idempotently."""

from __future__ import annotations

from schemashift.config import get_settings
from schemashift.persistence import PostgresRepository


def main() -> None:
    settings = get_settings()
    repository = PostgresRepository(settings)
    repository.initialize()
    if not repository.health_check():
        raise RuntimeError("PostgreSQL schema initialized but health check failed")
    print("SchemaShift PostgreSQL schema is ready.")


if __name__ == "__main__":
    main()
