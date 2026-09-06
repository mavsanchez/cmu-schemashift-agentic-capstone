"""Per-run persistence instrumentation for model and MCP operations."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, TypeVar
from uuid import uuid4

from schemashift.domain import RunContext
from schemashift.model import MessageInput, ModelProvider
from schemashift.persistence import ActorType, PostgresRepository

StructuredT = TypeVar("StructuredT")
_RUN_CONTEXT: ContextVar[RunContext | None] = ContextVar("schemashift_run_context", default=None)


@contextmanager
def instrument_run(context: RunContext) -> Iterator[None]:
    token: Token[RunContext | None] = _RUN_CONTEXT.set(context)
    try:
        yield
    finally:
        _RUN_CONTEXT.reset(token)


def current_run_context() -> RunContext | None:
    return _RUN_CONTEXT.get()


def _json(value: Any, *, limit: int = 100_000) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    text = json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "..."


class PersistingToolGateway:
    """Persist paired calls/results with one stable ``tool_call_id``."""

    def __init__(self, gateway: Any, repository: PostgresRepository) -> None:
        self.gateway = gateway
        self.repository = repository

    def discover_tools(self, *, refresh: bool = False) -> tuple[str, ...]:
        return self.gateway.discover_tools(refresh=refresh)

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        values = dict(arguments or {})
        call_id = str(uuid4())
        context = current_run_context()
        try:
            result = dict(self.gateway.call_tool(name, values))
        except Exception as exc:
            result = {
                "ok": False,
                "error": f"MCP tool {name} failed: {type(exc).__name__}: {exc}",
                "errors": [str(exc)],
            }
        if context is not None:
            self.repository.add_tool_interaction(
                context,
                tool_name=name,
                tool_call_id=call_id,
                request_content=_json(values),
                result_content=_json(result),
                actor_name="SchemaShift MCP",
                request_metadata={"transport": "mcp-v2"},
                result_metadata={"ok": bool(result.get("ok", False))},
            )
        return result


class PersistingModelProvider:
    """Record bounded model interactions while preserving the provider API."""

    def __init__(self, provider: ModelProvider, repository: PostgresRepository) -> None:
        self.provider = provider
        self.repository = repository

    def _record(
        self,
        *,
        messages: MessageInput,
        result: Any,
        operation: str,
        response_schema: str | None = None,
        error: Exception | None = None,
    ) -> None:
        context = current_run_context()
        if context is None:
            return
        prompt = _json(messages, limit=20_000)
        is_subagent = response_schema in {"MigrationProposal", "BranchExpansion"} or (
            "migration specialist" in prompt.casefold()
        )
        self.repository.add_message(
            context,
            actor_type=ActorType.SUBAGENT if is_subagent else ActorType.AGENT,
            actor_name="Migration Subagent" if is_subagent else "Parent Orchestrator",
            role="model_error" if error is not None else "model_result",
            content=(
                _json(
                    {"error_type": type(error).__name__, "message": str(error)},
                    limit=50_000,
                )
                if error is not None
                else _json(result, limit=50_000)
            ),
            metadata={
                "operation": operation,
                "response_schema": response_schema,
                "prompt_preview": prompt[:2_000],
                "success": error is None,
            },
        )

    def record_subagent_interaction(
        self,
        specialist: str,
        *,
        role: str,
        content: Any,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        context = current_run_context()
        if context is None:
            return
        self.repository.add_message(
            context,
            actor_type=ActorType.SUBAGENT,
            actor_name=specialist,
            role=role,
            content=_json(content, limit=50_000),
            metadata=dict(metadata or {}),
        )

    def chat(
        self,
        messages: MessageInput,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        try:
            result = self.provider.chat(messages, temperature=temperature, max_tokens=max_tokens)
        except Exception as exc:
            self._record(messages=messages, result=None, operation="chat", error=exc)
            raise
        self._record(messages=messages, result=result, operation="chat")
        return result

    def structured(
        self,
        messages: MessageInput,
        response_model: type[StructuredT] | Mapping[str, Any],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> StructuredT | dict[str, Any]:
        schema_name = getattr(response_model, "__name__", "json_schema")
        try:
            result = self.provider.structured(
                messages,
                response_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            self._record(
                messages=messages,
                result=None,
                operation="structured",
                response_schema=str(schema_name),
                error=exc,
            )
            raise
        self._record(
            messages=messages,
            result=result,
            operation="structured",
            response_schema=str(schema_name),
        )
        return result

    def embed(self, text: str) -> list[float]:
        return self.provider.embed(text)

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        return self.provider.embed_many(texts)


__all__ = [
    "PersistingModelProvider",
    "PersistingToolGateway",
    "current_run_context",
    "instrument_run",
]
