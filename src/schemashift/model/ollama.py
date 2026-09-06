"""Local Ollama implementation of :class:`~schemashift.model.ModelProvider`."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from .base import (
    MessageInput,
    ModelProviderError,
    normalize_messages,
    validate_structured_payload,
)

StructuredT = TypeVar("StructuredT")


def _keep_alive_seconds(value: str | int) -> int:
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"\s*(\d+)\s*([smh]?)\s*", value)
    if not match:
        return 600
    amount, unit = int(match.group(1)), match.group(2)
    return amount * {"": 1, "s": 1, "m": 60, "h": 3600}[unit]


def _content_text(content: Any) -> str:
    """Normalize both legacy string and LangChain content-block responses."""

    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ModelProviderError("Ollama returned invalid structured JSON") from exc
    if not isinstance(value, dict):
        raise ModelProviderError("Ollama structured output was not a JSON object")
    return value


class OllamaProvider:
    """Chat and embedding provider backed by the local Ollama server.

    LangChain clients are imported and constructed lazily.  Importing this
    module therefore remains safe in deterministic unit tests that do not have
    the optional runtime process available.
    """

    def __init__(
        self,
        *,
        chat_model: str,
        embedding_model: str,
        base_url: str | None = None,
        temperature: float = 0.0,
        keep_alive: str | int = "10m",
        max_attempts: int = 3,
        embedding_dimensions: int = 1024,
    ) -> None:
        if not chat_model.strip() or not embedding_model.strip():
            raise ValueError("chat_model and embedding_model are required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if embedding_dimensions < 1:
            raise ValueError("embedding_dimensions must be positive")

        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.base_url = base_url
        self.temperature = temperature
        self.keep_alive = keep_alive
        self.max_attempts = max_attempts
        self.embedding_dimensions = embedding_dimensions
        self._embeddings_client: Any | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> OllamaProvider:
        """Build from the project's settings object without coupling its type."""

        return cls(
            chat_model=settings.chat_model,
            embedding_model=settings.embedding_model,
            base_url=getattr(settings, "ollama_base_url", None),
            temperature=float(getattr(settings, "temperature", 0.0)),
            keep_alive=getattr(settings, "ollama_keep_alive", "10m"),
            max_attempts=int(getattr(settings, "model_max_attempts", 3)),
            embedding_dimensions=int(getattr(settings, "embedding_dimensions", 1024)),
        )

    def _chat_client(
        self,
        *,
        json_schema: Mapping[str, Any] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> Any:
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:  # pragma: no cover - dependency error is environment-specific
            raise ModelProviderError(
                "OllamaProvider requires the 'langchain-ollama' package"
            ) from exc

        options: dict[str, Any] = {
            "model": self.chat_model,
            "temperature": self.temperature if temperature is None else temperature,
            "keep_alive": self.keep_alive,
        }
        if self.base_url:
            options["base_url"] = self.base_url
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if json_schema is not None:
            # Ollama uses constrained decoding for a supplied JSON Schema; this
            # does not rely on the selected chat model supporting tool calls.
            options["format"] = dict(json_schema)
        return ChatOllama(**options)

    def _attempt(self, operation: str, call: Callable[[], Any]) -> Any:
        last_error: Exception | None = None
        for _ in range(self.max_attempts):
            try:
                return call()
            except Exception as exc:  # provider/network errors vary by client version
                last_error = exc
        raise ModelProviderError(
            f"Ollama {operation} failed after {self.max_attempts} attempt(s)"
        ) from last_error

    def chat(
        self,
        messages: MessageInput,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        client = self._chat_client(
            json_schema=None,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        response = self._attempt("chat", lambda: client.invoke(normalize_messages(messages)))
        return _content_text(getattr(response, "content", response))

    def structured(
        self,
        messages: MessageInput,
        response_model: type[StructuredT] | Mapping[str, Any],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> StructuredT | dict[str, Any]:
        if isinstance(response_model, Mapping):
            schema = dict(response_model)
        elif isinstance(response_model, type) and issubclass(response_model, BaseModel):
            schema = response_model.model_json_schema()
        else:
            schema_builder = getattr(response_model, "model_json_schema", None)
            if not callable(schema_builder):
                raise TypeError("response_model must be a Pydantic model type or JSON schema")
            schema = schema_builder()

        client = self._chat_client(
            json_schema=schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        normalized = normalize_messages(messages)

        def invoke_and_validate() -> StructuredT | dict[str, Any]:
            response = client.invoke(normalized)
            payload = _json_object(_content_text(getattr(response, "content", response)))
            return validate_structured_payload(payload, response_model)

        return self._attempt("structured chat", invoke_and_validate)

    def _embedding_client(self) -> Any:
        if self._embeddings_client is not None:
            return self._embeddings_client
        try:
            from langchain_ollama import OllamaEmbeddings
        except ImportError as exc:  # pragma: no cover - dependency error is environment-specific
            raise ModelProviderError(
                "OllamaProvider requires the 'langchain-ollama' package"
            ) from exc

        options: dict[str, Any] = {
            "model": self.embedding_model,
            "keep_alive": _keep_alive_seconds(self.keep_alive),
        }
        if self.base_url:
            options["base_url"] = self.base_url
        self._embeddings_client = OllamaEmbeddings(**options)
        return self._embeddings_client

    def _validate_embedding(self, embedding: Sequence[float]) -> list[float]:
        result = [float(value) for value in embedding]
        if len(result) != self.embedding_dimensions:
            raise ModelProviderError(
                f"Embedding model returned {len(result)} values; "
                f"Redis indexes require {self.embedding_dimensions}"
            )
        return result

    def embed(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        client = self._embedding_client()
        embedding = self._attempt("embedding", lambda: client.embed_query(text))
        return self._validate_embedding(embedding)

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        values = list(texts)
        if any(not isinstance(text, str) or not text.strip() for text in values):
            raise ValueError("all texts must be non-empty strings")
        if not values:
            return []
        client = self._embedding_client()
        embeddings = self._attempt("embedding batch", lambda: client.embed_documents(values))
        return [self._validate_embedding(embedding) for embedding in embeddings]
