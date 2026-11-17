"""Codex app-server implementation of the same OPSX ABCAdapter tool contract.

The adapter translates tool-call authority at its own boundary. Native Codex
shell/patch paths are denied, and mutating tools remain disabled until proven.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

from pydantic import BaseModel

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.config import ReadScope, resolve_effective_read_scope
from agents.read_scope_turn import build_tool_call_guard

from opsx_cli.codex_app_server import run_guarded_codex_turn
from opsx_cli.codex_provider import DEFAULT_CODEX_MODEL, CodexProviderError
from opsx_cli.codex_tool_gateway import CodexToolDenied, CodexToolGateway, SUPPORTED_TOOLS
from opsx_cli.codex_write_journal import CodexWriteJournal


def _require_linked_worktree(workspace: Path) -> None:
    """Write authority is confined to a separate Git worktree, never the main checkout."""
    proc = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--absolute-git-dir",
         "--git-common-dir", "--show-toplevel"],
        capture_output=True, text=True, check=False, timeout=10, encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise CodexProviderError("CODEX_WRITE_WORKTREE_UNVERIFIED")
    lines = proc.stdout.splitlines()
    if len(lines) != 3:
        raise CodexProviderError("CODEX_WRITE_WORKTREE_UNVERIFIED")
    git_dir = Path(lines[0]).resolve()
    # Git returns --git-common-dir relative to its -C worktree on Linux.
    common_value = Path(lines[1])
    common = (common_value if common_value.is_absolute()
              else workspace / common_value).resolve()
    top = Path(lines[2]).resolve()
    if (top != workspace or git_dir == common
            or not (workspace / ".git").is_file()
            or not git_dir.is_dir() or not common.is_dir()):
        raise CodexProviderError("CODEX_WRITE_REQUIRES_LINKED_WORKTREE")


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
        registered_tools: dict[str, Any] = {}
        if "run_pytest" in tools:
            if agent_key != "PROCESS_SUPERVISOR_FIX":
                raise CodexProviderError("CODEX_PYTEST_AGENT_AUTHORITY_MISMATCH")
            from agents.agent_tools.deterministic.registry import build_deterministic_custom_tools
            specs = build_deterministic_custom_tools(agent_key, repo_root=Path(
                str(state.get("repository_path") or state.get("workspace") or "")
            ).resolve())
            if set(specs) != {"run_opsx_pytest"}:
                raise CodexProviderError("CODEX_PYTEST_TOOL_UNBOUND")
            registered_tools["run_pytest"] = specs["run_opsx_pytest"]
        writable = "edit" in tools
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
        if writable:
            from agents.config import read_scope_enforcement_mode
            if read_scope_enforcement_mode() != "enforce":
                raise CodexProviderError("CODEX_WRITE_SCOPE_ENFORCEMENT_REQUIRED")
            _require_linked_worktree(workspace)
            # No write is attempted until stale pending edits are recovered.
            with CodexWriteJournal(workspace) as journal:
                journal.recover(CodexToolGateway._atomic_replace)
                try:
                    gateway = CodexToolGateway(
                        workspace, tools, guard=tool_call_guard,
                        read_scope=read_scope, journal=journal,
                        registered_tools=registered_tools,
                    )
                except CodexToolDenied as exc:
                    raise CodexProviderError(exc.code) from exc
                outcome = await run_guarded_codex_turn(
                    gateway, prompt=prompt, model=model,
                    reasoning_effort="low", timeout_seconds=self.timeout_seconds,
                )
        else:
            try:
                gateway = CodexToolGateway(
                    workspace, tools, guard=tool_call_guard, read_scope=read_scope,
                    registered_tools=registered_tools,
                )
            except CodexToolDenied as exc:
                raise CodexProviderError(exc.code) from exc
            outcome = await run_guarded_codex_turn(
                gateway, prompt=prompt, model=model,
                reasoning_effort="low", timeout_seconds=self.timeout_seconds,
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
