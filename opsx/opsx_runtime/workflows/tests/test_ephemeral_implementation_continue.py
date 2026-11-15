"""Ephemeral verify-impl: stale END после plan должен дойти до execute hop."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.state import GraphState
from workflows.checkpoint_resume import (
    graph_invoke_continuation_enabled,
    invoke_ephemeral_workflow_graph,
)
from workflows.implementation.graph import ImplementationHopAgents, build_implementation_graph
from workflows.implementation.routing_contract import (
    RUN_STATUS_APPLY_COMPLETE,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_COMPLETE,
    STAGE_APPLY_IMPLEMENT,
    SuperImplementPlanStep,
    parse_super_implement_plan_payload,
)
from workflows.implementation.adapter_run_reconcile import Allow, Pause
from workflows.implementation.design_machine_validation import DesignMachineGateResult
from workflows.repo_paths import resolve_opsx_repository_root

_REPO_ROOT = resolve_opsx_repository_root(Path(__file__).resolve().parents[4])
_DESIGN_GATE_OK = DesignMachineGateResult(ok=True, status="PASS", exit_code=0, errors=[])


def test_graph_invoke_continuation_disabled_when_durable_off() -> None:
    """При ``LANGGRAPH_DURABLE_CHECKPOINT_ENABLED=False`` нет внешнего stale-end loop."""
    graph = build_implementation_graph(_stub_hop_agents(), checkpointer=MemorySaver())
    assert graph_invoke_continuation_enabled(graph) is False


def _stub_hop_agents() -> ImplementationHopAgents:
    class _StubAgent:
        async def run(self, state: GraphState) -> dict[str, object]:
            _ = state
            return {}

    stub = _StubAgent()
    return ImplementationHopAgents(
        apply_design=stub,  # type: ignore[arg-type]
        apply_design_review=stub,  # type: ignore[arg-type]
        apply_scaffold=stub,  # type: ignore[arg-type]
        apply_red=stub,  # type: ignore[arg-type]
        apply_implement=stub,  # type: ignore[arg-type]
        apply_green=stub,  # type: ignore[arg-type]
        implementation_arbiter=stub,  # type: ignore[arg-type]
        scenario_coverage_plan=stub,  # type: ignore[arg-type]
        scenario_test_author=stub,  # type: ignore[arg-type]
    )


def _plan_steps_after_forward_then_complete() -> list[SuperImplementPlanStep]:
    forward = parse_super_implement_plan_payload(
        {
            "status": RUN_STATUS_RUNNING,
            "next_stage": STAGE_APPLY_IMPLEMENT,
            "next_slice": "SLICE-004",
            "live_execution_authorized": True,
            "plan_only": True,
        },
    )
    done = parse_super_implement_plan_payload(
        {
            "status": RUN_STATUS_APPLY_COMPLETE,
            "next_stage": STAGE_APPLY_COMPLETE,
            "live_execution_authorized": True,
        },
    )
    return [forward, forward, done]


@pytest.mark.asyncio
async def test_ephemeral_invoke_reaches_execute_after_plan_stale_end(tmp_path: Path) -> None:
    """Один ephemeral hop: stale end после plan ведёт в ``implementation_execute_hop``."""
    (tmp_path / "opsx").mkdir()
    implement_calls: list[str] = []
    plan_steps = _plan_steps_after_forward_then_complete()
    plan_index = {"i": 0}

    class _StubImplementAgent:
        async def run(self, state: GraphState) -> dict[str, object]:
            implement_calls.append("run")
            return {
                "apply_implement_status": "APPLY_COMPLETE",
                "implementation_status": RUN_STATUS_RUNNING,
                "run_id": "ephemeral-test-run",
            }

    class _StubAgent:
        async def run(self, state: GraphState) -> dict[str, object]:
            _ = state
            return {}

    agents = ImplementationHopAgents(
        apply_design=_StubAgent(),  # type: ignore[arg-type]
        apply_design_review=_StubAgent(),  # type: ignore[arg-type]
        apply_scaffold=_StubAgent(),  # type: ignore[arg-type]
        apply_red=_StubAgent(),  # type: ignore[arg-type]
        apply_implement=_StubImplementAgent(),  # type: ignore[arg-type]
        apply_green=_StubAgent(),  # type: ignore[arg-type]
        implementation_arbiter=_StubAgent(),  # type: ignore[arg-type]
        scenario_coverage_plan=_StubAgent(),  # type: ignore[arg-type]
        scenario_test_author=_StubAgent(),  # type: ignore[arg-type]
    )
    def _next_plan_step(
        change_id: str,
        *,
        state: GraphState | None = None,
        repo_root: Path | None = None,
        live_execution_authorized: bool = False,
        plan_payload_override: dict[str, object] | None = None,
    ) -> tuple[SuperImplementPlanStep, bool]:
        _ = (change_id, state, repo_root, live_execution_authorized, plan_payload_override)
        idx = min(plan_index["i"], len(plan_steps) - 1)
        plan_index["i"] += 1
        return plan_steps[idx], False

    def _plan_super_implement_step(
        change_id: str,
        *,
        repo_root: Path,
        live_execution_authorized: bool = False,
        plan_payload_override: dict[str, object] | None = None,
    ) -> SuperImplementPlanStep:
        idx = min(plan_index["i"], len(plan_steps) - 1)
        plan_index["i"] += 1
        return plan_steps[idx]

    with (
        patch(
            "workflows.implementation.stage_dispatch.resolve_super_implement_plan_step",
            _next_plan_step,
        ),
        patch(
            "workflows.implementation.stage_dispatch.plan_super_implement_step",
            _plan_super_implement_step,
        ),
        patch(
            "workflows.implementation.code_pipeline.graph.stagnation_patch_after_hop",
            lambda *_a, **_k: {},
        ),
        patch(
            "workflows.implementation.code_pipeline.graph.decide_mutating_dispatch_allowed",
            lambda **_kwargs: Allow(),
        ),
        patch(
            "workflows.implementation.plan_hop.validate_apply_design_machine",
            lambda *_a, **_k: _DESIGN_GATE_OK,
        ),
    ):
        graph = build_implementation_graph(agents, checkpointer=MemorySaver())
        result = await invoke_ephemeral_workflow_graph(
            graph,
            initial_state={
                "change_id": "visual",
                "repository_path": str(_REPO_ROOT),
                "run_uuid": "ephemeral-impl-1",
                "live_execution_authorized": True,
                "apply_design_lane_complete": True,
                "implementation_status": "IN_PROGRESS",
            },
            repo_root=_REPO_ROOT,
            thread_id="verify-impl:visual",
        )

    assert implement_calls == ["run"], (
        "ephemeral stale-end must reach implementation_execute_hop when plan is APPLY_IMPLEMENT"
    )
    assert result.get("apply_implement_status") == "APPLY_COMPLETE" or result.get(
        "implementation_status",
    ) in {RUN_STATUS_PAUSED, RUN_STATUS_APPLY_COMPLETE}


@pytest.mark.asyncio
async def test_ephemeral_invoke_explicit_pause_when_dispatch_blocks(tmp_path: Path) -> None:
    """Dispatch gate PAUSE на execute: typed PAUSE в state, agent не вызывается."""
    (tmp_path / "opsx").mkdir()
    plan_steps = _plan_steps_after_forward_then_complete()
    plan_index = {"i": 0}

    class _StubAgent:
        async def run(self, state: GraphState) -> dict[str, object]:
            _ = state
            pytest.fail("mutating agent must not run under dispatch gate PAUSE")

    agents = ImplementationHopAgents(
        apply_design=_StubAgent(),  # type: ignore[arg-type]
        apply_design_review=_StubAgent(),  # type: ignore[arg-type]
        apply_scaffold=_StubAgent(),  # type: ignore[arg-type]
        apply_red=_StubAgent(),  # type: ignore[arg-type]
        apply_implement=_StubAgent(),  # type: ignore[arg-type]
        apply_green=_StubAgent(),  # type: ignore[arg-type]
        implementation_arbiter=_StubAgent(),  # type: ignore[arg-type]
        scenario_coverage_plan=_StubAgent(),  # type: ignore[arg-type]
        scenario_test_author=_StubAgent(),  # type: ignore[arg-type]
    )
    def _next_plan_step(
        change_id: str,
        *,
        state: GraphState | None = None,
        repo_root: Path | None = None,
        live_execution_authorized: bool = False,
        plan_payload_override: dict[str, object] | None = None,
    ) -> tuple[SuperImplementPlanStep, bool]:
        _ = (change_id, state, repo_root, live_execution_authorized, plan_payload_override)
        idx = min(plan_index["i"], len(plan_steps) - 1)
        plan_index["i"] += 1
        return plan_steps[idx], False

    def _plan_super_implement_step(
        change_id: str,
        *,
        repo_root: Path,
        live_execution_authorized: bool = False,
        plan_payload_override: dict[str, object] | None = None,
    ) -> SuperImplementPlanStep:
        idx = min(plan_index["i"], len(plan_steps) - 1)
        plan_index["i"] += 1
        return plan_steps[idx]

    with (
        patch(
            "workflows.implementation.stage_dispatch.resolve_super_implement_plan_step",
            _next_plan_step,
        ),
        patch(
            "workflows.implementation.stage_dispatch.plan_super_implement_step",
            _plan_super_implement_step,
        ),
        patch(
            "workflows.implementation.code_pipeline.graph.stagnation_patch_after_hop",
            lambda *_a, **_k: {},
        ),
        patch(
            "workflows.implementation.code_pipeline.graph.decide_mutating_dispatch_allowed",
            lambda **_kwargs: Pause(reason="dispatch_ledger_active_hop stage=APPLY_IMPLEMENT"),
        ),
        patch(
            "workflows.implementation.plan_hop.validate_apply_design_machine",
            lambda *_a, **_k: _DESIGN_GATE_OK,
        ),
    ):
        graph = build_implementation_graph(agents, checkpointer=MemorySaver())
        from workflows.implementation.routing import ImplementationLaneTerminalError

        with pytest.raises(ImplementationLaneTerminalError, match="DISPATCH_LEDGER_ACTIVE_HOP"):
            _ = await invoke_ephemeral_workflow_graph(
                graph,
                initial_state={
                    "change_id": "visual",
                    "repository_path": str(_REPO_ROOT),
                    "run_uuid": "ephemeral-pause-1",
                    "apply_design_lane_complete": True,
                    "implementation_status": "IN_PROGRESS",
                },
                repo_root=_REPO_ROOT,
            )
