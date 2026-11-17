"""Non-bypassable tool authority for supervisor-created repair agents.

Supervisor may repair registered tool implementations within its META authority,
but MUST NOT give agents generic command or shell execution.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SUPERVISOR_FIX_AGENT_KEY = "PROCESS_SUPERVISOR_FIX"

SUPERVISOR_FIX_TOOLS: tuple[str, ...] = (
    "read", "grep", "glob", "ls", "edit", "run_pytest",
)
_SUPERVISOR_ALLOWED = frozenset(SUPERVISOR_FIX_TOOLS)
# Only generated adapter bridge for the pre-registered run_pytest, never
# a caller-supplied generic MCP capability.
_ALLOWED_PROVIDER_BUILTINS = frozenset({"read", "grep", "glob", "ls", "edit", "mcp"})


class SupervisorAgentToolPolicyError(ValueError):
    """Supervisor must PAUSE rather than expand tool authority."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def assert_supervisor_agent_tools(
    requested: Sequence[str],
    *,
    capability_manifest: Any | None = None,
) -> tuple[str, ...]:
    """Fail closed before agent creation, even with a new provider adapter."""
    if capability_manifest is not None:
        raise SupervisorAgentToolPolicyError("SUPERVISOR_CAPABILITY_MANIFEST_FORBIDDEN")
    if isinstance(requested, (str, bytes)) or not isinstance(requested, Sequence):
        raise SupervisorAgentToolPolicyError("SUPERVISOR_TOOL_LIST_INVALID")
    tools = tuple(requested)
    if not all(isinstance(tool, str) and tool == tool.strip() for tool in tools):
        raise SupervisorAgentToolPolicyError("SUPERVISOR_TOOL_LIST_INVALID")
    if len(set(tools)) != len(tools):
        raise SupervisorAgentToolPolicyError("SUPERVISOR_DUPLICATE_TOOL")
    if set(tools) - _SUPERVISOR_ALLOWED:
        raise SupervisorAgentToolPolicyError("SUPERVISOR_UNREGISTERED_TOOL_FORBIDDEN")
    return tools


def assert_supervisor_provider_options(
    options: Mapping[str, Any],
    *,
    requested: Sequence[str],
) -> None:
    """Second gate: reject shell/native command executor from SDK options."""
    requested = assert_supervisor_agent_tools(requested)
    disabled = options.get("disallowed_tools")
    if not isinstance(disabled, (list, tuple, set, frozenset)):
        raise SupervisorAgentToolPolicyError("SUPERVISOR_NATIVE_SHELL_NOT_DISABLED")
    if "shell" not in disabled:
        raise SupervisorAgentToolPolicyError("SUPERVISOR_NATIVE_SHELL_NOT_DISABLED")
    builtins = options.get("tools")
    if not isinstance(builtins, (list, tuple)) or any(
        not isinstance(tool, str) for tool in builtins
    ):
        raise SupervisorAgentToolPolicyError("SUPERVISOR_PROVIDER_TOOLS_INVALID")
    if len(set(builtins)) != len(builtins):
        raise SupervisorAgentToolPolicyError("SUPERVISOR_DUPLICATE_PROVIDER_TOOL")
    if set(builtins) - _ALLOWED_PROVIDER_BUILTINS:
        raise SupervisorAgentToolPolicyError("SUPERVISOR_PROVIDER_TOOL_ESCALATION")
    if "mcp" in builtins and "run_pytest" not in requested:
        raise SupervisorAgentToolPolicyError("SUPERVISOR_UNAUTHORIZED_MCP")
    if "run_pytest" in requested and "mcp" not in builtins:
        raise SupervisorAgentToolPolicyError("SUPERVISOR_REGISTERED_TOOL_NOT_CONNECTED")
    local = options.get("local")
    if not isinstance(local, Mapping):
        raise SupervisorAgentToolPolicyError("SUPERVISOR_LOCAL_TOOLS_INVALID")
    custom = local.get("custom_tools", {})
    if not isinstance(custom, Mapping):
        raise SupervisorAgentToolPolicyError("SUPERVISOR_LOCAL_TOOLS_INVALID")
    required_custom = {"run_opsx_pytest"} if "mcp" in builtins else set()
    if set(custom) != required_custom:
        raise SupervisorAgentToolPolicyError("SUPERVISOR_UNREGISTERED_CUSTOM_TOOL")
    mcp_servers = local.get("mcp_servers", {})
    if mcp_servers:
        raise SupervisorAgentToolPolicyError("SUPERVISOR_EXTERNAL_MCP_FORBIDDEN")
