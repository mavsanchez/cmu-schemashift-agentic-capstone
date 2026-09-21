from __future__ import annotations

from schemashift.config import Settings


def test_llm_environment_variables_configure_dgx_litellm(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://dgx-test:4000/v1")
    monkeypatch.setenv("LLM_MODEL", "test-agent")
    monkeypatch.setenv("LLM_API_KEY", "secret-value")
    monkeypatch.setenv("SCHEMASHIFT_EMBEDDING_MODEL", "local-embed")
    monkeypatch.setenv("SCHEMASHIFT_OLLAMA_BASE_URL", "http://local-embed:11434")
    monkeypatch.setenv("SCHEMASHIFT_RUNTIME_PROVIDER", "litellm")

    settings = Settings(_env_file=None)

    assert settings.model_provider == "litellm"
    assert settings.llm_base_url == "http://dgx-test:4000/v1"
    assert settings.llm_model == "test-agent"
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "secret-value"
    assert settings.embedding_model == "local-embed"
    assert settings.ollama_base_url == "http://local-embed:11434"
    assert "secret-value" not in repr(settings)


def test_llm_defaults_target_dgx_chat_and_local_embeddings(monkeypatch) -> None:
    for name in (
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_API_KEY",
        "SCHEMASHIFT_EMBEDDING_MODEL",
        "SCHEMASHIFT_OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)

    assert settings.llm_base_url == "http://dgx-ramona:4000/v1"
    assert settings.llm_model == "agent"
    assert settings.llm_api_key is None
    assert settings.embedding_model == "bge-m3"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
