"""Provider-neutral model interfaces used by SchemaShift graphs.

The graph layer depends on this small synchronous protocol instead of creating
LangChain or Ollama clients directly.  Synchronous methods are intentional:
SchemaShift's graph nodes are ordinary local functions and can be dispatched
by an async caller when needed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

Message = tuple[str, str] | Mapping[str, Any] | Any
MessageInput = str | Mapping[str, Any] | Sequence[Message]
StructuredSchema = type[BaseModel] | Mapping[str, Any]
StructuredT = TypeVar("StructuredT")


class ModelProviderError(RuntimeError):
    """Raised when a model provider cannot complete a requested operation."""


@runtime_checkable
class ModelProvider(Protocol):
    """The model capabilities used by the orchestration and retrieval layers."""

    def chat(
        self,
        messages: MessageInput,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Return a plain-text assistant response."""
        ...

    def structured(
        self,
        messages: MessageInput,
        response_model: type[StructuredT] | Mapping[str, Any],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> StructuredT | dict[str, Any]:
        """Return output validated against a model type or JSON schema."""
        ...

    def embed(self, text: str) -> list[float]:
        """Embed one string."""
        ...

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch while preserving input order."""
        ...


def normalize_messages(messages: MessageInput) -> list[Message]:
    """Normalize convenient message forms without importing LangChain types."""

    if isinstance(messages, str):
        return [("human", messages)]
    if isinstance(messages, Mapping):
        return [messages]
    return list(messages)


def validate_structured_payload(
    payload: Any,
    response_model: type[StructuredT] | Mapping[str, Any],
) -> StructuredT | dict[str, Any]:
    """Turn decoded JSON into the caller's requested response type.

    A mapping denotes a raw JSON Schema, for which a dictionary is returned.
    Pydantic models and simple dataclasses/classes are also supported so graph
    schemas remain independent from the provider implementation.
    """

    if not isinstance(payload, Mapping):
        raise ModelProviderError("Structured model output must decode to a JSON object")

    result = dict(payload)
    if isinstance(response_model, Mapping):
        return result

    validator = getattr(response_model, "model_validate", None)
    if callable(validator):
        return validator(result)

    try:
        return response_model(**result)
    except (TypeError, ValueError) as exc:
        raise ModelProviderError(
            f"Structured model output does not match {response_model!r}"
        ) from exc
