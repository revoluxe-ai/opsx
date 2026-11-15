"""Регрессии из плана (1–9, сжато)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.agent_tools.gates import gate_slice_image_tasks_complete
from agents.agent_tools.image.acceptance import repair_requires_new_bytes
from agents.agent_tools.image.pipeline import ImageTaskPipeline
from agents.agent_tools.image.ports.compose_generation import ComposeImageGenerationPort
from agents.agent_tools.image.ports.cursor_generation import CursorProviderImageGenerationPort
from agents.agent_tools.image.ports.protocols import ImageGenerationRequest
from agents.agent_tools.image.resolver import ImageTaskResolver
from agents.agent_tools.image.schemas import ImageDesignTask, ImageTaskAcceptance, TechnicalGateVerdict
from agents.agent_tools.image.technical_gate import run_technical_gate
from agents.agent_tools.parent_context import AgentToolParentContext
from agents.agent_tools.registry import AgentToolRegistry
from agents.agent_tools.tests.fake_adapter import RecordingFakeAdapter


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_port_technical_and_visual_pass(minimal_design) -> None:
    repo, change_id = minimal_design
    adapter = RecordingFakeAdapter()
    registry = AgentToolRegistry(adapter, allow_deterministic_fallback=True)
    ctx = AgentToolParentContext(
        change_id=change_id,
        slice_id="SLICE-001",
        parent_agent_key="apply_implement",
        repository_path=str(repo),
    )
    pipeline = ImageTaskPipeline(
        image_agent=registry.image_asset_agent,
        allow_deterministic_fallback=True,
    )
    result = await pipeline.execute(ctx, task_id="t1.cover", mode="create")
    assert result.status == "ACCEPTED"
    assert result.technical is not None and result.technical.ok
    assert (repo / "data" / "out.png").is_file()


@pytest.mark.unit
def test_write_outside_task_output_rejected(tmp_path: Path) -> None:
    task = ImageDesignTask(
        task_id="t",
        purpose="p",
        output_path="good/out.png",
        width=10,
        height=10,
        acceptance=ImageTaskAcceptance(ihdr=[10, 10], visual_rubric="r"),
    )
    port = ComposeImageGenerationPort()
    req = ImageGenerationRequest(
        task=task,
        output_path=tmp_path / "evil.png",
        repo_root=tmp_path,
    )
    import asyncio

    result = asyncio.run(port.generate(req))
    assert result.get("ok") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idempotent_reuse(minimal_design) -> None:
    repo, change_id = minimal_design
    adapter = RecordingFakeAdapter()
    registry = AgentToolRegistry(adapter, allow_deterministic_fallback=True)
    ctx = AgentToolParentContext(
        change_id=change_id,
        slice_id="SLICE-001",
        parent_agent_key="apply",
        repository_path=str(repo),
    )
    pipeline = ImageTaskPipeline(
        image_agent=registry.image_asset_agent,
        allow_deterministic_fallback=True,
    )
    first = await pipeline.execute(ctx, task_id="t1.cover")
    calls_after_first = len(adapter.calls)
    second = await pipeline.execute(ctx, task_id="t1.cover")
    assert first.status == "ACCEPTED"
    assert second.status == "ACCEPTED"
    assert len(adapter.calls) == calls_after_first


@pytest.mark.unit
def test_image_repair_required_without_new_bytes() -> None:
    tech = TechnicalGateVerdict(ok=True, output_revision="same")
    assert repair_requires_new_bytes("same", tech)


@pytest.mark.unit
def test_resolver_blocked_without_tasks(tmp_path: Path) -> None:
    change = "visual"
    design_dir = tmp_path / "openspec/changes/visual/apply-design"
    design_dir.mkdir(parents=True)
    (tmp_path / "opsx").mkdir()
    design_dir.joinpath("APPLY-DESIGN.json").write_text(
        '{"schema":"opsx.apply_design","schema_version":4,'
        '"change_id":"visual","slices":[],"status":"APPLY_DESIGN_READY",'
        '"generated_at":"x","chat_history_required":false,'
        '"write_boundary":{"product_code":false,"tests":false,"allowed_paths":["a"]},'
        '"planning_identity":{"change_root":"c","inputs":[],"planning_digest":"' + "0" * 64 + '"},'
        '"artifact_identity":{"human_path":"h","machine_path":"m"},'
        '"blockers":[],"next_stage":"n"}',
        encoding="utf-8",
    )
    resolver = ImageTaskResolver(repo_root=tmp_path)
    assert resolver.list_tasks(change, slice_id="SLICE-001") == []


@pytest.mark.unit
def test_registry_not_in_graph_state_module() -> None:
    import agents.state as state_mod

    src = Path(state_mod.__file__).read_text(encoding="utf-8")
    assert "agent_tool_bundle" not in src


@pytest.mark.unit
@pytest.mark.asyncio
async def test_swap_compose_vs_cursor_port(minimal_design) -> None:
    repo, change_id = minimal_design
    resolver = ImageTaskResolver(repo_root=repo)
    task = resolver.resolve(change_id, "t1.cover", slice_id="SLICE-001")
    compose = ComposeImageGenerationPort()
    cursor = CursorProviderImageGenerationPort()
    req = ImageGenerationRequest(
        task=task,
        output_path=repo / task.output_path,
        repo_root=repo,
    )
    r1 = await compose.generate(req)
    (repo / task.output_path).unlink(missing_ok=True)
    r2 = await cursor.generate(req)
    assert r1.get("ok") and r2.get("ok")


@pytest.mark.unit
def test_slice_gate_missing_accepted(minimal_design) -> None:
    repo, change_id = minimal_design
    gate = gate_slice_image_tasks_complete(
        repo_root=repo,
        change_id=change_id,
        slice_id="SLICE-001",
    )
    assert gate["ok"] is False
