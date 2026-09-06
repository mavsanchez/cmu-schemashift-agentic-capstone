"""LangGraph checkpoint setup with an explicitly reported degraded mode."""

from __future__ import annotations

import warnings
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal

from .redis_client import create_redis_client


class CheckpointerUnavailableError(RuntimeError):
    """Raised when Redis checkpointing is required but cannot be initialized."""


@dataclass(slots=True)
class CheckpointerSetup:
    """The saver plus enough state for the UI/runtime to report durability."""

    saver: Any
    backend: Literal["redis", "in_memory_degraded"]
    degraded: bool
    detail: str
    client: Any | None = None

    def close(self) -> None:
        close = getattr(self.saver, "close", None)
        if callable(close):
            close()
        if self.client is not None:
            client_close = getattr(self.client, "close", None)
            if callable(client_close):
                client_close()


def _redis_saver(client: Any, redis_url: str, checkpoint_prefix: str) -> Any:
    try:
        from langgraph.checkpoint.redis import RedisSaver
    except ImportError as exc:  # pragma: no cover - dependency installation issue
        raise CheckpointerUnavailableError(
            "Redis checkpointing requires 'langgraph-checkpoint-redis'"
        ) from exc

    # Current releases accept ``redis_client``.  The compatibility branch keeps
    # the factory usable with older 0.5.x constructor spelling without leaking
    # model/graph code into version checks.
    normalized_prefix = checkpoint_prefix.rstrip(":")
    try:
        return RedisSaver(
            redis_client=client,
            checkpoint_prefix=normalized_prefix,
            checkpoint_write_prefix=f"{normalized_prefix}:write",
        )
    except TypeError:
        try:
            return RedisSaver(redis_url=redis_url)
        except TypeError as exc:
            raise CheckpointerUnavailableError("Unsupported RedisSaver constructor") from exc


def setup_checkpointer(
    redis_url: str,
    *,
    checkpoint_prefix: str = "schemashift:checkpoint",
    allow_degraded: bool = True,
) -> CheckpointerSetup:
    """Initialize RedisSaver, or visibly fall back to MemorySaver.

    The fallback is for local continuity only and is never represented as
    durable persistence: callers receive ``degraded=True`` and a runtime warning
    suitable for an activity event.
    """

    client: Any | None = None
    try:
        client = create_redis_client(redis_url, verify=True)
        saver = _redis_saver(client, redis_url, checkpoint_prefix)
        saver.setup()
        return CheckpointerSetup(
            saver=saver,
            backend="redis",
            degraded=False,
            detail="Redis checkpoint persistence is active",
            client=client,
        )
    except Exception as exc:
        if client is not None:
            with suppress(Exception):
                client.close()
        if not allow_degraded:
            if isinstance(exc, CheckpointerUnavailableError):
                raise
            raise CheckpointerUnavailableError("Redis checkpoint setup failed") from exc

        try:
            from langgraph.checkpoint.memory import InMemorySaver
        except ImportError as import_exc:  # pragma: no cover - dependency installation issue
            raise CheckpointerUnavailableError(
                "Neither RedisSaver nor the LangGraph in-memory fallback is available"
            ) from import_exc
        detail = (
            "DEGRADED MODE: Redis checkpointing is unavailable; using process-local "
            f"InMemorySaver ({type(exc).__name__}: {exc})"
        )
        warnings.warn(detail, RuntimeWarning, stacklevel=2)
        return CheckpointerSetup(
            saver=InMemorySaver(),
            backend="in_memory_degraded",
            degraded=True,
            detail=detail,
        )


def setup_checkpointer_from_settings(
    settings: Any,
    *,
    allow_degraded: bool = True,
) -> CheckpointerSetup:
    return setup_checkpointer(
        settings.redis_url,
        checkpoint_prefix=getattr(settings, "redis_checkpoint_prefix", "schemashift:checkpoint"),
        allow_degraded=allow_degraded,
    )


# A discoverable factory alias for integration code using either naming style.
create_checkpointer = setup_checkpointer
