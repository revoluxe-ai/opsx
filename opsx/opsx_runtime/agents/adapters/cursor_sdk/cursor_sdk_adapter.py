"""Local Cursor SDK transport for a single graph node.

Reports SDK tokens and server-provided costs; never estimates missing values.
Do not use agent.get_usage(run_id=run.id): local SDK uses usage UUIDs instead.
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel

from cursor_sdk.asyncio import AsyncAgent, AsyncClient

from run_dialogs import persist_run_dialog_artifacts
from opsx_recording.adapter_hooks import adapter_inbox_capture_scope

from .cursor_bridge_runtime import CursorBridgeRuntime
from agents.adapters.adapter_transport import adapter_transport_state as sdk_transport_state
from ..abc_adapter import (
    ABCAdapter, AgentResult, AgentRunContractError, TERMINAL_SDK_STATUSES,
)
from agents.budget.guard import BudgetExceeded
from .cursor_sdk_stream import (
    _conversation_terminal_assistant_text,
    useful_progress_stall_seconds_for_tools,
    wait_cursor_run_with_live_log,
)
from agents.verify.impl_verify.scoped_read_tool import (
    build_impl_verify_scoped_read_sdk_options,
    impl_verify_uses_scoped_read_tool,
)
from agents.abc_agent import (
    ABCAgent,
    is_cursor_bridge_stream_drop,
    is_cursor_provider_access_denied,
    is_cursor_resume_target_gone,
    raise_cursor_infra_failure,
    raise_cursor_provider_access_denied,
    log_cursor_bridge_stream_drop,
    log_cursor_bridge_resume_impossible,
)

REPO_ROOT = project_root()
EXECUTOR_ROOT = resource_path(REPO_ROOT, "tools", "cursor_executor")

if str(EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTOR_ROOT))

from cursor_executor.auth import resolve_auth  # noqa: E402
from cursor_executor.bridge_local import (  # noqa: E402
    agent_local_options,
    bridge_launch_kwargs,
    cursor_sdk_builtin_tools_only_extras,
)
from workflows.repo_paths import normalize_workspace_path  # noqa: E402

_LOGGER = logging.getLogger(__name__)


async def _recover_empty_terminal_text(
    client: Any,
    *,
    run_id: str,
    agent_id: str,
    status: str,
) -> str | None:
    """One bounded, read-only GetRun for the SAME completed run.

    Never re-send the prompt, resume an unknown execution or import answer
    text from other turns/conversations. A mismatched/nonterminal GetRun
    receipt cannot authorize an answer.
    """
    if status.upper() not in TERMINAL_SDK_STATUSES or not run_id:
        return None
    try:
        fetched = await asyncio.wait_for(client.get_run(run_id), timeout=8.0)
    except Exception as exc:
        _LOGGER.warning(
            "cursor_empty_terminal_getrun run_id=%s recovered=false exception=%s",
            run_id, type(exc).__name__,
        )
        return None
    fetched_id = str(getattr(fetched, "id", "") or "")
    fetched_agent_id = str(getattr(fetched, "agent_id", "") or "")
    fetched_status = getattr(fetched, "status", "")
    fetched_status = str(getattr(fetched_status, "value", fetched_status) or "").upper()
    if (
        fetched_id != run_id
        or (fetched_agent_id and fetched_agent_id != agent_id)
        or fetched_status not in TERMINAL_SDK_STATUSES
    ):
        # A contradictory provider receipt must not be bypassed by a later
        # conversation fallback. Stop instead of accepting potentially stale
        # assistant text from an uncertain execution.
        _LOGGER.error(
            "cursor_empty_terminal_getrun code=SDK_RESULT_IDENTITY_CONFLICT "
            "run_id=%s fetched_run_id=%s agent_id=%s fetched_agent_id=%s "
            "fetched_status=%s",
            run_id, fetched_id, agent_id, fetched_agent_id, fetched_status,
        )
        raise AgentRunContractError(
            "SDK_RESULT_IDENTITY_CONFLICT",
            status=fetched_status,
            run_id=run_id,
            agent_id=agent_id,
            attempt=1,
            message="GetRun conflicts with terminal wait receipt; recovery forbidden",
        )
    text = getattr(fetched, "result", None)
    if not isinstance(text, str) or not text.strip():
        _LOGGER.warning(
            "cursor_empty_terminal_getrun run_id=%s recovered=false "
            "reason=server_text_empty",
            run_id,
        )
        return None
    _LOGGER.info(
        "cursor_empty_terminal_getrun run_id=%s recovered=true source=get_run",
        run_id,
    )
    return text


async def _recover_empty_terminal_conversation(
    run: Any,
    *,
    run_id: str,
    agent_id: str,
    status: str,
    payload_model: type[BaseModel],
) -> str | None:
    """Read the single-turn conversation attached to the SAME terminal run.

    The run-scoped SDK API is the provenance anchor, not a filesystem search or
    a previous agent response. Multi-turn/session histories are intentionally
    rejected: their final JSON may belong to a different execution.
    Only a final assistant message that validates against the expected Pydantic
    schema may be recovered. Partial progress, thinking and tool output never
    authorize success.
    """
    if (
        status.upper() not in TERMINAL_SDK_STATUSES
        or not run_id
        or not agent_id
        or str(getattr(run, "id", "") or "") != run_id
        or str(getattr(run, "agent_id", "") or "") != agent_id
    ):
        return None
    get_conversation = getattr(run, "conversation_json", None)
    if not callable(get_conversation):
        return None
    try:
        raw = await asyncio.wait_for(get_conversation(), timeout=8.0)
        if not isinstance(raw, str) or not raw.strip():
            return None
        turns = json.loads(raw)
        if not isinstance(turns, list) or len(turns) != 1:
            _LOGGER.warning(
                "cursor_empty_terminal_conversation run_id=%s recovered=false "
                "reason=not_single_run_turn",
                run_id,
            )
            return None
        valid_answer = _conversation_terminal_assistant_text(
            turns, payload_model=payload_model,
        )
    except Exception as exc:
        _LOGGER.warning(
            "cursor_empty_terminal_conversation run_id=%s recovered=false "
            "exception=%s",
            run_id, type(exc).__name__,
        )
        return None
    if not valid_answer:
        _LOGGER.warning(
            "cursor_empty_terminal_conversation run_id=%s recovered=false "
            "reason=missing_or_invalid_final_assistant_json",
            run_id,
        )
        return None
    _LOGGER.info(
        "cursor_empty_terminal_conversation run_id=%s agent_id=%s "
        "recovered=true source=run.conversation_json",
        run_id, agent_id,
    )
    return valid_answer


REVIEW_TOOLS = frozenset({"read", "grep", "glob", "ls", "shell"})


def _token_counts(usage: Any) -> dict[str, int | None] | None:
    if usage is None:
        return None
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "total_tokens": usage.total_tokens,
    }


class CursorSDKAdapter(ABCAdapter):
    """New-agent/reused-agent run; caller owns state and artifacts."""

    def __init__(
        self, *, timeout_seconds: float = 120, allow_git_shell: bool = False,
        bridge_runtime: CursorBridgeRuntime | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.allow_git_shell = allow_git_shell
        self.bridge_runtime = bridge_runtime
        # Legacy single-run diagnostics. Not authoritative for concurrent runs.
        self.last_result: AgentResult | None = None

    @asynccontextmanager
    async def _connection(self, workspace: Path):
        if self.bridge_runtime is not None:
            async with self.bridge_runtime.connection(workspace) as client:
                yield client
        else:
            async with await AsyncClient.launch_bridge(
                **bridge_launch_kwargs(agent_cwd=str(workspace))
            ) as client:
                yield client

    async def resume_existing_run(
        self,
        client: AsyncClient,
        *,
        agent_id: str,
        run_id: str,
        options: Mapping[str, Any],
    ) -> tuple[Any, Any]:
        """SDK resume существующего agent и reattach к run через ``GetRun``.

        Используется после обрыва bridge-stream: без повторного ``send(prompt)``.

        Returns:
            Пара ``(AsyncAgent, AsyncRun)`` для billing и ``wait_cursor_run_with_live_log``.
        """
        agent = await AsyncAgent.resume(str(agent_id), dict(options), client=client)
        run = await client.get_run(str(run_id))
        return agent, run

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: type[BaseModel] | None = None,
        resume_agent_id: str | None = None,
        tool_call_guard: Any | None = None,
        read_scope: Any | None = None,
        agent_key: str | None = None,
    ) -> AgentResult:
        workspace = normalize_workspace_path(
            state.get("workspace"),
            state.get("repository_path"),
        )
        model = state["model"]
        previous_agent_id = resume_agent_id

        if not workspace.is_dir():
            raise ValueError(f"Workspace not found: {workspace}")
        if not isinstance(model, Mapping) or not model.get("id"):
            raise ValueError("model must be a Cursor SDK model selection")
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        permitted_tools = REVIEW_TOOLS if self.allow_git_shell else REVIEW_TOOLS - {"shell"}
        if set(tools) - permitted_tools:
            raise ValueError("Bugbot adapter only allows read/grep/glob/ls/shell (shell opt-in)")
        if len(tools) != len(set(tools)):
            raise ValueError("Duplicate tool names")

        auth = resolve_auth()
        if not auth.api_key:
            raise RuntimeError("CURSOR_API_KEY is missing")
        # Local Windows Cursor SDK does not support sandbox_options.enabled.
        # Dedicated write tools are disabled; the review prompt is read-only.
        local = (
            self.bridge_runtime.agent_local_options(agent_cwd=str(workspace))
            if self.bridge_runtime is not None
            else agent_local_options(agent_cwd=str(workspace))
        )
        allowed = list(tools)
        scoped_prompt = prompt
        scoped_read_hop = impl_verify_uses_scoped_read_tool(agent_key, tools)

        max_stream_resumes = ABCAgent.cursor_stream_resume_max_attempts
        agent_name = str(state.get("stream_label") or "cursor-sdk")
        session_agent_id = (
            str(previous_agent_id).strip() if previous_agent_id else None
        ) or None
        active_run_id: str | None = None
        prompt_sent = False
        terminal_answer_text: str | None = None

        stream_log = state.get("stream_log")
        stream_label = str(state.get("stream_label") or "cursor-sdk")
        async with adapter_inbox_capture_scope(
            state,
            stream_label=stream_label,
            stream_log=stream_log,
            resume_agent_id=resume_agent_id,
        ) as capture_session:
            for stream_resume_index in range(max_stream_resumes + 1):
                client = None
                try:
                    async with self._connection(workspace) as client:
                        repo_root = Path(
                            str(
                                state.get("repository_path")
                                or state.get("workspace")
                                or workspace,
                            ),
                        ).resolve()
                        if scoped_read_hop:
                            if read_scope is None:
                                from agents.config import resolve_effective_read_scope

                                read_scope = resolve_effective_read_scope(
                                    "impl_verify",
                                    state,
                                )
                            options = build_impl_verify_scoped_read_sdk_options(
                                api_key=auth.api_key,
                                model=dict(model),
                                local=local,
                                scope=read_scope,
                                repo_root=repo_root,
                            )
                            send_kw: dict[str, Any] | None = None
                        else:
                            options = {
                                "apiKey": auth.api_key,
                                "model": dict(model),
                                "local": local,
                                "tools": allowed,
                                **cursor_sdk_builtin_tools_only_extras(),
                            }
                            send_kw = {"tools": allowed}
                        if not prompt_sent:
                            if self.bridge_runtime is not None:
                                await self.bridge_runtime.throttle_sdk_start()
                            if session_agent_id:
                                agent = await AsyncAgent.resume(
                                    session_agent_id, options, client=client
                                )
                            else:
                                agent = await AsyncAgent.create(options, client=client)
                                session_agent_id = str(agent.agent_id)
                            if send_kw is not None:
                                run = await agent.send(scoped_prompt, send_kw)
                            else:
                                run = await agent.send(scoped_prompt)
                            active_run_id = str(run.id)
                            prompt_sent = True
                        else:
                            if not session_agent_id or not active_run_id:
                                raise RuntimeError(
                                    "cursor stream resume requires agent_id and run_id",
                                )
                            agent, run = await self.resume_existing_run(
                                client,
                                agent_id=session_agent_id,
                                run_id=active_run_id,
                                options=options,
                            )

                        stream_log = state.get("stream_log")
                        stream_label = str(state.get("stream_label") or "cursor-sdk")
                        repo_root = Path(
                            str(
                                state.get("repository_path")
                                or state.get("workspace")
                                or workspace,
                            ),
                        ).resolve()
                        intra_guard = state.get("intra_run_budget_guard")
                        try:
                            result = await wait_cursor_run_with_live_log(
                                run,
                                timeout_seconds=self.timeout_seconds,
                                label=stream_label,
                                stream_log=stream_log,
                                emit_stderr=True,
                                payload_model=payload_model,
                                tool_call_guard=tool_call_guard,
                                repo_root=repo_root,
                                intra_run_budget_guard=intra_guard,
                                useful_progress_stall_seconds=useful_progress_stall_seconds_for_tools(
                                    allowed,
                                ),
                            )
                        except BudgetExceeded as exc:
                            raise AgentRunContractError(
                                "RESOURCE_LIMIT",
                                status="cancelled",
                                run_id=str(active_run_id or ""),
                                agent_id=str(session_agent_id or ""),
                                attempt=stream_resume_index + 1,
                                message=str(exc),
                            ) from exc
                        terminal_answer_text = result.result or ""
                        if payload_model is not None and not terminal_answer_text.strip():
                            sdk_status = getattr(result.status, "value", result.status)
                            recovered = await _recover_empty_terminal_text(
                                client,
                                run_id=str(result.id or active_run_id or ""),
                                agent_id=str(result.agent_id or session_agent_id or ""),
                                status=str(sdk_status),
                            )
                            if recovered is None:
                                recovered = await _recover_empty_terminal_conversation(
                                    run,
                                    run_id=str(result.id or active_run_id or ""),
                                    agent_id=str(result.agent_id or session_agent_id or ""),
                                    status=str(sdk_status),
                                    payload_model=payload_model,
                                )
                            if recovered is not None:
                                terminal_answer_text = recovered
                except BaseException as exc:
                    if is_cursor_provider_access_denied(exc):
                        raise_cursor_provider_access_denied(state=state, exc=exc)
                    if is_cursor_bridge_stream_drop(exc):
                        log_cursor_bridge_stream_drop(
                            agent_name=agent_name,
                            run_id=active_run_id,
                            agent_id=session_agent_id,
                            exc=exc,
                            resume_attempt=stream_resume_index + 1,
                            max_resumes=max_stream_resumes,
                        )
                        if self.bridge_runtime is not None and client is not None:
                            await self.bridge_runtime.invalidate(client)
                        if stream_resume_index >= max_stream_resumes:
                            raise_cursor_infra_failure(exc, state=state)
                        continue
                    if prompt_sent and is_cursor_resume_target_gone(exc):
                        log_cursor_bridge_resume_impossible(
                            agent_name=agent_name,
                            run_id=active_run_id,
                            agent_id=session_agent_id,
                            exc=exc,
                            resume_attempt=stream_resume_index + 1,
                            max_resumes=max_stream_resumes,
                        )
                        # The original SDK run may have finished while bridge
                        # connectivity was lost. Recreating an agent and
                        # resending this prompt would violate run idempotency.
                        # No evidence for a terminal status => fail closed.
                        raise AgentRunContractError(
                            "SDK_RUN_RECONCILIATION_REQUIRED",
                            status="UNKNOWN",
                            run_id=active_run_id or "",
                            agent_id=session_agent_id or "",
                            attempt=stream_resume_index + 1,
                            message="Original SDK run is unavailable; fresh prompt replay forbidden",
                        ) from exc
                    raise

                run_usage = run.usage or result.usage
                usage_source = "run.usage" if run_usage is not None else "unavailable"
                cost = None
                billing_error = None
                # Cursor billing is eventually consistent; poll with a short bound.
                # A resumed multi-turn agent has cumulative costs, not per-run costs:
                # never mislabel cumulative billing as the charge for this run.
                for delay in (0.0, 1.0, 3.0):
                    if delay:
                        await asyncio.sleep(delay)
                    try:
                        billed = await asyncio.wait_for(agent.get_usage(), timeout=8)
                        if run_usage is None and not previous_agent_id:
                            run_usage = billed.usage
                            usage_source = "agent.get_usage (new agent)"
                        billed_cost = billed.cost
                        if billed_cost is None and not previous_agent_id:
                            if len(billed.runs) == 1:
                                billed_cost = billed.runs[0].cost
                        if billed_cost is not None and not previous_agent_id:
                            cost = {
                                "raw_cost_cents": billed_cost.raw_cost_cents,
                                "charged_cents": billed_cost.charged_cents,
                            }
                            break
                    except Exception as exc:
                        # Do not fabricate zero tokens/cost if billing is unavailable.
                        billing_error = f"{type(exc).__name__}: {exc}"
                        if "feature_unavailable" in str(exc):
                            break
                        continue

                sdk_status = getattr(result.status, "value", result.status)
                response = AgentResult(
                    text=terminal_answer_text if terminal_answer_text is not None else (result.result or ""),
                    status=str(sdk_status),
                    agent_id=str(result.agent_id or agent.agent_id),
                    run_id=str(result.id),
                    usage=_token_counts(run_usage),
                    cost=cost,
                    usage_source=usage_source,
                    cost_status=(
                        "cursor_reported" if cost is not None
                        else "unavailable" if billing_error and "feature_unavailable" in billing_error
                        else "pending"
                    ),
                    billing_error=billing_error,
                )
                if self.bridge_runtime is None:
                    self.last_result = response
                _ = persist_run_dialog_artifacts(
                    state=state,
                    stream_log=stream_log,
                    stream_label=stream_label,
                    agent_result=response,
                    inbox_capture_session=capture_session,
                )
                return response
        raise RuntimeError("cursor stream resume loop exited without result")
