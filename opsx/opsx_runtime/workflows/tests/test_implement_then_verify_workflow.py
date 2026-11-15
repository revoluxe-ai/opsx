"""Контрактные тесты parent LangGraph: implementation nested subgraph → verify."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import ExitStack
from typing import Any
from unittest.mock import MagicMock, patch

from workflows.implementation.design_machine_validation import DesignMachineGateResult

import pytest
from langgraph.graph import END, START, StateGraph

from agents.state import GraphState
from workflows.implementation.graph import ImplementationHopAgents
from workflows.implementation.routing import route_main_after_implementation
from workflows.implementation.routing_contract import (
    RUN_STATUS_APPLY_COMPLETE,
    RUN_STATUS_FAILED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_COMPLETE,
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
    parse_super_implement_plan_payload,
)
from workflows.implementation.tests.stub_stage_receipts import write_stub_stage_receipt
from workflows.main_graph import build_main_graph


def _dummy_verify_agent_bundle() -> tuple[MagicMock, ...]:
    """Заглушки для сигнатуры ``build_main_graph`` (verify subgraph подменён)."""
    return tuple(MagicMock() for _ in range(17))


def _minimal_change_tree(tmp_path: Any, change_id: str = "parent-impl-contract") -> str:
    """Минимальное дерево change для implementation lane bootstrap."""
    change_dir = tmp_path / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True, exist_ok=True)
    _ = (change_dir / "proposal.md").write_text("# stub\n", encoding="utf-8")
    design_dir = change_dir / "apply-design"
    design_dir.mkdir(parents=True, exist_ok=True)
    _ = (design_dir / "APPLY-DESIGN.json").write_text(
        json.dumps(
            {
                "change_id": change_id,
                "status": "APPLY_DESIGN_READY",
                "slice_order": {"nodes": ["SLICE-001"]},
                "slices": [{"slice_id": "SLICE-001"}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _ = (tmp_path / "opsx" / "project").mkdir(parents=True, exist_ok=True)
    return change_id


class _PlanningReadyAgent:
    """Planning hop: сразу READY без SDK."""

    async def run(self, state: GraphState) -> dict[str, object]:
        _ = state
        return {
            "planning_readiness_status": "READY",
            "planning_readiness_result": {"status": "READY"},
        }


class _AuthoringCompleteAgent:
    """Authoring hop: сразу COMPLETE без SDK."""

    async def run(self, state: GraphState) -> dict[str, object]:
        _ = state
        return {
            "scenario_authoring_status": "COMPLETE",
            "scenario_authoring_result": {"status": "ALREADY_CURRENT", "pending": []},
        }


async def _stub_parent_materialize(state: GraphState) -> dict[str, object]:
    """Materialize hop: пропуск verify preflight в контрактных тестах."""
    _ = state
    return {"plan_artifact_materialization_result": {"status": "ALREADY_CURRENT"}}


class _RecordingHopAgent:
    """Stub Apply-hop: пишет ``(stage, slice_id)`` в общий hop log."""

    def __init__(self, hop_log: list[tuple[str, str | None]]) -> None:
        self._hop_log = hop_log

    async def run(self, state: GraphState) -> dict[str, object]:
        stage = str(state.get("implementation_stage") or "")
        slice_raw = state.get("implementation_slice_id")
        slice_id = str(slice_raw) if slice_raw is not None else None
        self._hop_log.append((stage, slice_id))
        change_id = str(state.get("change_id") or "")
        if change_id and slice_id and stage.startswith("APPLY_"):
            from pathlib import Path

            write_stub_stage_receipt(
                repo_root=Path(str(state["repository_path"])),
                change_id=change_id,
                slice_id=slice_id,
                stage=stage,
            )
        prefix = {
            STAGE_APPLY_SCAFFOLD: "apply_scaffold",
            STAGE_APPLY_RED: "apply_red",
            STAGE_APPLY_IMPLEMENT: "apply_implement",
            STAGE_APPLY_GREEN: "apply_green",
            "APPLY_DESIGN": "apply_design",
            "APPLY_DESIGN_REVIEW": "apply_design_review",
        }.get(stage, "apply_unknown")
        return {
            "implementation_last_hop": stage,
            f"{prefix}_status": "APPLY_COMPLETE",
        }


def _hop_agents(hop_log: list[tuple[str, str | None]]) -> ImplementationHopAgents:
    stub = _RecordingHopAgent(hop_log)
    return ImplementationHopAgents(
        apply_design=stub,  # type: ignore[arg-type]
        apply_design_review=stub,  # type: ignore[arg-type]
        apply_scaffold=stub,  # type: ignore[arg-type]
        apply_red=stub,  # type: ignore[arg-type]
        apply_implement=stub,  # type: ignore[arg-type]
        apply_green=stub,  # type: ignore[arg-type]
        implementation_arbiter=stub,  # type: ignore[arg-type]
        scenario_coverage_plan=_PlanningReadyAgent(),  # type: ignore[arg-type]
        scenario_test_author=_AuthoringCompleteAgent(),  # type: ignore[arg-type]
    )


def _plan_queue_runner(
    plan_queue: list[dict[str, object]],
) -> Callable[..., Any]:
    """``side_effect`` для ``workflows.implementation.graph.plan_super_implement_step``."""

    def _next_plan_step(*_args: object, **_kwargs: object) -> Any:
        if not plan_queue:
            msg = "plan_super_implement_step called too many times"
            raise AssertionError(msg)
        payload = plan_queue.pop(0)
        return parse_super_implement_plan_payload(payload)

    return _next_plan_step


def _make_fake_verify_graph(
    verify_invocations: list[dict[str, object]],
) -> Callable[..., Any]:
    """Factory для patch ``build_verify_graph`` во время сборки parent graph."""

    def fake_build_verify_graph(
        *_args: object,
        checkpointer: Any = None,
        **_kwargs: object,
    ) -> Any:
        async def fake_verify_node(state: GraphState) -> dict[str, object]:
            verify_invocations.append(dict(state))
            return {
                "verdict": "IMPL_OK",
                "marker": "FAKE_VERIFY_COMPLETED",
            }

        builder = StateGraph(GraphState)
        builder.add_node("fake_verify", fake_verify_node)
        _ = builder.add_edge(START, "fake_verify")
        _ = builder.add_edge("fake_verify", END)
        return builder.compile(checkpointer=checkpointer)

    return fake_build_verify_graph


def _build_parent_graph(
    *,
    hop_agents: ImplementationHopAgents,
    verify_invocations: list[dict[str, object]],
) -> Any:
    """Собрать parent graph с fake verify subgraph (patch на время compile)."""
    fake_build_verify_graph = _make_fake_verify_graph(verify_invocations)
    with (
        patch(
            "workflows.main_graph.build_verify_graph",
            side_effect=fake_build_verify_graph,
        ),
        patch(
            "workflows.main_graph.parent_scenario_trace_materialize_node",
            side_effect=_stub_parent_materialize,
        ),
    ):
        return build_main_graph(
            *_dummy_verify_agent_bundle(),
            implementation_hop_agents=hop_agents,
            checkpointer=None,
        )


async def _skip_design_lane_invoke(_lane: Any, state: GraphState) -> dict[str, object]:
    """Пропустить design subgraph в контрактных тестах slice-loop."""
    _ = _lane
    _ = state
    return {
        "apply_design_lane_complete": True,
        "implementation_status": RUN_STATUS_RUNNING,
    }


async def _ainvoke_parent(
    tmp_path: Any,
    plan_queue: list[dict[str, object]],
    hop_log: list[tuple[str, str | None]],
    verify_invocations: list[dict[str, object]],
    *,
    change_id: str | None = None,
    skip_design_lane: bool = True,
    bootstrap_change_tree: bool = True,
) -> GraphState:
    cid = change_id or "parent-impl-contract"
    if bootstrap_change_tree:
        _ = _minimal_change_tree(tmp_path, cid)
    hop_agents = _hop_agents(hop_log)
    initial: GraphState = {
        "change_id": cid,
        "repository_path": str(tmp_path),
        "live_execution_authorized": True,
        "skip_implementation": False,
    }
    plan_runner = _plan_queue_runner(plan_queue)
    design_gate_ok = DesignMachineGateResult(
        ok=True,
        status="PASS",
        exit_code=0,
        errors=[],
    )
    patches = [
        patch(
            "workflows.implementation.stage_dispatch.plan_super_implement_step",
            side_effect=plan_runner,
        ),
        patch(
            "workflows.implementation.plan_hop.validate_apply_design_machine",
            return_value=design_gate_ok,
        ),
        patch(
            "workflows.implementation.stage_dispatch.advance_super_implement_receipt",
            return_value={"status": RUN_STATUS_APPLY_COMPLETE},
        ),
    ]
    if skip_design_lane:
        patches.append(
            patch(
                "workflows.implementation.graph._invoke_apply_design_lane_subgraph",
                side_effect=_skip_design_lane_invoke,
            ),
        )
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        graph = _build_parent_graph(
            hop_agents=hop_agents,
            verify_invocations=verify_invocations,
        )
        result = await graph.ainvoke(initial)
    return result


_DESIGN_LANE_SKIP_PLAN: dict[str, object] = {
    "status": "RUNNING",
    "next_stage": "APPLY_SCAFFOLD",
    "next_slice": None,
    "primitive_command": "/opsx-apply-scaffold",
    "plan_only": True,
    "live_execution_authorized": True,
}

_TWO_SLICE_PLAN_QUEUE: list[dict[str, object]] = [
    dict(_DESIGN_LANE_SKIP_PLAN),
    {
        "status": "RUNNING",
        "next_stage": "APPLY_GREEN",
        "next_slice": "slice-001",
        "primitive_command": "/opsx-apply-green",
        "plan_only": True,
        "live_execution_authorized": True,
    },
    {
        "status": "RUNNING",
        "next_stage": "APPLY_RED",
        "next_slice": "slice-002",
        "primitive_command": "/opsx-apply-red",
        "plan_only": True,
        "live_execution_authorized": True,
    },
    {
        "status": "RUNNING",
        "next_stage": "APPLY_IMPLEMENT",
        "next_slice": "slice-002",
        "primitive_command": "/opsx-apply-implement",
        "plan_only": True,
        "live_execution_authorized": True,
    },
    {
        "status": "RUNNING",
        "next_stage": "APPLY_GREEN",
        "next_slice": "slice-002",
        "primitive_command": "/opsx-apply-green",
        "plan_only": True,
        "live_execution_authorized": True,
    },
    {
        "status": "APPLY_COMPLETE",
        "next_stage": "APPLY_COMPLETE",
        "auto_verify_forbidden": True,
        "live_execution_authorized": True,
    },
]


@pytest.mark.parametrize(
    ("implementation_status", "expected_route"),
    [
        (RUN_STATUS_APPLY_COMPLETE, "verify"),
        (RUN_STATUS_PAUSED, "raise"),
        (RUN_STATUS_FAILED, "raise"),
        (RUN_STATUS_RUNNING, "raise"),
    ],
)
def test_route_main_after_implementation_matrix(
    implementation_status: str,
    expected_route: str,
) -> None:
    from workflows.implementation.routing import ImplementationLaneTerminalError

    state: GraphState = {
        "implementation_status": implementation_status,
        "skip_implementation": False,
    }
    if expected_route == "raise":
        with pytest.raises(ImplementationLaneTerminalError):
            _ = route_main_after_implementation(state)
        return
    assert route_main_after_implementation(state) == expected_route


@pytest.mark.asyncio
async def test_parent_apply_complete_invokes_verify_once(tmp_path: Any) -> None:
    plan_queue: list[dict[str, object]] = []
    hop_log: list[tuple[str, str | None]] = []
    verify_invocations: list[dict[str, object]] = []
    final = await _ainvoke_parent(tmp_path, plan_queue, hop_log, verify_invocations)
    assert plan_queue == []
    assert len(verify_invocations) == 1
    assert final.get("implementation_status") == RUN_STATUS_APPLY_COMPLETE
    assert final.get("verdict") == "IMPL_OK"
    assert final.get("marker") == "FAKE_VERIFY_COMPLETED"


@pytest.mark.asyncio
async def test_parent_paused_never_invokes_verify(tmp_path: Any) -> None:
    hop_log: list[tuple[str, str | None]] = []
    verify_invocations: list[dict[str, object]] = []

    class _PauseStub(_RecordingHopAgent):
        async def run(self, state: GraphState) -> dict[str, object]:
            if str(state.get("implementation_stage") or "") == STAGE_APPLY_SCAFFOLD:
                return {
                    "apply_scaffold_status": "BLOCKED",
                    "implementation_status": RUN_STATUS_PAUSED,
                    "implementation_recovery_code": "TEST_PAUSE",
                    "implementation_last_hop": STAGE_APPLY_SCAFFOLD,
                }
            return await super().run(state)

    cid = _minimal_change_tree(tmp_path)
    agents = ImplementationHopAgents(
        apply_design=_PauseStub(hop_log),  # type: ignore[arg-type]
        apply_design_review=_PauseStub(hop_log),  # type: ignore[arg-type]
        apply_scaffold=_PauseStub(hop_log),  # type: ignore[arg-type]
        apply_red=_PauseStub(hop_log),  # type: ignore[arg-type]
        apply_implement=_PauseStub(hop_log),  # type: ignore[arg-type]
        apply_green=_PauseStub(hop_log),  # type: ignore[arg-type]
        implementation_arbiter=_PauseStub(hop_log),  # type: ignore[arg-type]
            scenario_coverage_plan=_PlanningReadyAgent(),  # type: ignore[arg-type]
            scenario_test_author=_AuthoringCompleteAgent(),  # type: ignore[arg-type]
    )
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "workflows.implementation.graph._invoke_apply_design_lane_subgraph",
                side_effect=_skip_design_lane_invoke,
            ),
        )
        stack.enter_context(
            patch(
                "workflows.implementation.stage_dispatch.advance_super_implement_receipt",
                return_value={"status": RUN_STATUS_APPLY_COMPLETE},
            ),
        )
        graph = _build_parent_graph(
            hop_agents=agents,
            verify_invocations=verify_invocations,
        )
        from workflows.implementation.routing import ImplementationLaneTerminalError

        with pytest.raises(ImplementationLaneTerminalError, match="TEST_PAUSE"):
            _ = await graph.ainvoke(
                {
                    "change_id": cid,
                    "repository_path": str(tmp_path),
                    "live_execution_authorized": True,
                    "skip_implementation": False,
                },
            )
    assert len(verify_invocations) == 0


@pytest.mark.asyncio
async def test_parent_failed_never_invokes_verify(tmp_path: Any) -> None:
    hop_log: list[tuple[str, str | None]] = []
    verify_invocations: list[dict[str, object]] = []

    class _FailStub(_RecordingHopAgent):
        async def run(self, state: GraphState) -> dict[str, object]:
            if str(state.get("implementation_stage") or "") == STAGE_APPLY_SCAFFOLD:
                return {
                    "apply_scaffold_status": "BLOCKED",
                    "implementation_status": RUN_STATUS_FAILED,
                    "implementation_recovery_code": "TEST_FAILURE",
                    "implementation_last_hop": STAGE_APPLY_SCAFFOLD,
                }
            return await super().run(state)

    cid = _minimal_change_tree(tmp_path)
    agents = ImplementationHopAgents(
        apply_design=_FailStub(hop_log),  # type: ignore[arg-type]
        apply_design_review=_FailStub(hop_log),  # type: ignore[arg-type]
        apply_scaffold=_FailStub(hop_log),  # type: ignore[arg-type]
        apply_red=_FailStub(hop_log),  # type: ignore[arg-type]
        apply_implement=_FailStub(hop_log),  # type: ignore[arg-type]
        apply_green=_FailStub(hop_log),  # type: ignore[arg-type]
        implementation_arbiter=_FailStub(hop_log),  # type: ignore[arg-type]
        scenario_coverage_plan=_PlanningReadyAgent(),  # type: ignore[arg-type]
        scenario_test_author=_AuthoringCompleteAgent(),  # type: ignore[arg-type]
    )
    from workflows.implementation.routing import ImplementationLaneTerminalError

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "workflows.implementation.graph._invoke_apply_design_lane_subgraph",
                side_effect=_skip_design_lane_invoke,
            ),
        )
        stack.enter_context(
            patch(
                "workflows.implementation.stage_dispatch.advance_super_implement_receipt",
                return_value={"status": RUN_STATUS_APPLY_COMPLETE},
            ),
        )
        graph = _build_parent_graph(
            hop_agents=agents,
            verify_invocations=verify_invocations,
        )
        with pytest.raises(ImplementationLaneTerminalError, match="TEST_FAILURE"):
            _ = await graph.ainvoke(
                {
                    "change_id": cid,
                    "repository_path": str(tmp_path),
                    "live_execution_authorized": True,
                    "skip_implementation": False,
                },
            )
    assert len(verify_invocations) == 0


@pytest.mark.asyncio
async def test_diagnostics_visible_inside_fake_verify(tmp_path: Any) -> None:
    plan_queue: list[dict[str, object]] = []
    hop_log: list[tuple[str, str | None]] = []
    verify_invocations: list[dict[str, object]] = []
    final = await _ainvoke_parent(tmp_path, plan_queue, hop_log, verify_invocations)
    assert plan_queue == []
    assert len(verify_invocations) == 1
    assert verify_invocations[0].get("implementation_last_hop") == "APPLY_GREEN"
    assert final.get("implementation_last_hop") == "APPLY_GREEN"


@pytest.mark.asyncio
async def test_hop_agents_invoked_via_di(tmp_path: Any) -> None:
    hop_log: list[tuple[str, str | None]] = []
    verify_invocations: list[dict[str, object]] = []

    class _PauseAfterRed(_RecordingHopAgent):
        async def run(self, state: GraphState) -> dict[str, object]:
            stage = str(state.get("implementation_stage") or "")
            if stage == STAGE_APPLY_IMPLEMENT:
                return {
                    "apply_implement_status": "BLOCKED",
                    "implementation_status": RUN_STATUS_PAUSED,
                    "implementation_recovery_code": "STOP_AFTER_RED",
                    "implementation_last_hop": stage,
                }
            return await super().run(state)

    cid = _minimal_change_tree(tmp_path)
    hop_agents = ImplementationHopAgents(
        apply_design=_PauseAfterRed(hop_log),  # type: ignore[arg-type]
        apply_design_review=_PauseAfterRed(hop_log),  # type: ignore[arg-type]
        apply_scaffold=_PauseAfterRed(hop_log),  # type: ignore[arg-type]
        apply_red=_PauseAfterRed(hop_log),  # type: ignore[arg-type]
        apply_implement=_PauseAfterRed(hop_log),  # type: ignore[arg-type]
        apply_green=_PauseAfterRed(hop_log),  # type: ignore[arg-type]
        implementation_arbiter=_PauseAfterRed(hop_log),  # type: ignore[arg-type]
            scenario_coverage_plan=_PlanningReadyAgent(),  # type: ignore[arg-type]
            scenario_test_author=_AuthoringCompleteAgent(),  # type: ignore[arg-type]
    )
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "workflows.implementation.graph._invoke_apply_design_lane_subgraph",
                side_effect=_skip_design_lane_invoke,
            ),
        )
        stack.enter_context(
            patch(
                "workflows.implementation.stage_dispatch.advance_super_implement_receipt",
                return_value={"status": RUN_STATUS_APPLY_COMPLETE},
            ),
        )
        graph = _build_parent_graph(
            hop_agents=hop_agents,
            verify_invocations=verify_invocations,
        )
        from workflows.implementation.routing import ImplementationLaneTerminalError

        with pytest.raises(ImplementationLaneTerminalError, match="STOP_AFTER_RED"):
            _ = await graph.ainvoke(
                {
                    "change_id": cid,
                    "repository_path": str(tmp_path),
                    "live_execution_authorized": True,
                    "skip_implementation": False,
                },
            )
    assert hop_log == [
        ("APPLY_SCAFFOLD", "SLICE-001"),
        ("APPLY_RED", "SLICE-001"),
    ]


@pytest.mark.asyncio
async def test_two_slices_verify_once_and_hop_log(tmp_path: Any) -> None:
    plan_queue: list[dict[str, object]] = []
    hop_log: list[tuple[str, str | None]] = []
    verify_invocations: list[dict[str, object]] = []
    cid = _minimal_change_tree(tmp_path)
    design_path = tmp_path / "openspec" / "changes" / cid / "apply-design" / "APPLY-DESIGN.json"
    design = json.loads(design_path.read_text(encoding="utf-8"))
    design["slice_order"] = {"nodes": ["slice-001", "slice-002"]}
    design["slices"] = [{"slice_id": "slice-001"}, {"slice_id": "slice-002"}]
    _ = design_path.write_text(json.dumps(design, indent=2), encoding="utf-8")
    final = await _ainvoke_parent(
        tmp_path,
        plan_queue,
        hop_log,
        verify_invocations,
        change_id=cid,
        bootstrap_change_tree=False,
    )
    assert plan_queue == []
    assert hop_log == [
        ("APPLY_SCAFFOLD", "slice-001"),
        ("APPLY_RED", "slice-001"),
        ("APPLY_IMPLEMENT", "slice-001"),
        ("APPLY_GREEN", "slice-001"),
        ("APPLY_SCAFFOLD", "slice-002"),
        ("APPLY_RED", "slice-002"),
        ("APPLY_IMPLEMENT", "slice-002"),
        ("APPLY_GREEN", "slice-002"),
    ]
    assert len(verify_invocations) == 1
    assert final.get("implementation_status") == RUN_STATUS_APPLY_COMPLETE
    assert final.get("implementation_stage") == STAGE_APPLY_COMPLETE
    assert final.get("verdict") == "IMPL_OK"
