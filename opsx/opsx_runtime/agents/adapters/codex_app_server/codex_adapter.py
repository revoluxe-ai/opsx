"""Codex app-server implementation of the same OPSX ABCAdapter tool contract.

The adapter translates tool-call authority at its own boundary. Native Codex
shell/patch paths are denied, and mutating tools remain disabled until proven.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.config import ReadScope, resolve_effective_read_scope
from agents.read_scope_turn import build_tool_call_guard

from opsx_cli.codex_app_server import run_guarded_codex_turn
from opsx_cli.codex_provider import DEFAULT_CODEX_MODEL, CodexProviderError
from opsx_cli.codex_tool_gateway import CodexToolDenied, CodexToolGateway, SUPPORTED_TOOLS


class CodexAppServerAdapter(ABCAdapter):
    """Provider-native tool proxy with OPSX callback invoked before file I/O."""

    def __init__(self, *, timeout_seconds: float = 300, **unused: Any) -> None:
        if not 1 <= timeout_seconds <= 3600:
            raise ValueError("invalid Codex timeout")
        self.timeout_seconds = int(timeout_seconds)
        self.last_result: AgentResult | None = None

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: type[BaseModel] | None = None,
        resume_agent_id: str | None = None,
        tool_call_guard: Any | None = None,
        read_scope: ReadScope | None = None,
        agent_key: str | None = None,
    ) -> AgentResult:
        if resume_agent_id:
            raise CodexProviderError("CODEX_RESUME_NOT_PROVEN")
        if len(set(tools)) != len(tools) or set(tools) - SUPPORTED_TOOLS:
            raise CodexProviderError("CODEX_TOOL_AUTHORITY_UNPROVEN")
        if "edit" in tools:
            # Guarded edit exists in host gateway but durable crash recovery and
            # Windows OS isolation must be independently accepted first.
            raise CodexProviderError("CODEX_WRITE_ISOLATION_NOT_PROVEN")
        # Never silently substitute a hard-selected Cursor model/tool tier.
        declared = state.get("model")
        if declared is not None:
            if not isinstance(declared, Mapping) or declared.get("id") != DEFAULT_CODEX_MODEL:
                raise CodexProviderError("CODEX_MODEL_AUTHORITY_MISMATCH")
        model = DEFAULT_CODEX_MODEL
        workspace = Path(str(state.get("repository_path") or state.get("workspace") or "")).resolve()
        if not workspace.is_dir():
            raise CodexProviderError("CODEX_WORKSPACE_MISSING")
        if read_scope is None:
            read_scope = resolve_effective_read_scope(agent_key, state)
        if tool_call_guard is None:
            tool_call_guard = build_tool_call_guard(read_scope, workspace, agent_key=agent_key)
        try:
            gateway = CodexToolGateway(
                workspace, tools, guard=tool_call_guard, read_scope=read_scope,
            )
        except CodexToolDenied as exc:
            raise CodexProviderError(exc.code) from exc
        outcome = await run_guarded_codex_turn(
            gateway,
            prompt=prompt,
            model=model,
            reasoning_effort="low",
            timeout_seconds=self.timeout_seconds,
        )
        result = AgentResult(
            text=outcome.text,
            status="COMPLETED",
            agent_id=outcome.thread_id,
            run_id=outcome.turn_id,
            usage=(
                {str(k): v for k, v in outcome.usage.items() if isinstance(v, int)}
                if outcome.usage else None
            ),
            usage_source="codex_app_server" if outcome.usage else "unavailable",
        )
        self.last_result = result
        return result
