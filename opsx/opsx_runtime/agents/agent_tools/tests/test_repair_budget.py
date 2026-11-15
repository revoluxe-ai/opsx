"""Бюджет repair / deadline."""

from __future__ import annotations

import json
import time

import pytest

from agents.agent_tools.image.pipeline import ImageTaskPipeline
from agents.agent_tools.image.repair_budget import (
    RepairBudgetPolicy,
    budget_exhausted,
    ensure_repair_state,
    load_repair_state,
    record_attempt,
    save_repair_state,
    visual_counts_as_progress,
)
from agents.agent_tools.image.schemas import VisualFinding, VisualReviewVerdict
from agents.agent_tools.image.visual_review import fake_visual_review
from agents.agent_tools.parent_context import AgentToolParentContext
from agents.agent_tools.registry import AgentToolRegistry
from agents.agent_tools.tests.fake_adapter import RecordingFakeAdapter
from agents.adapters.abc_adapter import AgentResult
from agents.agent_tools.image.image_asset_agent import ImageAssetHopAnswer


@pytest.mark.unit
def test_bytes_change_not_progress() -> None:
    prior = {"A", "B"}
    verdict = VisualReviewVerdict(
        status="FAIL",
        findings=[
            VisualFinding(code="A", message="still bad"),
            VisualFinding(code="B", message="still bad"),
        ],
    )
    assert not visual_counts_as_progress(prior_finding_codes=prior, verdict=verdict)


@pytest.mark.unit
def test_resolved_finding_is_progress() -> None:
    prior = {"A", "B"}
    verdict = VisualReviewVerdict(
        status="FAIL",
        findings=[VisualFinding(code="B", message="remaining")],
    )
    assert visual_counts_as_progress(prior_finding_codes=prior, verdict=verdict)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exhausted_after_max_attempts(minimal_design, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, change_id = minimal_design
    design_path = repo / "openspec/changes/visual/apply-design/APPLY-DESIGN.json"
    data = json.loads(design_path.read_text(encoding="utf-8"))
    data["slices"][0]["image_tasks"][0]["repair_max_attempts"] = 2
    data["slices"][0]["image_tasks"][0]["repair_deadline_seconds"] = 3600
    design_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

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

    class AlwaysFailReview:
        async def review(self, task, *, repo_root, prior_findings=None):  # noqa: ANN001
            return fake_visual_review(task, repo_root=repo_root, force_fail=True)

    pipeline = ImageTaskPipeline(
        image_agent=registry.image_asset_agent,
        visual_reviewer=AlwaysFailReview(),
        allow_deterministic_fallback=True,
    )
    ctx = AgentToolParentContext(
        change_id=change_id,
        slice_id="SLICE-001",
        parent_agent_key="apply_implement",
        repository_path=str(repo),
    )
    r1 = await pipeline.execute(ctx, task_id="t1.cover", mode="create")
    assert r1.status == "VISUAL_FAIL"
    r2 = await pipeline.execute(ctx, task_id="t1.cover", mode="repair", visual_findings=[])
    assert r2.status in ("VISUAL_FAIL", "EXHAUSTED")
    r3 = await pipeline.execute(ctx, task_id="t1.cover", mode="repair", visual_findings=[])
    assert r3.status == "EXHAUSTED"
    assert r3.code in ("IMAGE_REPAIR_BUDGET_EXHAUSTED", "IMAGE_TASK_DEADLINE")


@pytest.mark.unit
def test_deadline_exhausted(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    state = ensure_repair_state(
        tmp_path,
        "c",
        "t",
        RepairBudgetPolicy(max_attempts=10, deadline_seconds=1),
    )
    state = record_attempt(
        state,
        mode="create",
        output_revision="a",
        visual=VisualReviewVerdict(status="FAIL", findings=[]),
        made_progress=False,
    )
    save_repair_state(tmp_path, "c", "t", state)
    monkeypatch.setattr(time, "time", lambda: state["deadline_unix"] + 5)
    exhausted, code = budget_exhausted(load_repair_state(tmp_path, "c", "t"))
    assert exhausted
    assert code == "IMAGE_TASK_DEADLINE"
