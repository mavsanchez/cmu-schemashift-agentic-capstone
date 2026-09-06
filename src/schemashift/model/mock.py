"""Scriptable, deterministic model provider for tests and offline demos."""

from __future__ import annotations

import hashlib
import math
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from .base import (
    MessageInput,
    ModelProviderError,
    normalize_messages,
    validate_structured_payload,
)

StructuredT = TypeVar("StructuredT")


class MockScriptExhausted(ModelProviderError):
    """Raised when strict mode observes an unscripted chat operation."""


@dataclass(frozen=True, slots=True)
class ModelCall:
    kind: Literal["chat", "structured", "embed", "embed_many"]
    input: Any


class MockProvider:
    """A provider whose chat results are queued and embeddings are stable.

    ``strict=True`` makes unexpected model calls fail loudly.  Embeddings are
    always available: absent an explicit vector, SHAKE-256 expands the input to
    a repeatable unit vector, avoiding Python's process-randomized ``hash``.
    """

    def __init__(
        self,
        *,
        chat_responses: Iterable[str] = (),
        structured_responses: Iterable[Any] = (),
        embeddings: Mapping[str, Sequence[float]] | Iterable[Sequence[float]] | None = None,
        embedding_dimensions: int = 1024,
        strict: bool = True,
        default_chat_response: str = "Mock response",
    ) -> None:
        if embedding_dimensions < 1:
            raise ValueError("embedding_dimensions must be positive")
        self.embedding_dimensions = embedding_dimensions
        self.strict = strict
        self.default_chat_response = default_chat_response
        self.calls: list[ModelCall] = []
        self._chat_responses = deque(chat_responses)
        self._structured_responses = deque(structured_responses)
        self._embedding_map: dict[str, list[float]] = {}
        self._embedding_queue: deque[list[float]] = deque()
        if isinstance(embeddings, Mapping):
            self._embedding_map = {
                text: self._validate_embedding(vector) for text, vector in embeddings.items()
            }
        elif embeddings is not None:
            self._embedding_queue = deque(self._validate_embedding(vector) for vector in embeddings)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def chat(
        self,
        messages: MessageInput,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        del temperature, max_tokens
        normalized = normalize_messages(messages)
        self.calls.append(ModelCall("chat", normalized))
        if self._chat_responses:
            return self._chat_responses.popleft()
        if self.strict:
            raise MockScriptExhausted("No scripted chat response remains")
        return self.default_chat_response

    def structured(
        self,
        messages: MessageInput,
        response_model: type[StructuredT] | Mapping[str, Any],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> StructuredT | dict[str, Any]:
        del temperature, max_tokens
        normalized = normalize_messages(messages)
        self.calls.append(ModelCall("structured", normalized))
        if not self._structured_responses:
            if self.strict:
                raise MockScriptExhausted("No scripted structured response remains")
            payload: Any = {}
        else:
            payload = self._structured_responses.popleft()

        if not isinstance(response_model, Mapping) and isinstance(payload, response_model):
            return payload
        return validate_structured_payload(payload, response_model)

    def _validate_embedding(self, vector: Sequence[float]) -> list[float]:
        result = [float(value) for value in vector]
        if len(result) != self.embedding_dimensions:
            raise ValueError(
                f"Mock embedding has {len(result)} values, expected {self.embedding_dimensions}"
            )
        if not all(math.isfinite(value) for value in result):
            raise ValueError("Mock embeddings must contain only finite numbers")
        return result

    def _deterministic_embedding(self, text: str) -> list[float]:
        # Two bytes per dimension gives enough resolution without platform- or
        # process-dependent randomness.
        raw = hashlib.shake_256(text.encode("utf-8")).digest(self.embedding_dimensions * 2)
        values = [
            (int.from_bytes(raw[index : index + 2], "little") / 32767.5) - 1.0
            for index in range(0, len(raw), 2)
        ]
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]

    def embed(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        self.calls.append(ModelCall("embed", text))
        if text in self._embedding_map:
            return list(self._embedding_map[text])
        if self._embedding_queue:
            return list(self._embedding_queue.popleft())
        return self._deterministic_embedding(text)

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        values = list(texts)
        self.calls.append(ModelCall("embed_many", values))
        # Avoid recording one additional ``embed`` call per item.
        result: list[list[float]] = []
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("all texts must be non-empty strings")
            if value in self._embedding_map:
                result.append(list(self._embedding_map[value]))
            elif self._embedding_queue:
                result.append(list(self._embedding_queue.popleft()))
            else:
                result.append(self._deterministic_embedding(value))
        return result
