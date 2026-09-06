from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from schemashift.model import (
    MockProvider,
    MockScriptExhausted,
    ModelProvider,
    ModelProviderError,
    OllamaProvider,
)


class Decision(BaseModel):
    action: str
    confidence: float


def test_mock_provider_is_scripted_and_records_calls() -> None:
    provider = MockProvider(chat_responses=["first", "second"])

    assert isinstance(provider, ModelProvider)
    assert provider.chat("hello") == "first"
    assert provider.chat([("human", "again")]) == "second"
    assert [call.kind for call in provider.calls] == ["chat", "chat"]

    with pytest.raises(MockScriptExhausted):
        provider.chat("unexpected")


def test_mock_provider_validates_structured_responses() -> None:
    provider = MockProvider(structured_responses=[{"action": "migrate", "confidence": 0.9}])

    result = provider.structured("choose", Decision)

    assert result == Decision(action="migrate", confidence=0.9)
    assert provider.calls[-1].kind == "structured"


def test_mock_embeddings_are_repeatable_and_fixed_width() -> None:
    first = MockProvider(strict=False)
    second = MockProvider(strict=False)

    vector = first.embed("customer status mapping")

    assert len(vector) == 1024
    assert vector == second.embed("customer status mapping")
    assert vector != first.embed("order status mapping")
    assert sum(value * value for value in vector) == pytest.approx(1.0)


class FakeChatClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.call_count = 0

    def invoke(self, _messages):
        self.call_count += 1
        return SimpleNamespace(content=next(self.responses))


def test_ollama_structured_retries_parse_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(
        chat_model="test-chat",
        embedding_model="test-embed",
        max_attempts=3,
    )
    client = FakeChatClient(["not json", '{"action": "migrate", "confidence": 0.9}'])
    monkeypatch.setattr(provider, "_chat_client", lambda **_kwargs: client)

    result = provider.structured("choose", Decision)

    assert result == Decision(action="migrate", confidence=0.9)
    assert client.call_count == 2


def test_ollama_structured_retries_schema_validation_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OllamaProvider(
        chat_model="test-chat",
        embedding_model="test-embed",
        max_attempts=3,
    )
    client = FakeChatClient(['{"action": "migrate"}', '{"action": "migrate", "confidence": 0.85}'])
    monkeypatch.setattr(provider, "_chat_client", lambda **_kwargs: client)

    result = provider.structured("choose", Decision)

    assert result == Decision(action="migrate", confidence=0.85)
    assert client.call_count == 2


def test_ollama_structured_retry_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(
        chat_model="test-chat",
        embedding_model="test-embed",
        max_attempts=3,
    )
    client = FakeChatClient(["bad", "still bad", "also bad"])
    monkeypatch.setattr(provider, "_chat_client", lambda **_kwargs: client)

    with pytest.raises(ModelProviderError, match="after 3 attempt"):
        provider.structured("choose", Decision)

    assert client.call_count == 3
