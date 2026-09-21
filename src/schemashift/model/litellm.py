"""OpenAI-compatible model provider for the DGX LiteLLM gateway."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, SecretStr

from .base import (
    MessageInput,
    ModelProviderError,
    normalize_messages,
    validate_structured_payload,
)

StructuredT = TypeVar("StructuredT")
_ROLE_ALIASES = {"ai": "assistant", "human": "user"}
_MESSAGE_FIELDS = ("content", "name", "tool_call_id", "tool_calls")


def _keep_alive_seconds(value: str | int) -> int:
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"\s*(\d+)\s*([smh]?)\s*", value)
    if not match:
        return 600
    amount, unit = int(match.group(1)), match.group(2)
    return amount * {"": 1, "s": 1, "m": 60, "h": 3600}[unit]


def _content_text(content: Any) -> str:
    """Normalize OpenAI-compatible string and content-block responses."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (bytes, bytearray)):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(block, "text", None)
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
        raise ModelProviderError("LiteLLM returned invalid structured JSON") from exc
    if not isinstance(value, dict):
        raise ModelProviderError("LiteLLM structured output was not a JSON object")
    return value


def _message_role(value: Any) -> str:
    role = str(value or "user").lower()
    return _ROLE_ALIASES.get(role, role)


def _openai_messages(messages: MessageInput) -> list[dict[str, Any]]:
    """Convert provider-neutral message forms into OpenAI chat messages."""

    converted: list[dict[str, Any]] = []
    for message in normalize_messages(messages):
        if isinstance(message, tuple) and len(message) == 2:
            converted.append({"role": _message_role(message[0]), "content": message[1]})
            continue
        if isinstance(message, Mapping):
            item = {
                key: message[key]
                for key in _MESSAGE_FIELDS
                if key in message and message[key] is not None
            }
            item["role"] = _message_role(message.get("role", message.get("type")))
            item.setdefault("content", "")
            converted.append(item)
            continue

        role = _message_role(getattr(message, "role", getattr(message, "type", None)))
        item = {"role": role, "content": getattr(message, "content", str(message))}
        for key in _MESSAGE_FIELDS[1:]:
            value = getattr(message, key, None)
            if value is not None:
                item[key] = value
        converted.append(item)
    return converted


def _schema_for(response_model: type[Any] | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(response_model, Mapping):
        return dict(response_model)
    if isinstance(response_model, type) and issubclass(response_model, BaseModel):
        return response_model.model_json_schema()
    schema_builder = getattr(response_model, "model_json_schema", None)
    if not callable(schema_builder):
        raise TypeError("response_model must be a Pydantic model type or JSON schema")
    return dict(schema_builder())


def _schema_name(response_model: type[Any] | Mapping[str, Any], schema: Mapping[str, Any]) -> str:
    raw_name = getattr(response_model, "__name__", None) or schema.get("title") or "response"
    name = re.sub(r"[^A-Za-z0-9_-]", "_", str(raw_name))[:64]
    return name or "response"


def _structured_messages(
    messages: MessageInput,
    schema: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Add an explicit schema contract for gateways that ignore response_format."""

    converted = _openai_messages(messages)
    contract = (
        "Return ONLY one valid JSON object matching this exact JSON Schema. "
        "Do not include markdown fences, commentary, or any text outside the JSON object.\n"
        f"JSON Schema:\n{json.dumps(schema, sort_keys=True, separators=(',', ':'))}"
    )
    for index, message in enumerate(converted):
        if message.get("role") != "system":
            continue
        updated = dict(message)
        existing = _content_text(updated.get("content")).rstrip()
        updated["content"] = f"{existing}\n\n{contract}" if existing else contract
        converted[index] = updated
        break
    else:
        converted.insert(0, {"role": "system", "content": contract})
    return converted


def _secret_value(value: Any) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value or "")


class LiteLLMProvider:
    """DGX LiteLLM chat with local Ollama embeddings and no local chat fallback."""

    def __init__(
        self,
        *,
        model: str,
        embedding_model: str,
        base_url: str,
        api_key: str,
        embedding_base_url: str = "http://127.0.0.1:11434",
        embedding_keep_alive: str | int = "10m",
        temperature: float = 0.0,
        max_attempts: int = 3,
        embedding_dimensions: int = 1024,
        client: Any | None = None,
        embedding_client: Any | None = None,
    ) -> None:
        if not model.strip() or not embedding_model.strip():
            raise ValueError("model and embedding_model are required")
        if not base_url.strip():
            raise ValueError("LLM_BASE_URL is required")
        if not api_key.strip():
            raise ValueError("LLM_API_KEY is required")
        if not embedding_base_url.strip():
            raise ValueError("SCHEMASHIFT_OLLAMA_BASE_URL is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if embedding_dimensions < 1:
            raise ValueError("embedding_dimensions must be positive")

        self.model = model
        self.embedding_model = embedding_model
        self.base_url = base_url.rstrip("/")
        self.embedding_base_url = embedding_base_url.rstrip("/")
        self.embedding_keep_alive = embedding_keep_alive
        self.temperature = temperature
        self.max_attempts = max_attempts
        self.embedding_dimensions = embedding_dimensions
        self._api_key = api_key
        self._client = client
        self._embedding_client = embedding_client

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        max_attempts: int | None = None,
    ) -> LiteLLMProvider:
        """Build a provider from the centralized application settings."""

        return cls(
            model=settings.llm_model,
            embedding_model=settings.embedding_model,
            base_url=settings.llm_base_url,
            api_key=_secret_value(getattr(settings, "llm_api_key", None)),
            embedding_base_url=settings.ollama_base_url,
            embedding_keep_alive=settings.ollama_keep_alive,
            temperature=float(getattr(settings, "temperature", 0.0)),
            max_attempts=(
                max_attempts
                if max_attempts is not None
                else int(getattr(settings, "model_max_attempts", 3))
            ),
            embedding_dimensions=int(getattr(settings, "embedding_dimensions", 1024)),
        )

    def _openai(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - installation-specific
                raise ModelProviderError(
                    "LiteLLMProvider requires the 'openai' package"
                ) from exc
            self._client = OpenAI(base_url=self.base_url, api_key=self._api_key)
        return self._client

    def _attempt(
        self,
        operation: str,
        call: Callable[[], Any],
        *,
        endpoint: str | None = None,
    ) -> Any:
        last_error: Exception | None = None
        for _ in range(self.max_attempts):
            try:
                return call()
            except Exception as exc:  # SDK, proxy, and validation errors vary
                last_error = exc
        raise ModelProviderError(
            f"{operation} at {endpoint or self.base_url} failed after "
            f"{self.max_attempts} attempt(s)"
        ) from last_error

    @staticmethod
    def _completion_text(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            raise ModelProviderError("LiteLLM returned no completion choices")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        text = _content_text(content).strip()
        if not text:
            raise ModelProviderError("LiteLLM returned no visible completion content")
        return text

    def _chat_completion(
        self,
        messages: MessageInput,
        *,
        temperature: float | None,
        max_tokens: int | None,
        response_format: Mapping[str, Any] | None = None,
    ) -> Any:
        options: dict[str, Any] = {
            "model": self.model,
            "messages": _openai_messages(messages),
            "temperature": self.temperature if temperature is None else temperature,
        }
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        if response_format is not None:
            options["response_format"] = dict(response_format)
        return self._openai().chat.completions.create(**options)

    def chat(
        self,
        messages: MessageInput,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        return self._attempt(
            "LiteLLM chat",
            lambda: self._completion_text(
                self._chat_completion(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            ),
        )

    def structured(
        self,
        messages: MessageInput,
        response_model: type[StructuredT] | Mapping[str, Any],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> StructuredT | dict[str, Any]:
        schema = _schema_for(response_model)
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": _schema_name(response_model, schema),
                "schema": schema,
            },
        }

        def invoke_and_validate() -> StructuredT | dict[str, Any]:
            response = self._chat_completion(
                _structured_messages(messages, schema),
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            payload = _json_object(self._completion_text(response))
            return validate_structured_payload(payload, response_model)

        return self._attempt("LiteLLM structured chat", invoke_and_validate)

    def _local_embeddings(self) -> Any:
        if self._embedding_client is None:
            try:
                from langchain_ollama import OllamaEmbeddings
            except ImportError as exc:  # pragma: no cover - installation-specific
                raise ModelProviderError(
                    "Local bge-m3 embeddings require the 'langchain-ollama' package"
                ) from exc
            self._embedding_client = OllamaEmbeddings(
                model=self.embedding_model,
                base_url=self.embedding_base_url,
                keep_alive=_keep_alive_seconds(self.embedding_keep_alive),
            )
        return self._embedding_client

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

        def request() -> list[float]:
            embedding = self._local_embeddings().embed_query(text)
            return self._validate_embedding(embedding)

        return self._attempt(
            "Local Ollama embedding",
            request,
            endpoint=self.embedding_base_url,
        )

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        values = list(texts)
        if any(not isinstance(text, str) or not text.strip() for text in values):
            raise ValueError("all texts must be non-empty strings")
        if not values:
            return []

        def request() -> list[list[float]]:
            embeddings = self._local_embeddings().embed_documents(values)
            if len(embeddings) != len(values):
                raise ModelProviderError("Ollama returned the wrong embedding batch length")
            return [self._validate_embedding(embedding) for embedding in embeddings]

        return self._attempt(
            "Local Ollama embedding batch",
            request,
            endpoint=self.embedding_base_url,
        )
