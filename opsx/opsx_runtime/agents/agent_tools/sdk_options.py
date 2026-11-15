"""SDK options для child ImageAsset hop."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents.adapters.agent_capability_options import AgentCustomToolSpec
from agents.adapters.agent_capability_options import custom_tool_specs_to_provider


def build_parent_hop_sdk_options_with_agent_tools(
    *,
    base_options: dict[str, Any],
    extra_custom_tools: Mapping[str, AgentCustomToolSpec],
) -> dict[str, Any]:
    """Merge agent tools into уже собранные scoped-read options (post provider map)."""
    from agents.adapters.agent_capability_options import custom_tool_specs_to_provider

    out = dict(base_options)
    local = dict(out.get("local") or {})
    existing = dict(local.get("custom_tools") or {})
    merged = custom_tool_specs_to_provider(dict(extra_custom_tools))
    existing.update(merged)
    local["custom_tools"] = existing
    out["local"] = local
    return out


def build_image_child_sdk_options(
    *,
    api_key: str,
    model: Mapping[str, Any],
    local: Mapping[str, Any],
    custom_tools: Mapping[str, AgentCustomToolSpec],
) -> dict[str, Any]:
    """MCP-only child hop."""
    local_payload = dict(local)
    local_payload["setting_sources"] = []
    local_payload["custom_tools"] = custom_tool_specs_to_provider(dict(custom_tools))
    return {
        "apiKey": api_key,
        "model": dict(model),
        "local": local_payload,
        "tools": ["mcp"],
        "mcpServers": {},
    }
