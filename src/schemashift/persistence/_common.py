"""Internal repository helpers."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from schemashift.persistence.errors import RecordNotFoundError

RecordT = TypeVar("RecordT", bound=BaseModel)


def as_record(model: type[RecordT], row: dict[str, Any] | None, label: str) -> RecordT:
    if row is None:
        raise RecordNotFoundError(f"{label} was not found")
    return model.model_validate(row)


def page_size(value: int, *, maximum: int = 1_000) -> int:
    if value < 1 or value > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value
