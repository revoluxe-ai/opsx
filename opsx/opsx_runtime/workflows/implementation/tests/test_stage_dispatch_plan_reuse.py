"""Reuse plan JSON без повторного Node subprocess."""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.graph import END

from workflows.implementation.design_pipeline.routing import (
    design_lane_forward_plan_in_state,
    route_after_design_lane_route,
)
from workflows.implementation.routing_contract import RUN_STATUS_RUNNING, STAGE_APPLY_IMPLEMENT
from workflows.implementation.stage_dispatch import (
    consume_plan_from_completed_design_lane,
    resolve_super_implement_plan_step,
)


def test_consume_plan_from_completed_design_lane_forward() -> None:
    raw = {
        "status": RUN_STATUS_RUNNING,
        "next_stage": STAGE_APPLY_IMPLEMENT,
        "next_slice": "SLICE-004",
        "live_execution_authorized": True,
        "plan_only": True,
    }
    state = {
        "apply_design_lane_complete": True,
        "implementation_status": "IN_PROGRESS",
        "implementation_stage": None,
        "implementation_plan": raw,
    }
    assert consume_plan_from_completed_design_lane(state) == raw


def test_design_lane_route_ends_when_forward_plan_present() -> None:
    raw = {
        "status": RUN_STATUS_RUNNING,
        "next_stage": STAGE_APPLY_IMPLEMENT,
        "next_slice": "SLICE-004",
        "live_execution_authorized": True,
        "plan_only": True,
    }
    state = {
        "apply_design_lane_complete": True,
        "implementation_status": "IN_PROGRESS",
        "implementation_stage": None,
        "implementation_plan": raw,
    }
    assert design_lane_forward_plan_in_state(state)
    assert route_after_design_lane_route(state) == END


def test_consume_plan_skips_when_design_lane_incomplete() -> None:
    assert (
        consume_plan_from_completed_design_lane(
            {
                "apply_design_lane_complete": False,
                "implementation_plan": {"status": RUN_STATUS_RUNNING, "next_stage": STAGE_APPLY_IMPLEMENT},
            },
        )
        is None
    )


def test_resolve_super_implement_reuses_design_lane_plan_without_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``implementation_route`` после design subgraph — один subprocess plan на hop."""
    called: list[str] = []

    def _fail_plan(*_a: object, **_k: object) -> object:
        called.append("subprocess")
        raise AssertionError("plan subprocess must not run")

    monkeypatch.setattr(
        "workflows.implementation.stage_dispatch.plan_super_implement_step",
        _fail_plan,
    )
    (tmp_path / "opsx").mkdir()
    step, reused = resolve_super_implement_plan_step(
        "visual",
        state={
            "apply_design_lane_complete": True,
            "implementation_status": "IN_PROGRESS",
            "implementation_stage": None,
            "implementation_plan": {
                "status": RUN_STATUS_RUNNING,
                "next_stage": STAGE_APPLY_IMPLEMENT,
                "next_slice": "SLICE-004",
            },
        },
        repo_root=tmp_path,
        live_execution_authorized=True,
    )
    assert reused is True
    assert step.next_stage == STAGE_APPLY_IMPLEMENT
    assert called == []
