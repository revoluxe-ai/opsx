"""Guard-тесты: известные входы встроены, запрещённые подсказки убраны."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agents.config import GLOBAL_READ_SCOPE, ReadScopeViolation
from agents.planning.scenario_coverage_plan.prompt.get_prompt import get_prompt as plan_prompt
from agents.read_scope_turn import read_scope_correction_prompt
from agents.repair.prompt_wire import build_role_prompt
from agents.verify.static_triage.opsx_context import _verify_run_artifact_hints
from agents.verify.impl_verify.preflight import load_change_artifact_bundle


@pytest.mark.asyncio
async def test_scenario_coverage_plan_embeds_bundle(tmp_path: Path) -> None:
    change = tmp_path / "openspec" / "changes" / "c1"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text("PROP BODY", encoding="utf-8")
    bundle = load_change_artifact_bundle("c1", repo_root=tmp_path)
    text = await plan_prompt(
        repository_path=str(tmp_path),
        change_id="c1",
        artifact_bundle=bundle,
    )
    assert "PROP BODY" in text
    assert "plan.receipt.json" not in text or "Do NOT write" in text
    assert "repo-root grep" in text.lower() or "Do NOT use repo-root" in text
    assert "PLAN FILE TEMPLATE" in text
    assert "LIFECYCLE_COVERAGE:" in text
    assert "SCENARIO FILE TEMPLATE" in text
    assert "other** changes" in text.lower() or "other**" in text


def test_read_scope_correction_keeps_base_and_no_apply_scaffold() -> None:
    base = "# hop\n\n## INPUT CONTEXT\nembedded"
    text = read_scope_correction_prompt(
        ReadScopeViolation(tool="glob", path="/", rule="repo_root_scan"),
        GLOBAL_READ_SCOPE,
        {"change_id": "visual", "implementation_slice_id": "SLICE-001"},
        base_prompt=base,
    )
    assert base in text
    assert "apply-scaffold" not in text


def test_prompt_wire_no_read_dotcursor() -> None:
    repo = str(Path(__file__).resolve().parents[4])
    from workflows.repair.schemas import RepairState

    state = RepairState(
        mission_id="m",
        repository_path=repo,
        workspace=repo,
        output_dir=repo,
    )
    text = build_role_prompt(
        state,
        role="GREEN_VERIFIER",
        role_instructions="task",
        payload_model=RepairState,  # type: ignore[arg-type]
        context={"finding": {}},
    )
    assert "Read .cursor" not in text


def test_opsx_context_hints_without_receipts_dir() -> None:
    hints = _verify_run_artifact_hints({"analysis_result": {}}, "c1")
    assert "change_receipts_dir" not in hints
