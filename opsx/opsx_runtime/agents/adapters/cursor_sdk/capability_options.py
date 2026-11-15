"""Provider wiring: wrap neutral capability specs as provider CustomTool instances."""

from __future__ import annotations

from typing import Any, Mapping

from cursor_sdk.types import CustomTool

from agents.adapters.agent_capability_options import (
    AgentCustomToolSpec,
    build_provider_agent_options as build_neutral_agent_options,
)
from agents.capabilities.manifest import AgentCapabilityManifest


def custom_tool_specs_to_provider(
    specs: Mapping[str, AgentCustomToolSpec],
) -> dict[str, CustomTool]:
    """Neutral ``AgentCustomToolSpec`` → provider ``CustomTool`` map."""
    out: dict[str, CustomTool] = {}
    for name, spec in specs.items():
        if not isinstance(spec, AgentCustomToolSpec):
            raise TypeError(f"expected AgentCustomToolSpec, got {type(spec)!r}")
        out[name] = CustomTool(
            description=spec.description,
            input_schema=dict(spec.input_schema),
            execute=spec.execute,
        )
    return out


def build_provider_agent_options(
    manifest: AgentCapabilityManifest,
    *,
    api_key: str,
    model: Mapping[str, Any],
    local: Mapping[str, Any],
    tool_runtime: Any | None = None,
) -> dict[str, Any]:
    """Neutral payload → provider ``CustomTool`` map for create/resume."""
    neutral = build_neutral_agent_options(
        manifest,
        api_key=api_key,
        model=model,
        local=local,
        tool_runtime=tool_runtime,
    )
    raw_tools = neutral["local"]["custom_tools"]
    provider_tools = custom_tool_specs_to_provider(raw_tools)
    out = dict(neutral)
    local_payload = dict(out["local"])
    local_payload["custom_tools"] = provider_tools
    out["local"] = local_payload
    return out
