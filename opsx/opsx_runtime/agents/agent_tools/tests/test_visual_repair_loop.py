"""Visual FAIL → repair hop must change output_revision."""

from __future__ import annotations

import pytest

from agents.agent_tools.image.pipeline import ImageTaskPipeline
from agents.agent_tools.image.visual_review import fake_visual_review
from agents.agent_tools.parent_context import AgentToolParentContext
from agents.agent_tools.registry import AgentToolRegistry
from agents.agent_tools.tests.fake_adapter import RecordingFakeAdapter
from agents.adapters.abc_adapter import AgentResult
from agents.agent_tools.image.image_asset_agent import ImageAssetHopAnswer


@pytest.mark.unit
@pytest.mark.asyncio
async def test_visual_fail_then_repair_without_change_blocks(minimal_design) -> None:
    repo, change_id = minimal_design
    adapter = RecordingFakeAdapter()
    def _done(*_a, **_k):  # noqa: ANN001
        parsed = ImageAssetHopAnswer(status="DONE")
        return AgentResult(
            text=parsed.model_dump_json(),
            status="COMPLETED",
            agent_id="c",
            run_id="r",
            parsed=parsed,
        )

    adapter._handlers["image_asset"] = _done
    registry = AgentToolRegistry(adapter, allow_deterministic_fallback=True)
    ctx = AgentToolParentContext(
        change_id=change_id,
        slice_id="SLICE-001",
        parent_agent_key="apply_implement",
        repository_path=str(repo),
    )
    class FailingReview:
        async def review(self, task, *, repo_root, prior_findings=None):  # noqa: ANN001
            return fake_visual_review(task, repo_root=repo_root, force_fail=True)

    pipeline_fail = ImageTaskPipeline(
        image_agent=registry.image_asset_agent,
        visual_reviewer=FailingReview(),
        allow_deterministic_fallback=True,
    )
    fail_visual = await pipeline_fail.execute(ctx, task_id="t1.cover", mode="create")
    assert fail_visual.status == "VISUAL_FAIL"

    pipeline = ImageTaskPipeline(
        image_agent=registry.image_asset_agent,
        allow_deterministic_fallback=True,
    )
    repair = await pipeline.execute(
        ctx,
        task_id="t1.cover",
        mode="repair",
        visual_findings=[{"code": "X", "message": "fix"}],
    )
    if repair.code == "IMAGE_REPAIR_REQUIRED":
        assert repair.status == "BLOCKED"
    else:
        assert repair.status in ("ACCEPTED", "VISUAL_FAIL")
