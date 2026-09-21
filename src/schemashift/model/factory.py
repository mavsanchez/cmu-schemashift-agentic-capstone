"""Central model-provider construction from validated application settings."""

from __future__ import annotations

from typing import Any

from .base import ModelProvider
from .litellm import LiteLLMProvider
from .mock import MockProvider


def create_model_provider(settings: Any, *, mock: MockProvider | None = None) -> ModelProvider:
    provider = str(getattr(settings, "model_provider", getattr(settings, "provider", "litellm")))
    if provider == "litellm":
        return LiteLLMProvider.from_settings(settings)
    if provider == "mock":
        return mock or MockProvider(
            embedding_dimensions=int(getattr(settings, "embedding_dimensions", 1024)),
            strict=False,
        )
    raise ValueError(f"Unsupported model provider: {provider!r}")


get_model_provider = create_model_provider
