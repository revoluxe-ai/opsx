"""Neutral agent capability option payloads (schemas + tool wiring)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from agents.capabilities.manifest import AgentCapabilityManifest

_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query", "domains"],
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "domains": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "string",
                "enum": [
                    "product",
                    "tests",
                    "migrations",
                    "docs",
                    "changes",
                    "specs",
                    "decisions",
                    "prior_designs",
                ],
            },
        },
    },
}

_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_id"],
    "properties": {
        "source_id": {"type": "string", "minLength": 1},
        "cursor": {"type": "integer", "minimum": 0},
        "expected_revision": {"type": "string"},
    },
}

_SUBMIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["content", "base_digest"],
    "properties": {
        "content": {"type": "string"},
        "base_digest": {"type": "string"},
    },
}


@dataclass(frozen=True)
class AgentCustomToolSpec:
    """Provider-neutral custom tool descriptor for adapter option builders."""

    description: str
    input_schema: Mapping[str, Any]
    execute: Callable[..., Any]


def build_provider_agent_options(
    manifest: AgentCapabilityManifest,
    *,
    api_key: str,
    model: Mapping[str, Any],
    local: Mapping[str, Any],
    tool_runtime: Any | None = None,
) -> dict[str, Any]:
    """Build create/resume options dict (provider package wraps ``custom_tools``)."""
    from agents.implementation.apply_design.tool_runtime import ApplyDesignToolRuntime

    runtime = tool_runtime if tool_runtime is not None else ApplyDesignToolRuntime(manifest)
    custom_tools = {
        "search_repository": AgentCustomToolSpec(
            description="Search allowed repository domains by query. No path argument.",
            input_schema=_SEARCH_SCHEMA,
            execute=runtime.search_repository,
        ),
        "read_source": AgentCustomToolSpec(
            description="Read one file by source_id from this hop manifest.",
            input_schema=_READ_SCHEMA,
            execute=runtime.read_source,
        ),
        "submit_apply_design": AgentCustomToolSpec(
            description="Submit APPLY-DESIGN JSON for the current change only.",
            input_schema=_SUBMIT_SCHEMA,
            execute=runtime.submit_apply_design,
        ),
    }
    local_payload = dict(local)
    local_payload["setting_sources"] = []
    local_payload["custom_tools"] = custom_tools
    return {
        "apiKey": api_key,
        "model": dict(model),
        "local": local_payload,
        "tools": ["mcp"],
        "mcpServers": {},
    }


def custom_tool_specs_to_provider(specs):
    """Delegate optional wire conversion to the explicitly registered adapter."""
    from agents.adapters.provider_registry import call_bootstrap_hook
    try:
        return call_bootstrap_hook("custom_tool_specs_to_provider", specs=specs)
    except RuntimeError as error:
        if "not registered" in str(error):
            return dict(specs)
        raise
