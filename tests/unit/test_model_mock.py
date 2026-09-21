from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from schemashift.model import (
    LiteLLMProvider,
    MockProvider,
    MockScriptExhausted,
    ModelProvider,
    ModelProviderError,
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


class FakeCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.call_count = 0
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.call_count += 1
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=next(self.responses)))]
        )


class FakeEmbeddings:
    def __init__(self, responses: list[list[list[float]]]) -> None:
        self.responses = iter(responses)
        self.requests: list[dict] = []

    def embed_query(self, text: str):
        self.requests.append({"kind": "query", "input": text})
        return next(self.responses)[0]

    def embed_documents(self, texts: list[str]):
        self.requests.append({"kind": "documents", "input": texts})
        return next(self.responses)


def _client(
    responses: list[str],
    *,
    embeddings: list[list[list[float]]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions(responses)),
        embeddings=FakeEmbeddings(embeddings or []),
    )


def _provider(client: SimpleNamespace, *, max_attempts: int = 3) -> LiteLLMProvider:
    return LiteLLMProvider(
        model="agent",
        embedding_model="bge-m3",
        base_url="http://dgx-ramona:4000/v1",
        api_key="test-key",
        max_attempts=max_attempts,
        embedding_dimensions=3,
        client=client,
        embedding_client=client.embeddings,
    )


def test_litellm_structured_retries_parse_failures() -> None:
    client = _client(["not json", '{"action": "migrate", "confidence": 0.9}'])
    provider = _provider(client)

    result = provider.structured("choose", Decision)

    assert result == Decision(action="migrate", confidence=0.9)
    assert client.chat.completions.call_count == 2
    request = client.chat.completions.requests[-1]
    assert request["model"] == "agent"
    assert request["messages"][0]["role"] == "system"
    assert "Return ONLY one valid JSON object" in request["messages"][0]["content"]
    assert '"confidence"' in request["messages"][0]["content"]
    assert request["messages"][1] == {"role": "user", "content": "choose"}
    assert request["response_format"]["type"] == "json_schema"


def test_litellm_structured_retries_schema_validation_failures() -> None:
    client = _client(
        ['{"action": "migrate"}', '{"action": "migrate", "confidence": 0.85}']
    )
    provider = _provider(client)

    result = provider.structured("choose", Decision)

    assert result == Decision(action="migrate", confidence=0.85)
    assert client.chat.completions.call_count == 2


def test_litellm_structured_retry_is_bounded() -> None:
    client = _client(["bad", "still bad", "also bad"])
    provider = _provider(client)

    with pytest.raises(ModelProviderError, match="after 3 attempt"):
        provider.structured("choose", Decision)

    assert client.chat.completions.call_count == 3


def test_litellm_chat_preserves_system_prompt_and_maps_human_role() -> None:
    client = _client(["answer"])
    provider = _provider(client)

    result = provider.chat([("system", "guardrails"), ("human", "migrate")], max_tokens=64)

    assert result == "answer"
    assert client.chat.completions.requests == [
        {
            "model": "agent",
            "messages": [
                {"role": "system", "content": "guardrails"},
                {"role": "user", "content": "migrate"},
            ],
            "temperature": 0.0,
            "max_tokens": 64,
        }
    ]


def test_litellm_embeddings_use_local_bge_m3_without_remote_embedding_calls() -> None:
    client = _client([], embeddings=[[[1.0, 0.0, 0.0]], [[1, 0, 0], [0, 1, 0]]])
    provider = _provider(client)

    assert provider.embed("schema") == [1.0, 0.0, 0.0]
    assert provider.embed_many(["schema", "migration"]) == [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]
    assert client.embeddings.requests == [
        {"kind": "query", "input": "schema"},
        {"kind": "documents", "input": ["schema", "migration"]},
    ]


def test_litellm_requires_environment_supplied_api_key() -> None:
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        LiteLLMProvider(
            model="agent",
            embedding_model="bge-m3",
            base_url="http://dgx-ramona:4000/v1",
            api_key="",
        )
