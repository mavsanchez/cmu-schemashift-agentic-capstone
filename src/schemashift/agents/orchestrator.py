"""Dependency container and structured decisions for the parent graph."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemashift.config import Settings
from schemashift.mcp.context import ToolContext
from schemashift.model import ModelProvider


class ImpactAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    impacted_tables: list[str] = Field(default_factory=list)
    impacted_columns: list[str] = Field(default_factory=list)
    expression_changes: list[str] = Field(default_factory=list)
    summary: str = ""
    requires_human_review: bool = False


@dataclass(slots=True)
class OrchestratorRuntime:
    settings: Settings
    provider: ModelProvider
    tool_context: ToolContext
    tool_client: Any | None = None
    memory_store: Any | None = None
    knowledge_store: Any | None = None
    redis_degraded: bool = False
    redis_detail: str = ""
    artifact_writer: Callable[[Mapping[str, Any]], str] | None = None

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Call the discovered MCP client, with a direct adapter for unit tests."""

        if self.tool_client is not None:
            result = self.tool_client.call_tool(name, dict(arguments))
            if hasattr(result, "structured_content") and result.structured_content is not None:
                return dict(result.structured_content)
            if isinstance(result, Mapping):
                return dict(result)
            if isinstance(result, str):
                try:
                    decoded = json.loads(result)
                except json.JSONDecodeError:
                    return {"ok": False, "error": result}
                return dict(decoded) if isinstance(decoded, Mapping) else {"value": decoded}
            content = getattr(result, "content", None)
            if content:
                text = "\n".join(str(getattr(item, "text", "")) for item in content)
                try:
                    decoded = json.loads(text)
                    return dict(decoded) if isinstance(decoded, Mapping) else {"value": decoded}
                except json.JSONDecodeError:
                    return {"ok": not bool(getattr(result, "is_error", False)), "text": text}

        from schemashift.mcp.tools import (
            compare_results,
            execute_readonly_sql,
            inspect_schema,
            list_uploaded_sources,
            parse_sql,
            read_source,
        )

        direct = {
            "parse_sql": lambda values: parse_sql(**values),
            "inspect_schema": lambda values: inspect_schema(**values, context=self.tool_context),
            "execute_readonly_sql": lambda values: execute_readonly_sql(
                **values, context=self.tool_context
            ),
            "compare_results": lambda values: compare_results(**values, context=self.tool_context),
            "list_uploaded_sources": lambda values: list_uploaded_sources(
                **values, context=self.tool_context
            ),
            "read_source": lambda values: read_source(**values, context=self.tool_context),
        }
        if name not in direct:
            raise ValueError(f"Unknown SchemaShift tool: {name}")
        return dict(direct[name](dict(arguments)))


def default_artifact_writer(root: Path) -> Callable[[Mapping[str, Any]], str]:
    """Return an idempotent, run-scoped SQL artifact writer."""

    resolved_root = root.resolve()

    def write(payload: Mapping[str, Any]) -> str:
        conversation_id = str(payload["conversation_id"])
        run_id = str(payload["run_id"])
        stem = str(payload.get("source_stem") or "migration")
        safe_stem = "".join(char if char.isalnum() or char in "-_" else "_" for char in stem)
        directory = (resolved_root / conversation_id / run_id).resolve()
        if resolved_root not in directory.parents:
            raise ValueError("Artifact path escaped the configured root")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{safe_stem}.migrated.sql"
        temporary = directory / f".{safe_stem}.migrated.sql.tmp"
        temporary.write_text(str(payload["candidate_sql"]).rstrip() + "\n", encoding="utf-8")
        temporary.replace(target)
        return str(target)

    return write
