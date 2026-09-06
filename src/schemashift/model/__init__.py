"""Model-provider boundary for SchemaShift."""

from .base import Message, MessageInput, ModelProvider, ModelProviderError
from .factory import create_model_provider, get_model_provider
from .mock import MockProvider, MockScriptExhausted, ModelCall
from .ollama import OllamaProvider

__all__ = [
    "Message",
    "MessageInput",
    "MockProvider",
    "MockScriptExhausted",
    "ModelCall",
    "ModelProvider",
    "ModelProviderError",
    "OllamaProvider",
    "create_model_provider",
    "get_model_provider",
]
