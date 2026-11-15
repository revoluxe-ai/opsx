"""Адаптер Cursor SDK для мутирующих repair-примитивов.

Отдельно от ``cursor_sdk_adapter`` (review-only). Разрешены только tool names,
которые local bridge реально отдаёт: ``edit`` (legacy ``pi*`` → ``edit``), ``shell``, read-only.
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import asyncio
from contextlib import asynccontextmanager
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel

from cursor_sdk.asyncio import AsyncAgent, AsyncClient
from cursor_sdk.errors import APITimeoutError, InternalServerError

REPO_ROOT = project_root()
from agents.adapters.hop_model_selection import (
    default_hop_model_selection,
    implementation_arbiter_model_selection,
    model_selection_for_hop,
)
from agents.adapters.repair_tool_policy import (
    ALLOWED_TOOLS,
    MUTATION_TOOLS,
    READ_ONLY_TOOLS,
    tools_for_adapter_create,
)
from agents.verify.impl_verify.scoped_read_tool import (
    build_impl_verify_scoped_read_sdk_options,
    hop_uses_scoped_read_tool,
)
from agents.agent_tools.image.parent_tool import hop_includes_execute_image_task
from agents.agent_tools.registry import AgentToolRegistry
from agents.implementation.apply.visual_cover_asset_tool import (
    hop_includes_visual_cover_asset_tool,
)
from agents.verify.repair.run_pytest_tool import (
    hop_includes_run_pytest_tool,
    merge_run_pytest_custom_tools,
)
from agents.abc_agent import is_cursor_bridge_stream_drop, log_cursor_bridge_stream_drop
from agents.adapters.abc_adapter import ABCAdapter, ActiveCursorAgentRunError, AgentResult
from agents.config import ReadScopeViolation
from opsx_recording.adapter_hooks import adapter_inbox_capture_scope
from run_dialogs import persist_run_dialog_artifacts
from workflows.repo_paths import normalize_workspace_path  # noqa: E402

from .capability_options import build_provider_agent_options
from .cursor_bridge_runtime import CursorBridgeRuntime
from workflows.stop_errors import OpsxStageStop

from .cursor_sdk_stream import (
    useful_progress_stall_seconds_for_tools,
    wait_cursor_run_with_live_log,
)

EXECUTOR_ROOT = resource_path(REPO_ROOT, "tools", "cursor_executor")
if str(EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTOR_ROOT))

from cursor_executor.auth import resolve_auth  # noqa: E402
from cursor_executor.bridge_local import (  # noqa: E402
    agent_local_options,
    bridge_launch_kwargs,
    cursor_sdk_builtin_tools_only_extras,
)

tools_for_cursor_sdk_create = tools_for_adapter_create
model_selection_for_sdk = model_selection_for_hop
_USEFUL_PROGRESS_STALL_RETRY_MAX = 2


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


class RepairCursorSDKAdapter(ABCAdapter):
    """Fresh Cursor agent per primitive; LangGraph owns orchestration state."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 7200,
        bridge_runtime: CursorBridgeRuntime | None = None,
        agent_tool_registry: AgentToolRegistry | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.bridge_runtime = bridge_runtime
        self.agent_tool_registry = agent_tool_registry
        self.last_result: AgentResult | None = None

    @asynccontextmanager
    async def _connection(self, workspace: Path):
        if self.bridge_runtime is not None:
            async with self.bridge_runtime.connection(workspace) as client:
                try:
                    yield client
                except BaseException as exc:
                    if is_cursor_bridge_stream_drop(exc):
                        log_cursor_bridge_stream_drop(
                            agent_name="repair_connection",
                            run_id=None,
                            agent_id=None,
                            exc=exc,
                            resume_attempt=0,
                            max_resumes=0,
                        )
                        await self.bridge_runtime.invalidate(client)
                    # Mutation authority: never silently replay a failed run.
                    raise
        else:
            async with await AsyncClient.launch_bridge(
                **bridge_launch_kwargs(agent_cwd=str(workspace))
            ) as client:
                yield client

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
        requested = tuple(tools)
        capability_manifest = state.get("capability_manifest")

        if not workspace.is_dir():
            raise ValueError(f"Workspace not found: {workspace}")
        if not isinstance(model, Mapping) or not model.get("id"):
            raise ValueError("model must be a Cursor SDK model selection")
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        if capability_manifest is None:
            unknown = set(requested) - ALLOWED_TOOLS
            if unknown:
                raise ValueError(
                    "unsupported Cursor SDK repair tools: "
                    + ", ".join(sorted(unknown))
                )
            if len(requested) != len(set(requested)):
                raise ValueError("duplicate tool names")
        elif requested:
            raise ValueError("capability hop must not request built-in tools")

        auth = resolve_auth()
        if not auth.api_key:
            raise RuntimeError("CURSOR_API_KEY is missing")

        local = (
            self.bridge_runtime.agent_local_options(agent_cwd=str(workspace))
            if self.bridge_runtime is not None
            else agent_local_options(agent_cwd=str(workspace))
        )
        allowed = tools_for_cursor_sdk_create(requested)
        scoped_prompt = prompt
        scoped_read_hop = hop_uses_scoped_read_tool(agent_key, requested)
        stream_log = state.get("stream_log")
        stream_label = str(state.get("stream_label") or "repair")
        async with adapter_inbox_capture_scope(
            state,
            stream_label=stream_label,
            stream_log=stream_log,
            resume_agent_id=resume_agent_id,
        ) as capture_session:
            async with self._connection(workspace) as client:
                if capability_manifest is not None:
                    options = build_provider_agent_options(
                        capability_manifest,
                        api_key=auth.api_key,
                        model=dict(model),
                        local=local,
                        tool_runtime=state.get("apply_design_tool_runtime"),
                    )
                    send_kwargs: dict[str, Any] = {}
                else:
                    repo_root = Path(
                        str(
                            state.get("repository_path")
                            or state.get("workspace")
                            or workspace,
                        ),
                    ).resolve()
                    if str(agent_key or "").strip() in (
                        "image_asset",
                        "image_asset_visual_review",
                    ):
                        child_tools = state.get("image_child_custom_tools")
                        if not isinstance(child_tools, Mapping):
                            raise ValueError(
                                "image_child_custom_tools required for image_asset hop",
                            )
                        from agents.agent_tools.sdk_options import (
                            build_image_child_sdk_options,
                        )

                        options = build_image_child_sdk_options(
                            api_key=auth.api_key,
                            model=dict(model),
                            local=local,
                            custom_tools=child_tools,
                        )
                        send_kwargs = {}
                    elif scoped_read_hop:
                        if read_scope is None:
                            from agents.config import resolve_effective_read_scope

                            read_scope = resolve_effective_read_scope(agent_key, state)
                        extra = tuple(
                            t
                            for t in allowed
                            if t not in {"read", "grep", "glob", "ls", "run_pytest"}
                        )
                        extra_specs = None
                        parent_ctx = state.get("agent_tool_parent_ctx")
                        if (
                            hop_includes_execute_image_task(agent_key)
                            and self.agent_tool_registry is not None
                            and parent_ctx is not None
                        ):
                            extra_specs = self.agent_tool_registry.build_custom_tools(
                                parent_ctx,
                            )
                        options = build_impl_verify_scoped_read_sdk_options(
                            api_key=auth.api_key,
                            model=dict(model),
                            local=local,
                            scope=read_scope,
                            repo_root=repo_root,
                            extra_builtin_tools=extra,
                            include_run_pytest=hop_includes_run_pytest_tool(agent_key),
                            include_visual_cover_asset=hop_includes_visual_cover_asset_tool(
                                agent_key,
                            ),
                            extra_custom_tool_specs=extra_specs,
                        )
                        send_kwargs = {}
                    else:
                        local_payload = dict(local)
                        normalized_allowed = {
                            str(t).strip().lower() for t in allowed if str(t).strip()
                        }
                        include_pytest = (
                            hop_includes_run_pytest_tool(agent_key)
                            and "run_pytest" in normalized_allowed
                        )
                        if include_pytest:
                            local_payload = merge_run_pytest_custom_tools(
                                local_payload,
                                repo_root=repo_root,
                                agent_key=agent_key,
                            )
                            builtin_tools = [
                                t
                                for t in allowed
                                if str(t).strip().lower() not in {"run_pytest", "shell"}
                            ]
                            tool_names = ["mcp", *builtin_tools]
                            options = {
                                "apiKey": auth.api_key,
                                "model": dict(model),
                                "local": local_payload,
                                "tools": tool_names,
                                "disallowed_tools": ["shell"],
                            }
                            send_kwargs = {"tools": tool_names}
                        else:
                            options = {
                                "apiKey": auth.api_key,
                                "model": dict(model),
                                "local": local_payload,
                                "tools": allowed,
                                "disallowed_tools": ["shell"],
                                **cursor_sdk_builtin_tools_only_extras(),
                            }
                            send_kwargs = {"tools": allowed}
                stream_log = state.get("stream_log")
                stream_label = str(state.get("stream_label") or "repair")
                repo_root = Path(
                    str(
                        state.get("repository_path")
                        or state.get("workspace")
                        or workspace,
                    ),
                ).resolve()
                intra_guard = state.get("intra_run_budget_guard")
                stall_guard_seconds = useful_progress_stall_seconds_for_tools(requested)
                soft_scope_violations: list[Any] = []
                stall_retry = 0
                agent: Any = None
                run: Any = None
                result: Any = None
                while True:
                    try:
                        if self.bridge_runtime is not None:
                            await self.bridge_runtime.throttle_sdk_start()
                        use_resume = resume_agent_id if stall_retry == 0 else None
                        if use_resume:
                            agent = await AsyncAgent.resume(
                                str(use_resume), options, client=client
                            )
                        else:
                            agent = await AsyncAgent.create(options, client=client)
                        if send_kwargs:
                            run = await agent.send(scoped_prompt, send_kwargs)
                        else:
                            run = await agent.send(scoped_prompt)
                    except InternalServerError as exc:
                        if use_resume and "already has active run" in str(exc).lower():
                            raise ActiveCursorAgentRunError(str(use_resume)) from exc
                        raise
                    agent_id_for_pause = str(
                        getattr(agent, "agent_id", "") or use_resume or "",
                    )
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
                            observed_soft_scope_violation_out=(
                                soft_scope_violations
                                if agent_key == "test_repair"
                                else None
                            ),
                            intra_run_budget_guard=intra_guard,
                            useful_progress_stall_seconds=stall_guard_seconds,
                        )
                        break
                    except ReadScopeViolation as exc:
                        if (
                            agent_key == "test_repair"
                            and exc.rule == "outside_restrict_allow"
                            and not soft_scope_violations
                        ):
                            soft_scope_violations.append(exc)
                        raise
                    except OpsxStageStop as stall_exc:
                        if (
                            stall_exc.code != "CURSOR_STREAM_USEFUL_PROGRESS_STALL"
                            or stall_guard_seconds is None
                            or stall_retry >= _USEFUL_PROGRESS_STALL_RETRY_MAX
                        ):
                            raise
                        stall_retry += 1
                        _ = sys.stderr.write(
                            "named_bug_repair: useful_progress_stall retry "
                            f"{stall_retry}/{_USEFUL_PROGRESS_STALL_RETRY_MAX} "
                            f"hop={stream_label} — новый Cursor run\n",
                        )
                        sys.stderr.flush()
                        continue
                    except Exception as exc:
                        if (
                            isinstance(exc, (asyncio.TimeoutError, APITimeoutError))
                            or is_cursor_bridge_stream_drop(exc)
                        ):
                            raw_run_id = getattr(run, "id", None)
                            current_run_id: str | None
                            if raw_run_id is None or str(raw_run_id).strip() == "":
                                current_run_id = None
                            else:
                                current_run_id = str(raw_run_id).strip()
                            raise ActiveCursorAgentRunError(
                                agent_id_for_pause or "unknown",
                                current_run_id,
                                code="SDK_BRIDGE_TIMEOUT",
                            ) from exc
                        raise

                run_usage = run.usage or result.usage
                usage_source = (
                    "run.usage" if run_usage is not None else "unavailable"
                )
                cost = None
                billing_error = None
                for delay in (0.0, 1.0, 3.0):
                    if delay:
                        await asyncio.sleep(delay)
                    try:
                        billed = await asyncio.wait_for(
                            agent.get_usage(),
                            timeout=8,
                        )
                        if run_usage is None and not resume_agent_id:
                            run_usage = billed.usage
                            usage_source = "agent.get_usage"
                        billed_cost = billed.cost
                        if billed_cost is None and not resume_agent_id:
                            if len(billed.runs) == 1:
                                billed_cost = billed.runs[0].cost
                        if billed_cost is not None and not resume_agent_id:
                            cost = {
                                "raw_cost_cents": billed_cost.raw_cost_cents,
                                "charged_cents": billed_cost.charged_cents,
                            }
                            break
                    except Exception as exc:
                        billing_error = f"{type(exc).__name__}: {exc}"
                        if "feature_unavailable" in str(exc):
                            break

                sdk_status = getattr(result.status, "value", result.status)
                recorded_soft_scope = (
                    soft_scope_violations[0] if soft_scope_violations else None
                )
                response = AgentResult(
                    text=result.result or "",
                    status=str(sdk_status),
                    agent_id=str(result.agent_id or agent.agent_id),
                    run_id=str(result.id),
                    usage=_token_counts(run_usage),
                    cost=cost,
                    usage_source=usage_source,
                    cost_status=(
                        "cursor_reported"
                        if cost is not None
                        else "unavailable"
                        if billing_error
                        and "feature_unavailable" in billing_error
                        else "pending"
                    ),
                    billing_error=billing_error,
                    recorded_outside_restrict_allow=recorded_soft_scope,
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
