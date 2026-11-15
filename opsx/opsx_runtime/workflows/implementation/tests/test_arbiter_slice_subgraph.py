"""Арбитр в slice subgraph: не двигает индекс, START-роутинг, bypass SKIP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agents.state import GraphState
from workflows.implementation.graph import _invoke_apply_one_slice_subgraph
from workflows.implementation.routing_contract import STAGE_APPLY_RED, STAGE_APPLY_SCAFFOLD
from workflows.implementation.routing import ImplementationLaneTerminalError
from workflows.implementation.slice_pipeline.graph import (
    route_after_slice_stage,
    route_slice_lane_start,
)
from workflows.implementation.slice_pipeline.stage_hop import run_slice_stage_hop

_REPO_ROOT = Path(__file__).resolve().parents[5]


class _RedObservationCompleteStub:
    """Как visual l.log: observation-only APPLY_COMPLETE, files_changed=[]."""

    async def run(self, hop_state: GraphState) -> dict[str, Any]:
        return {
            "apply_red_status": "APPLY_COMPLETE",
            "apply_red_answer": {
                "status": "APPLY_COMPLETE",
                "tasks_summary": "already-satisfied observation",
                "gap_closure_summary": (
                    "PRE_AGENT RECEIPT_MISSING closed via bounded pytest; "
                    "runtime-owned materialization"
                ),
                "files_changed": [],
                "remaining_gaps": [],
            },
        }


class _RedDemotedBlockedStub:
    """SDK lane BLOCKED при typed APPLY_COMPLETE (demotion fix)."""

    async def run(self, hop_state: GraphState) -> dict[str, Any]:
        return {
            "apply_red_status": "BLOCKED",
            "apply_red_answer": {
                "status": "APPLY_COMPLETE",
                "tasks_summary": "already-satisfied observation",
                "gap_closure_summary": "observation-only",
                "files_changed": [],
                "remaining_gaps": [],
            },
        }


class _CountingStub:
    def __init__(self, patch: dict[str, Any]) -> None:
        self.calls = 0
        self._patch = patch

    async def run(self, state: GraphState) -> dict[str, Any]:
        self.calls += 1
        return dict(self._patch)


def _ensure_opsx_root(repo: Path) -> None:
    _ = (repo / "opsx" / "project").mkdir(parents=True, exist_ok=True)


@pytest.mark.asyncio
async def test_subgraph_does_not_advance_slice_on_arbiter_pending() -> None:
    async def fake_lane(_state: GraphState) -> dict[str, Any]:
        return {
            "implementation_arbiter_pending": True,
            "implementation_status": "RUNNING",
            "implementation_last_hop": "APPLY_GREEN",
            "implementation_slice_index": 0,
            "implementation_slices": [{"slice_id": "S1"}, {"slice_id": "S2"}],
        }

    class _Lane:
        ainvoke = staticmethod(fake_lane)

    state: GraphState = {
        "implementation_slice_index": 0,
        "implementation_slices": [{"slice_id": "S1"}, {"slice_id": "S2"}],
    }
    patch = await _invoke_apply_one_slice_subgraph(_Lane(), state)  # type: ignore[arg-type]
    assert patch.get("implementation_arbiter_pending") is True
    assert patch.get("implementation_slice_index", 0) == 0


@pytest.mark.asyncio
async def test_subgraph_forwards_pause_error_to_parent() -> None:
    """PAUSE slice lane отдаёт ``error`` и machine gate в parent state."""
    async def fake_lane(_state: GraphState) -> dict[str, Any]:
        return {
            "implementation_status": "PAUSED",
            "implementation_recovery_code": "APPLY_BLOCKED",
            "implementation_stage": "APPLY_SCAFFOLD",
            "implementation_slice_id": "SLICE-004",
            "error": "SCHEMA: SCAFFOLD.json is not a scaffold receipt",
            "implementation_machine_gate_errors": [
                {"code": "SCHEMA", "message": "SCAFFOLD.json is not a scaffold receipt"},
            ],
        }

    class _Lane:
        ainvoke = staticmethod(fake_lane)

    patch = await _invoke_apply_one_slice_subgraph(_Lane(), {})  # type: ignore[arg-type]
    assert patch["error"] == "SCHEMA: SCAFFOLD.json is not a scaffold receipt"
    assert patch["implementation_machine_gate_errors"][0]["code"] == "SCHEMA"


def test_route_slice_lane_start_with_directive() -> None:
    state: GraphState = {
        "implementation_arbiter_directive": "add oracle bindings",
        "implementation_arbiter_target_stage": STAGE_APPLY_RED,
    }
    assert route_slice_lane_start(state) == "slice_apply_red"


def test_route_slice_lane_start_default_scaffold() -> None:
    state: GraphState = {"implementation_slice_id": "SLICE-001"}
    assert route_slice_lane_start(state) == "slice_apply_scaffold"


def test_route_after_red_oracle_gap_retries_one_red_hop() -> None:
    state: GraphState = {
        "implementation_status": "RUNNING",
        "implementation_last_hop": STAGE_APPLY_RED,
        "implementation_arbiter_target_stage": STAGE_APPLY_RED,
        "implementation_arbiter_directive": "align behavior marker",
        "implementation_red_oracle_retry_count": 1,
    }
    assert route_after_slice_stage(state) == "retry_red"


def test_route_after_red_oracle_gap_retries_without_directive() -> None:
    """Первый marker gap: retry_red даже если directive не попал в checkpoint."""
    state: GraphState = {
        "implementation_status": "RUNNING",
        "implementation_last_hop": STAGE_APPLY_RED,
        "implementation_red_oracle_retry_count": 1,
    }
    assert route_after_slice_stage(state) == "retry_red"


def test_route_after_red_oracle_gap_second_failure_pauses() -> None:
    state: GraphState = {
        "implementation_status": "PAUSED",
        "implementation_recovery_code": "RED_ORACLE_BEHAVIOR_MARKER_GAP",
        "implementation_last_hop": STAGE_APPLY_RED,
    }
    with pytest.raises(ImplementationLaneTerminalError):
        route_after_slice_stage(state)


def test_route_after_red_missing_receipt_retries_when_deferred() -> None:
    """stage_hop выставил retry_count=1 без RED.json — route не terminal."""
    state: GraphState = {
        "implementation_status": "RUNNING",
        "implementation_last_hop": STAGE_APPLY_RED,
        "implementation_red_oracle_retry_count": 1,
        "change_id": "visual",
        "implementation_slice_id": "SLICE-007",
        "repository_path": ".",
    }
    assert route_after_slice_stage(state) == "retry_red"


def test_route_after_red_valid_receipt_with_cleared_retry_count_routes_forward() -> None:
    """После valid PRE/SKIP счётчик сброшен — маршрут вперёд, не retry_red."""
    slice_id = "SLICE-009"
    change_id = "visual"
    red_json = (
        _REPO_ROOT / "openspec" / "changes" / change_id / "apply-red" / slice_id / "RED.json"
    )
    if not red_json.is_file():
        pytest.skip("visual SLICE-009 RED.json missing on disk")
    state: GraphState = {
        "implementation_status": "RUNNING",
        "implementation_last_hop": STAGE_APPLY_RED,
        "implementation_red_oracle_retry_count": 0,
        "change_id": change_id,
        "implementation_slice_id": slice_id,
        "repository_path": str(_REPO_ROOT),
    }
    assert route_after_slice_stage(state) == "continue"


def test_route_after_red_oracle_gap_retries_despite_stale_red_json_on_disk() -> None:
    """Устаревший RED.json не отменяет первый retry_red после materialize refusal."""
    slice_id = "SLICE-009"
    change_id = "visual"
    red_json = (
        _REPO_ROOT / "openspec" / "changes" / change_id / "apply-red" / slice_id / "RED.json"
    )
    if not red_json.is_file():
        pytest.skip("visual SLICE-009 RED.json missing on disk")
    state: GraphState = {
        "implementation_status": "RUNNING",
        "implementation_last_hop": STAGE_APPLY_RED,
        "implementation_red_oracle_retry_count": 1,
        "change_id": change_id,
        "implementation_slice_id": slice_id,
        "repository_path": str(_REPO_ROOT),
    }
    assert route_after_slice_stage(state) == "retry_red"


@pytest.mark.asyncio
async def test_stage_hop_valid_red_skip_clears_oracle_retry_count() -> None:
    """PRE valid SKIP после первого oracle-gap не оставляет retry_count=1."""
    slice_id = "SLICE-009"
    change_id = "visual"
    red_json = (
        _REPO_ROOT / "openspec" / "changes" / change_id / "apply-red" / slice_id / "RED.json"
    )
    if not red_json.is_file():
        pytest.skip("visual SLICE-009 RED.json missing on disk")
    agent = _CountingStub({"apply_red_status": "SHOULD_NOT_RUN"})
    state: GraphState = {
        "change_id": change_id,
        "repository_path": str(_REPO_ROOT),
        "implementation_slice_id": slice_id,
        "implementation_red_oracle_retry_count": 1,
    }
    out = await run_slice_stage_hop(state, agent=agent, stage=STAGE_APPLY_RED)
    assert agent.calls == 0
    assert out.get("implementation_receipt_gate") == "SKIP"
    assert out.get("implementation_red_oracle_retry_count") == 0
    assert route_after_slice_stage({**state, **out}) == "continue"


@pytest.mark.asyncio
async def test_stage_hop_visual_slice007_observation_without_red_json_schedules_retry() -> None:
    """Регрессия repair-parent-20261112-l: успешный hop без useful RED.json → retry_red."""
    slice_id = "SLICE-007"
    change_id = "visual"
    red_md = (
        _REPO_ROOT / "openspec" / "changes" / change_id / "apply-red" / slice_id / "RED.md"
    )
    red_json = red_md.with_name("RED.json")
    if not red_md.is_file():
        pytest.skip("visual SLICE-007 RED.md missing on disk")
    if red_json.is_file():
        pytest.skip("RED.json already present; case needs absent machine receipt")
    state: GraphState = {
        "change_id": change_id,
        "repository_path": str(_REPO_ROOT),
        "implementation_slice_id": slice_id,
    }
    out = await run_slice_stage_hop(
        state,
        agent=_RedObservationCompleteStub(),
        stage=STAGE_APPLY_RED,
    )
    assert out.get("implementation_red_oracle_retry_count") == 1
    assert out.get("implementation_status") == "RUNNING"
    assert out.get("implementation_arbiter_target_stage") == STAGE_APPLY_RED
    route_state: GraphState = {**state, **out}
    assert route_after_slice_stage(route_state) == "retry_red"


@pytest.mark.asyncio
async def test_stage_hop_red_demoted_blocked_still_schedules_retry_without_red_json() -> None:
    """BLOCKED lane + APPLY_COMPLETE answer: materialize не падает FORBIDDEN_PARAM:blockers."""
    slice_id = "SLICE-007"
    change_id = "visual"
    red_json = (
        _REPO_ROOT
        / "openspec/changes/visual/apply-red/SLICE-007/RED.json"
    )
    if red_json.is_file():
        pytest.skip("RED.json already present")
    state: GraphState = {
        "change_id": change_id,
        "repository_path": str(_REPO_ROOT),
        "implementation_slice_id": slice_id,
    }
    out = await run_slice_stage_hop(
        state,
        agent=_RedDemotedBlockedStub(),
        stage=STAGE_APPLY_RED,
    )
    assert out.get("implementation_red_oracle_retry_count") == 1
    assert out.get("implementation_status") == "RUNNING"
    assert route_after_slice_stage({**state, **out}) == "retry_red"


def test_route_after_red_missing_receipt_terminal_without_retry() -> None:
    state: GraphState = {
        "implementation_status": "RUNNING",
        "implementation_last_hop": STAGE_APPLY_RED,
        "implementation_red_oracle_retry_count": 0,
        "change_id": "visual",
        "implementation_slice_id": "SLICE-007",
        "repository_path": str(Path(__file__).resolve().parents[5]),
    }
    with pytest.raises(ImplementationLaneTerminalError, match="RECEIPT_PUBLISH_MISSING"):
        route_after_slice_stage(state)


@pytest.mark.asyncio
async def test_arbiter_directive_bypasses_valid_receipt_skip(tmp_path: Path) -> None:
    change = "arbiter-bypass"
    slice_id = "SLICE-001"
    repo = tmp_path
    _ensure_opsx_root(repo)
    rel = "pkg/a.py"
    file_path = repo / rel
    file_path.parent.mkdir(parents=True)
    _ = file_path.write_text("ok", encoding="utf-8")
    scaffold_dir = repo / "openspec" / "changes" / change / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    receipt = {
        "status": "SCAFFOLD_READY",
        "extended_paths": [rel],
        "created_paths": [],
    }
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(json.dumps(receipt), encoding="utf-8")
    agent = _CountingStub(
        {
            "apply_scaffold_status": "NO_CHANGE",
            "apply_scaffold_answer": {
                "status": "NO_CHANGE",
                "tasks_summary": "ok",
                "gap_closure_summary": "ok",
                "files_changed": [],
                "remaining_gaps": [],
            },
        },
    )
    state: GraphState = {
        "change_id": change,
        "repository_path": str(repo),
        "implementation_slice_id": slice_id,
        "implementation_arbiter_target_stage": STAGE_APPLY_SCAFFOLD,
        "implementation_arbiter_directive": "patch scaffold seam",
    }
    out = await run_slice_stage_hop(state, agent=agent, stage=STAGE_APPLY_SCAFFOLD)
    assert agent.calls == 1
    assert out.get("implementation_arbiter_directive") is None
    assert out.get("implementation_arbiter_target_stage") is None


def test_red_oracle_retry_count_is_graph_state_field() -> None:
    """LangGraph хранит только ключи ``GraphState``; счётчик повтора RED должен быть в схеме."""
    assert "implementation_red_oracle_retry_count" in GraphState.__annotations__
