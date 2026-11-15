"""R-000: ``RUN.json`` не закрывает implementation lane; completion только через fresh plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from agents.state import GraphState
from workflows.implementation.code_pipeline.graph import main_lane_bootstrap_patch
from workflows.implementation.ledger import is_super_implement_apply_complete
from workflows.implementation.routing import (
    OUTCOME_ENTER_SCENARIO_AUTHORING,
    route_after_apply_design_lane,
)
from workflows.implementation.routing_contract import RUN_STATUS_APPLY_COMPLETE, RUN_STATUS_PAUSED
from workflows.tests.test_implement_then_verify_workflow import (
    _ainvoke_parent,
    _minimal_change_tree,
    _PlanningReadyAgent,
)


@pytest.mark.asyncio
async def test_begin_always_routes_to_plan_despite_stale_run(tmp_path: Path) -> None:
    """Stale ``RUN.json=APPLY_COMPLETE`` не пропускает loop и не выставляет receipt_skip."""
    change_id = "stale-run-change"
    _ = _minimal_change_tree(tmp_path, change_id)
    change_dir = tmp_path / "openspec" / "changes" / change_id
    ledger_dir = change_dir / "super-implement"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    _ = (ledger_dir / "RUN.json").write_text(
        json.dumps({"status": RUN_STATUS_APPLY_COMPLETE}, indent=2),
        encoding="utf-8",
    )
    state: GraphState = {
        "change_id": change_id,
        "repository_path": str(tmp_path),
    }
    assert is_super_implement_apply_complete(change_id, repo_root=tmp_path)
    patch_body = main_lane_bootstrap_patch(state)
    assert patch_body.get("implementation_status") == "IN_PROGRESS"
    assert patch_body.get("implementation_receipt_skip") is False
    merged: GraphState = {
        **state,
        **patch_body,
        "apply_design_lane_complete": True,
    }
    assert route_after_apply_design_lane(merged) == OUTCOME_ENTER_SCENARIO_AUTHORING


def test_begin_routes_to_plan_without_receipt() -> None:
    state: GraphState = {
        "implementation_status": "IN_PROGRESS",
        "apply_design_lane_complete": True,
    }
    assert route_after_apply_design_lane(state) == OUTCOME_ENTER_SCENARIO_AUTHORING


@pytest.mark.asyncio
async def test_stale_run_on_disk_paused_plan_never_invokes_verify(tmp_path: Any) -> None:
    """На диске RUN complete, но fresh plan (mock) = PAUSED → verify subgraph не вызывается."""
    change_id = "stale-run-paused-plan"
    _ = _minimal_change_tree(tmp_path, change_id)
    change_dir = tmp_path / "openspec" / "changes" / change_id
    ledger_dir = change_dir / "super-implement"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    _ = (ledger_dir / "RUN.json").write_text(
        json.dumps({"status": RUN_STATUS_APPLY_COMPLETE}, indent=2),
        encoding="utf-8",
    )
    plan_queue: list[dict[str, object]] = []
    hop_log: list[tuple[str, str | None]] = []
    verify_invocations: list[dict[str, object]] = []

    class _PlanPauseStub:
        async def run(self, state: GraphState) -> dict[str, object]:
            stage = str(state.get("implementation_stage") or "")
            if stage == "APPLY_SCAFFOLD":
                return {
                    "apply_scaffold_status": "BLOCKED",
                    "implementation_status": RUN_STATUS_PAUSED,
                    "implementation_recovery_code": "TASKS_NOT_RECONCILED",
                    "implementation_last_hop": "APPLY_SCAFFOLD",
                }
            if state.get("apply_design_lane_complete") and not stage:
                return {
                    "scenario_authoring_status": "COMPLETE",
                    "scenario_authoring_result": {
                        "status": "ALREADY_CURRENT",
                        "pending": [],
                    },
                }
            return {}

    from workflows.implementation.graph import ImplementationHopAgents

    agents = ImplementationHopAgents(
        apply_design=_PlanPauseStub(),  # type: ignore[arg-type]
        apply_design_review=_PlanPauseStub(),  # type: ignore[arg-type]
        apply_scaffold=_PlanPauseStub(),  # type: ignore[arg-type]
        apply_red=_PlanPauseStub(),  # type: ignore[arg-type]
        apply_implement=_PlanPauseStub(),  # type: ignore[arg-type]
        apply_green=_PlanPauseStub(),  # type: ignore[arg-type]
        implementation_arbiter=_PlanPauseStub(),  # type: ignore[arg-type]
        scenario_coverage_plan=_PlanningReadyAgent(),  # type: ignore[arg-type]
        scenario_test_author=_PlanPauseStub(),  # type: ignore[arg-type]
    )
    from contextlib import ExitStack
    from unittest.mock import patch

    from workflows.tests.test_implement_then_verify_workflow import (
        _build_parent_graph,
        _skip_design_lane_invoke,
    )

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "workflows.implementation.graph._invoke_apply_design_lane_subgraph",
                side_effect=_skip_design_lane_invoke,
            ),
        )
        graph = _build_parent_graph(
            hop_agents=agents,
            verify_invocations=verify_invocations,
        )
        from workflows.implementation.routing import ImplementationLaneTerminalError

        with pytest.raises(ImplementationLaneTerminalError, match="TASKS_NOT_RECONCILED"):
            _ = await graph.ainvoke(
                {
                    "change_id": change_id,
                    "repository_path": str(tmp_path),
                    "live_execution_authorized": True,
                    "skip_implementation": False,
                },
            )
    assert is_super_implement_apply_complete(change_id, repo_root=tmp_path)
    assert len(verify_invocations) == 0


@pytest.mark.asyncio
async def test_fresh_plan_apply_complete_reaches_verify_once(tmp_path: Any) -> None:
    """APPLY_COMPLETE из plan (не из RUN skip) → parent verify ровно один раз."""
    plan_queue: list[dict[str, object]] = []
    hop_log: list[tuple[str, str | None]] = []
    verify_invocations: list[dict[str, object]] = []
    final = await _ainvoke_parent(tmp_path, plan_queue, hop_log, verify_invocations)
    assert plan_queue == []
    assert len(verify_invocations) == 1
    assert final.get("implementation_status") == RUN_STATUS_APPLY_COMPLETE
    assert final.get("verdict") == "IMPL_OK"
