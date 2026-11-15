"""Parent LangGraph + AsyncSqliteSaver: граница implementation → verify (real plan, без stub APPLY_COMPLETE)."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from agents.state import GraphState
from workflows.checkpoint_resume import (
    WorkflowResumeScope,
    invoke_checkpointed_graph,
    plan_stale_end_resume,
    stable_thread_id,
)
from workflows.implementation import stage_dispatch
from workflows.implementation.design_machine_validation import DesignMachineGateResult
from workflows.implementation.code_pipeline.graph import implementation_route_node
from workflows.implementation.graph import (
    ImplementationHopAgents,
    build_implementation_hop_agents,
)
from workflows.implementation.red_operative_validation import RedOperativeValidation
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
    parse_super_implement_plan_payload,
)
_LEGACY_PLAN_ROUTE_NODE = "implementation_route"
from workflows.main_graph import build_main_graph

_REPO_ROOT = Path(__file__).resolve().parents[4]

from workflows.implementation.tests.test_two_slice_stand import (
    CHANGE_ID,
    _PLAN_STEP_BUDGET,
    _TwoSliceCompiledBridgeAdapter,
    _copy_opsx_registry,
    _prepare_hermetic_compiled_bridge,
    _repair_slice_two_design_bindings,
    materialize_hermetic_repository,
)

_PLAN_STEP_BUDGET_BOUNDARY = _PLAN_STEP_BUDGET


def _thread_id() -> str:
    return stable_thread_id(WorkflowResumeScope(kind="verify_impl", change_id=CHANGE_ID))


def _dummy_verify_agents() -> tuple[MagicMock, ...]:
    return tuple(MagicMock() for _ in range(17))


def _fake_verify_subgraph(verify_calls: list[dict[str, object]]) -> Callable[..., Any]:
    def _factory(*_args: object, checkpointer: Any = None, **_kwargs: object) -> Any:
        async def _verify_node(state: GraphState) -> dict[str, object]:
            verify_calls.append(dict(state))
            return {"verdict": "IMPL_OK", "marker": "FAKE_VERIFY_COUNTER"}

        builder = StateGraph(GraphState)
        builder.add_node("fake_verify_entry", _verify_node)
        _ = builder.add_edge(START, "fake_verify_entry")
        _ = builder.add_edge("fake_verify_entry", END)
        return builder.compile(checkpointer=checkpointer)

    return _factory


def _compile_parent_graph(
    hop_agents: ImplementationHopAgents,
    checkpointer: Any,
    verify_calls: list[dict[str, object]],
    *,
    interrupt_after: Sequence[str] = (),
) -> Any:
    """``START → implementation → verify`` с fake verify-счётчиком и опциональным interrupt."""
    from workflows.implementation.graph import build_implementation_graph

    implementation_graph = build_implementation_graph(
        hop_agents,
        checkpointer=checkpointer,
    )
    verify_graph = _fake_verify_subgraph(verify_calls)(checkpointer=checkpointer)
    builder = StateGraph(GraphState)
    builder.add_node("implementation", implementation_graph)
    builder.add_edge(START, "implementation")
    builder.add_node("verify", verify_graph)
    builder.add_conditional_edges(
        "implementation",
        route_main_after_implementation,
        {END: END, "verify": "verify"},
    )
    builder.add_edge("verify", END)
    compile_kwargs: dict[str, Any] = {"checkpointer": checkpointer}
    if interrupt_after:
        compile_kwargs["interrupt_after"] = list(interrupt_after)
    return builder.compile(**compile_kwargs)


def _initial_state(hermetic: Path) -> GraphState:
    return {
        "change_id": CHANGE_ID,
        "repository_path": str(hermetic),
        "live_execution_authorized": True,
        "implementation_receipt_skip": True,
        "skip_implementation": False,
    }


def _budgeted_real_plan_factory(
    node_plan_trace: list[dict[str, object]],
) -> Callable[..., Any]:
    real = stage_dispatch.plan_super_implement_step

    def _wrapped(
        change_id: str,
        *,
        repo_root: Path,
        live_execution_authorized: bool = False,
        plan_payload_override: dict[str, Any] | None = None,
    ) -> Any:
        if plan_payload_override is not None:
            return parse_super_implement_plan_payload(plan_payload_override)
        invocation = len(node_plan_trace) + 1
        if invocation > _PLAN_STEP_BUDGET_BOUNDARY:
            pytest.fail(
                f"plan budget exceeded invocations={len(node_plan_trace)} trace={node_plan_trace!r}",
            )
        step = real(
            change_id,
            repo_root=repo_root,
            live_execution_authorized=live_execution_authorized,
            plan_payload_override=plan_payload_override,
        )
        payload = dict(step.raw)
        node_plan_trace.append(
            {
                "invocation": invocation,
                "status": payload.get("status"),
                "next_stage": payload.get("next_stage"),
                "next_slice": payload.get("next_slice"),
            },
        )
        return step

    return _wrapped


def _stand_patch_stack(node_plan_trace: list[dict[str, object]]) -> ExitStack:
    """Активировать patch-и hermetic two-slice (real Node plan + gate stubs)."""
    design_gate_ok = DesignMachineGateResult(
        ok=True,
        status="PASS",
        exit_code=0,
        errors=[],
    )
    red_operative_ok = RedOperativeValidation(
        ok=True,
        red_json_path=Path("noop"),
        exit_code=0,
        status="PASS",
        errors=[],
        raw_stdout="",
        raw_stderr="",
    )
    stack = ExitStack()
    _ = stack.enter_context(
        patch.object(
            stage_dispatch,
            "plan_super_implement_step",
            side_effect=_budgeted_real_plan_factory(node_plan_trace),
        ),
    )
    _ = stack.enter_context(
        patch(
            "workflows.implementation.plan_hop.validate_apply_design_machine",
            return_value=design_gate_ok,
        ),
    )
    _ = stack.enter_context(
        patch(
            "agents.implementation.apply_red.agent.validate_red_operative",
            return_value=red_operative_ok,
        ),
    )
    return stack


async def _astream_until(
    graph: Any,
    *,
    config: dict[str, object],
    initial: GraphState | None,
    stop_when: Callable[[GraphState], bool],
    max_events: int = 128,
) -> GraphState:
    """Частичный прогон parent graph до ``stop_when`` (durable checkpoint на обрыве)."""
    latest: GraphState = {}
    invocation: GraphState | None = initial
    events = 0
    async for chunk in graph.astream(
        invocation,
        config=config,
        durability="sync",
        stream_mode="values",
    ):
        if isinstance(chunk, dict):
            latest = chunk  # type: ignore[assignment]
        events += 1
        if stop_when(latest):
            break
        if events >= max_events:
            pytest.fail(f"astream budget exceeded events={events}")
        invocation = None
    return latest


async def _fresh_graph_invoke(
    checkpoint: Path,
    hop_agents: ImplementationHopAgents,
    verify_calls: list[dict[str, object]],
    *,
    interrupt_after: Sequence[str] = (),
    initial: GraphState | None = None,
    node_plan_trace: list[dict[str, object]] | None = None,
) -> GraphState:
    """Открыть новый AsyncSqliteSaver на том же файле и вызвать ``invoke_checkpointed_graph``."""
    trace = node_plan_trace if node_plan_trace is not None else []
    with _stand_patch_stack(trace):
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
            graph = _compile_parent_graph(
                hop_agents,
                saver,
                verify_calls,
                interrupt_after=interrupt_after,
            )
            repo = Path(str((initial or {}).get("repository_path") or "."))
            return await invoke_checkpointed_graph(
                graph,
                initial_state=initial or {},
                thread_id=_thread_id(),
                repo_root=repo,
            )


def _strip_slice_two_lane_artifacts(hermetic: Path) -> None:
    """Оставить только прогресс SLICE-001 (fixture GREEN), снять receipt'ы SLICE-002."""
    root = hermetic / "openspec" / "changes" / CHANGE_ID
    for stage in ("apply-scaffold", "apply-red", "apply-implement", "apply-green"):
        target = root / stage / "SLICE-002"
        if target.is_dir():
            shutil.rmtree(target)


def _slice_two_lane_incomplete(hermetic: Path) -> bool:
    """True, пока SLICE-002 не закрыт GREEN (SLICE-001 уже GREEN в fixture)."""
    step = parse_super_implement_plan_payload(
        stage_dispatch.plan_super_implement_step(
            CHANGE_ID,
            repo_root=hermetic,
            live_execution_authorized=True,
        ).raw,
    )
    return step.status != RUN_STATUS_APPLY_COMPLETE


@pytest.mark.asyncio
async def test_sqlite_parent_no_verify_after_slice_one_green_only(tmp_path: Path) -> None:
    """Пока lane не APPLY_COMPLETE (только SLICE-001 GREEN в fixture) — verify не стартует."""
    hermetic = materialize_hermetic_repository(tmp_path, _REPO_ROOT)
    _copy_opsx_registry(hermetic)
    _strip_slice_two_lane_artifacts(hermetic)
    _repair_slice_two_design_bindings(hermetic)
    assert _slice_two_lane_incomplete(hermetic)
    adapter = _TwoSliceCompiledBridgeAdapter(hermetic)
    hop_agents = build_implementation_hop_agents(apply_adapter=adapter)
    verify_calls: list[dict[str, object]] = []
    checkpoint = tmp_path / "parent-boundary-1.sqlite"
    node_trace: list[dict[str, object]] = []
    initial = _initial_state(hermetic)
    config = {"configurable": {"thread_id": _thread_id()}}

    def _stop_after_first_hop(_state: GraphState) -> bool:
        return len(adapter.executed_hops) >= 1 and _slice_two_lane_incomplete(hermetic)

    with _stand_patch_stack(node_trace):
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
            graph = _compile_parent_graph(hop_agents, saver, verify_calls)
            result = await _astream_until(
                graph,
                config=config,
                initial=initial,
                stop_when=_stop_after_first_hop,
            )

    assert _slice_two_lane_incomplete(hermetic)
    assert len(verify_calls) == 0
    assert result.get("implementation_status") != RUN_STATUS_APPLY_COMPLETE
    assert result.get("implementation_status") in {RUN_STATUS_RUNNING, RUN_STATUS_PAUSED}
    assert result.get("marker") != "FAKE_VERIFY_COUNTER"
    assert route_main_after_implementation(result) != "verify"

    async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
        graph = _compile_parent_graph(hop_agents, saver, verify_calls)
        snap = await graph.aget_state(config)
        assert snap is not None
        assert "verify" not in tuple(snap.next or ())
        assert route_main_after_implementation(snap.values) != "verify"


@pytest.mark.asyncio
async def test_sqlite_parent_verify_once_after_real_apply_complete(tmp_path: Path) -> None:
    """Оба слайса + реальный Node APPLY_COMPLETE → verify ровно один раз."""
    hermetic = materialize_hermetic_repository(tmp_path, _REPO_ROOT)
    _prepare_hermetic_compiled_bridge(hermetic)
    adapter = _TwoSliceCompiledBridgeAdapter(hermetic)
    hop_agents = build_implementation_hop_agents(apply_adapter=adapter)
    verify_calls: list[dict[str, object]] = []
    checkpoint = tmp_path / "parent-boundary-2.sqlite"
    node_trace: list[dict[str, object]] = []
    initial = _initial_state(hermetic)

    with _stand_patch_stack(node_trace):
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
            graph = _compile_parent_graph(hop_agents, saver, verify_calls)
            final = await invoke_checkpointed_graph(
                graph,
                initial_state=initial,
                thread_id=_thread_id(),
                repo_root=hermetic,
            )

    assert node_trace, "ожидали вызовы реального Node plan"
    assert final.get("implementation_status") == RUN_STATUS_APPLY_COMPLETE
    assert final.get("implementation_stage") == STAGE_APPLY_COMPLETE
    assert len(verify_calls) == 1
    # IMPL_OK из fake verify — не доказательство production verify
    assert final.get("marker") == "FAKE_VERIFY_COUNTER"


@pytest.mark.asyncio
async def test_sqlite_resume_after_implement_skips_repeat_red(tmp_path: Path) -> None:
    """После accepted IMPLEMENT: close/reopen sqlite — повторного RED для того же slice нет."""
    hermetic = materialize_hermetic_repository(tmp_path, _REPO_ROOT)
    _prepare_hermetic_compiled_bridge(hermetic)
    adapter = _TwoSliceCompiledBridgeAdapter(hermetic)
    hop_agents = build_implementation_hop_agents(apply_adapter=adapter)
    verify_calls: list[dict[str, object]] = []
    checkpoint = tmp_path / "parent-boundary-3.sqlite"
    node_trace: list[dict[str, object]] = []
    initial = _initial_state(hermetic)
    config = {"configurable": {"thread_id": _thread_id()}}

    with _stand_patch_stack(node_trace):
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
            graph = _compile_parent_graph(hop_agents, saver, verify_calls)
            _ = await _astream_until(
                graph,
                config=config,
                initial=initial,
                stop_when=lambda _state: any(
                    stage == STAGE_APPLY_IMPLEMENT and sid == "SLICE-002"
                    for stage, sid in adapter.executed_hops
                ),
            )
        hops_before_resume = list(adapter.executed_hops)
        red_before = sum(1 for s, sid in hops_before_resume if s == STAGE_APPLY_RED)

        final = await _fresh_graph_invoke(
            checkpoint,
            hop_agents,
            verify_calls,
            initial=initial,
            node_plan_trace=node_trace,
        )

    hops_after = adapter.executed_hops
    red_after = sum(1 for s, sid in hops_after if s == STAGE_APPLY_RED)
    new_red_on_resume = [
        (s, sid)
        for s, sid in hops_after[len(hops_before_resume) :]
        if s == STAGE_APPLY_RED and sid == "SLICE-002"
    ]
    assert new_red_on_resume == []
    assert red_after == red_before
    assert str(final.get("implementation_status") or "") in {
        RUN_STATUS_RUNNING,
        RUN_STATUS_APPLY_COMPLETE,
    }


@pytest.mark.asyncio
async def test_sqlite_boundary_resume_enters_verify_not_implementation_route_bootstrap(
    tmp_path: Path,
) -> None:
    """На границе impl→verify после close/reopen следующий шаг — verify, не лишний plan/bootstrap."""
    hermetic = materialize_hermetic_repository(tmp_path, _REPO_ROOT)
    _prepare_hermetic_compiled_bridge(hermetic)
    adapter = _TwoSliceCompiledBridgeAdapter(hermetic)
    hop_agents = build_implementation_hop_agents(apply_adapter=adapter)
    verify_calls: list[dict[str, object]] = []
    checkpoint = tmp_path / "parent-boundary-4.sqlite"
    node_trace: list[dict[str, object]] = []
    initial = _initial_state(hermetic)
    config = {"configurable": {"thread_id": _thread_id()}}

    with _stand_patch_stack(node_trace):
        paused: GraphState = {}
        for step in range(_PLAN_STEP_BUDGET_BOUNDARY):
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
                graph = _compile_parent_graph(
                    hop_agents,
                    saver,
                    verify_calls,
                    interrupt_after=("implementation",),
                )
                invocation: GraphState | None = initial if step == 0 else None
                paused = await graph.ainvoke(
                    invocation,
                    config=config,
                    durability="sync",
                )
            if str(paused.get("implementation_status") or "") == RUN_STATUS_APPLY_COMPLETE:
                break

    assert paused.get("implementation_status") == RUN_STATUS_APPLY_COMPLETE
    assert len(verify_calls) == 0

    async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
        graph = _compile_parent_graph(hop_agents, saver, verify_calls)
        snap = await graph.aget_state(config)
        assert snap is not None
        next_nodes = tuple(snap.next or ())
        assert "verify" in next_nodes or (
            not next_nodes and route_main_after_implementation(snap.values) == "verify"
        )
        plan = await plan_stale_end_resume(graph, config=config, snapshot=snap)
        if plan is not None:
            assert plan.successor != _LEGACY_PLAN_ROUTE_NODE
            assert plan.successor in {"verify", "fake_verify_entry"}

    final = await _fresh_graph_invoke(
        checkpoint,
        hop_agents,
        verify_calls,
        initial=initial,
        node_plan_trace=node_trace,
    )
    assert len(verify_calls) == 1
    assert final.get("marker") == "FAKE_VERIFY_COUNTER"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status,terminal_payload",
    [
        (
            RUN_STATUS_PAUSED,
            {
                "status": "PAUSED",
                "code": "BOUNDARY_TEST_PAUSE",
                "live_execution_authorized": True,
            },
        ),
        (
            RUN_STATUS_FAILED,
            {
                "status": "FAILED",
                "code": "BOUNDARY_TEST_FAIL",
                "live_execution_authorized": True,
            },
        ),
    ],
)
async def test_sqlite_terminal_pause_failed_no_verify_no_begin_loop(
    tmp_path: Path,
    terminal_status: str,
    terminal_payload: dict[str, object],
) -> None:
    """PAUSED/FAILED: verify не вызывается и resume не зацикливает implementation_route bootstrap."""
    change_id = "boundary-terminal"
    hop_log: list[tuple[str, str | None]] = []

    class _StubHop:
        async def run(self, state: GraphState) -> dict[str, object]:
            stage = str(state.get("implementation_stage") or "")
            hop_log.append((stage, None))
            return {"implementation_last_hop": stage}

    stub = _StubHop()
    hop_agents = ImplementationHopAgents(
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
    verify_calls: list[dict[str, object]] = []
    checkpoint = tmp_path / f"parent-boundary-5-{terminal_status}.sqlite"
    change_dir = tmp_path / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True, exist_ok=True)
    _ = (change_dir / "proposal.md").write_text("# stub\n", encoding="utf-8")
    _ = (tmp_path / "opsx" / "project").mkdir(parents=True, exist_ok=True)

    thread = stable_thread_id(WorkflowResumeScope(kind="verify_impl", change_id=change_id))
    initial: GraphState = {
        "change_id": change_id,
        "repository_path": str(tmp_path),
        "live_execution_authorized": True,
        "skip_implementation": False,
    }
    route_calls = {"count": 0}
    real_route = implementation_route_node

    async def _counting_route(state: GraphState) -> dict[str, object]:
        route_calls["count"] += 1
        return await real_route(state)

    from workflows.tests.test_implement_then_verify_workflow import _DESIGN_LANE_SKIP_PLAN

    plan_queue = [
        dict(_DESIGN_LANE_SKIP_PLAN),
        dict(terminal_payload),
        dict(terminal_payload),
    ]

    def _pop_plan(*_a: object, **_k: object) -> Any:
        if not plan_queue:
            raise AssertionError("plan queue exhausted on resume")
        return parse_super_implement_plan_payload(plan_queue.pop(0))

    design_gate_ok = DesignMachineGateResult(
        ok=True,
        status="PASS",
        exit_code=0,
        errors=[],
    )
    with (
        patch(
            "workflows.implementation.code_pipeline.graph.implementation_route_node",
            side_effect=_counting_route,
        ),
        patch(
            "workflows.implementation.stage_dispatch.plan_super_implement_step",
            side_effect=_pop_plan,
        ),
        patch(
            "workflows.implementation.plan_hop.validate_apply_design_machine",
            return_value=design_gate_ok,
        ),
        patch(
            "workflows.main_graph.build_verify_graph",
            side_effect=_fake_verify_subgraph(verify_calls),
        ),
    ):
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
            graph = build_main_graph(
                *_dummy_verify_agents(),
                implementation_hop_agents=hop_agents,
                checkpointer=saver,
            )
            first = await invoke_checkpointed_graph(
                graph,
                initial_state=initial,
                thread_id=thread,
                repo_root=tmp_path,
            )

        assert first.get("implementation_status") == terminal_status
        assert len(verify_calls) == 0
        route_after_first = route_calls["count"]

        async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
            graph = build_main_graph(
                *_dummy_verify_agents(),
                implementation_hop_agents=hop_agents,
                checkpointer=saver,
            )
            second = await invoke_checkpointed_graph(
                graph,
                initial_state=initial,
                thread_id=thread,
                repo_root=tmp_path,
            )

    assert second.get("implementation_status") == terminal_status
    assert len(verify_calls) == 0
    assert route_calls["count"] == route_after_first
    assert route_main_after_implementation(second) != "verify"
