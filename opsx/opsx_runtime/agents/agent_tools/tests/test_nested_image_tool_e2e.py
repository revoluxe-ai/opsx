"""E2E: parent tool → ImageAssetAgent child hop + options wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.adapters.abc_adapter import AgentResult
from agents.agent_tools.image.image_asset_agent import ImageAssetHopAnswer
from agents.agent_tools.image.parent_tool import build_execute_image_task_tool
from agents.agent_tools.parent_context import AgentToolParentContext
from agents.agent_tools.registry import AgentToolRegistry
from agents.agent_tools.tests.fake_adapter import RecordingFakeAdapter
from agents.verify.impl_verify.scoped_read_tool import build_impl_verify_scoped_read_sdk_options
from agents.config import ReadScope


@pytest.mark.unit
@pytest.mark.asyncio
async def test_nested_image_tool_invoke_and_options(minimal_design) -> None:
    repo, change_id = minimal_design
    adapter = RecordingFakeAdapter()
    child_invocations = 0

    async def _child_handler(state, *, prompt, tools, payload_model):  # noqa: ANN001
        nonlocal child_invocations
        child_invocations += 1
        parsed = ImageAssetHopAnswer(status="DONE", chosen_operation="image_compose")
        return AgentResult(
            text=parsed.model_dump_json(),
            status="COMPLETED",
            agent_id="child-agent",
            run_id=f"run-{child_invocations}",
            parsed=parsed,
        )

    adapter._handlers["image_asset"] = _child_handler
    registry = AgentToolRegistry(adapter, allow_deterministic_fallback=True)
    parent_ctx = AgentToolParentContext(
        change_id=change_id,
        slice_id="SLICE-001",
        parent_agent_key="apply_implement",
        repository_path=str(repo),
        parent_run_id="parent-run",
    )
    extra = registry.build_custom_tools(parent_ctx)
    scope = ReadScope(allow=("**",), deny=())
    options = build_impl_verify_scoped_read_sdk_options(
        api_key="test",
        model={"id": "composer-2.5"},
        local={},
        scope=scope,
        repo_root=repo,
        extra_builtin_tools=("edit",),
        extra_custom_tool_specs=extra,
    )
    provider_tools = options["local"]["custom_tools"]
    assert "execute_image_task" in provider_tools

    tool_map = build_execute_image_task_tool(
        image_agent=registry.image_asset_agent,
        parent_ctx=parent_ctx,
        allow_deterministic_fallback=True,
    )
    result = await tool_map["execute_image_task"].execute({"task_id": "t1.cover"}, None)
    assert result.get("status") in ("ACCEPTED", "VISUAL_FAIL", "FAIL")
    assert child_invocations == 1
    assert adapter.calls
    child_calls = [c for c in adapter.calls if c.get("agent_key") == "image_asset"]
    assert child_calls
    assert child_calls[0]["resume_agent_id"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_two_task_ids_isolated_paths(minimal_design) -> None:
    repo, change_id = minimal_design
    adapter = RecordingFakeAdapter()
    def _done(*_a, **_k):  # noqa: ANN001
        parsed = ImageAssetHopAnswer(status="DONE", chosen_operation="image_compose")
        return AgentResult(
            text=parsed.model_dump_json(),
            status="COMPLETED",
            agent_id="c",
            run_id="r",
            parsed=parsed,
        )

    adapter._handlers["image_asset"] = _done
    registry = AgentToolRegistry(adapter, allow_deterministic_fallback=True)
    tools = build_execute_image_task_tool(
        image_agent=registry.image_asset_agent,
        parent_ctx=AgentToolParentContext(
            change_id=change_id,
            slice_id="SLICE-001",
            parent_agent_key="apply",
            repository_path=str(repo),
        ),
        allow_deterministic_fallback=True,
    )
    _ = await tools["execute_image_task"].execute({"task_id": "t1.cover"}, None)
    assert (repo / "data/out.png").is_file()
