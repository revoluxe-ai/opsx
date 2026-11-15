from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END
from langgraph.types import Command

from workflows.repair.graph import build_repair_graph
from workflows.repair.schemas import Finding, RepairState

from .fakes import FakeAdapter, response
from .test_graph import classification, fix, green, invariant, red


@pytest.mark.asyncio
async def test_sqlite_checkpoint_interrupt_and_resume_does_not_repeat_red(tmp_path):
    adapter = FakeAdapter([
        classification(),
        red(),
        fix(),
        green(),
        invariant(),
    ])
    db = tmp_path / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": "checkpoint-owner-test"}}
    initial = RepairState(
        mission_id="checkpoint-owner-test",
        repository_path=str(tmp_path),
        workspace=str(tmp_path),
        isolated_workspace=True,
        output_dir=str(tmp_path / "run"),
        owner_go=None,
        active=Finding(id="F1", finding="bug", location="a.py:1"),
    )

    async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
        graph = build_repair_graph(
            adapter,
            checkpointer=saver,
            include_preflight=False,
        )
        first = await graph.ainvoke(
            initial.model_dump(mode="json"),
            config=config,
            durability="sync",
        )
        assert "__interrupt__" in first
        assert len(adapter.calls) == 2
        assert "CLASSIFIER" in adapter.calls[0]["prompt"]
        assert "RED_TEST_WORKER" in adapter.calls[1]["prompt"]

        final = await graph.ainvoke(
            Command(resume={"approved": True, "decision": "GO"}),
            config=config,
            durability="sync",
        )

    state = RepairState.model_validate(final)
    assert state.status == "FIXED"
    assert len(adapter.calls) == 5
    assert "IMPLEMENTATION_EXECUTOR" in adapter.calls[2]["prompt"]
    assert "GREEN_VERIFIER" in adapter.calls[3]["prompt"]
    assert "INVARIANT_CAPTURE_WORKER" in adapter.calls[4]["prompt"]
    assert db.exists()
    assert db.stat().st_size > 0


@pytest.mark.asyncio
async def test_list_checkpoint_candidates_prefers_impl_verify_receipt(tmp_path):
    change_id = "sample-change"
    repo = tmp_path / "repo"
    legacy_suffix = "e006e79cd580"
    legacy_dir = repo / "opsx" / "runs" / "verify" / f"impl-{change_id}-{legacy_suffix}"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "checkpoints.sqlite").write_bytes(b"sqlite")

    verify_dir = repo / "openspec" / "changes" / change_id / "verify"
    verify_dir.mkdir(parents=True)
    receipt = {
        "finished_at": "2026-11-01T06:17:20+00:00",
        "stream_log": str(verify_dir / f"impl-verify-{legacy_suffix}.live.ndjson"),
    }
    (verify_dir / "IMPL-VERIFY-RECEIPT.json").write_text(
        json.dumps(receipt),
        encoding="utf-8",
    )

    from workflows.checkpoint_resume import WorkflowResumeScope, list_checkpoint_candidates

    scope = WorkflowResumeScope(kind="verify_impl", change_id=change_id)
    candidates = list_checkpoint_candidates(repo, scope)
    assert candidates
    assert candidates[0][0] == legacy_dir / "checkpoints.sqlite"
    assert candidates[0][1] == legacy_suffix


@pytest.mark.asyncio
async def test_resolve_stale_end_successor_after_impl_verify(
    tmp_path,
    durable_langgraph_checkpoint: None,
):
    """Checkpoint END на старом графе продолжается в ``apply`` без повторного impl_verify."""
    from functools import partial
    from unittest.mock import patch

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import END, START, StateGraph

    from agents.adapters.abc_adapter import ABCAdapter, AgentResult
    from agents.state import GraphState
    from agents.verify.impl_verify.agent import ImplVerifyAgent
    from agents.verify.impl_verify.schemas import VerifyImplAnswer
    from workflows.checkpoint_resume import (
        invoke_checkpointed_graph,
        plan_stale_end_resume,
        resolve_stale_end_resume_successor,
    )
    from workflows.main_graph import build_main_graph
    from workflows.verify.graph import impl_verify_node
    from workflows.verify.green_loop import APPLY_NODE, IMPL_VERIFY_ENTRY_NODE
    from workflows.verify.tests.test_impl_graph import _stub_verify_agents

    (tmp_path / "opsx").mkdir()
    change_id = "stale-end-change"
    calls = {"impl": 0}

    class _CountingImplAdapter(ABCAdapter):
        async def _run_turn(self, state, *, prompt, tools, payload_model=None, **kwargs: object):
            calls["impl"] += 1
            answer = VerifyImplAnswer(
                verdict="IMPL_GAPS",
                behavior_summary="stub",
                architecture_placement="stub",
                tasks_consistency="stub",
                gap_details=["gap"],
                next_command=f"/opsx-apply {change_id}",
                local_hop_copy_block="Workspace: /tmp\nChange: stale",
            )
            return AgentResult(
                text=answer.model_dump_json(),
                status="finished",
                agent_id="agent-stub",
                run_id="run-stub",
            )

    agents = _stub_verify_agents()
    impl_agent = ImplVerifyAgent(_CountingImplAdapter())
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "change_id": change_id,
        "model": {"id": "composer-2.5", "params": []},
    }
    preflight = {
        "change_id": change_id,
        "change_exists": True,
        "config_contract": {"status": "FAIL"},
    }
    checkpoint = tmp_path / "stale-end.sqlite"
    thread_id = "verify-impl:stale-end"
    config = {"configurable": {"thread_id": thread_id}}

    async def _fixture_plan_ready(_state: object) -> dict[str, object]:
        return {
            "plan_artifact_status": "READY",
            "plan_artifact_source_digest": "synthetic-stale-end",
            "plan_artifact_result": {"status": "READY", "code": "TEST_FIXTURE_READY"},
        }

    async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
        from workflows.checkpoint_resume import verify_subgraph_invoke_config

        with patch(
            "workflows.verify.graph.plan_artifacts_preflight_node",
            _fixture_plan_ready,
        ):
            main_graph = build_main_graph(
                impl_agent,
                *agents[1:],
                checkpointer=saver,
            )
        verify_subgraph, sub_config = verify_subgraph_invoke_config(main_graph, config)
        with (
            patch(
                "agents.verify.impl_verify.agent.collect_deterministic_preflight",
                return_value=preflight,
            ),
            patch(
                "agents.verify.impl_verify.agent.preflight_implies_gaps",
                return_value=["gap"],
            ),
        ):
            first = await verify_subgraph.ainvoke(
                state,
                config=sub_config,
                durability="sync",
            )

        assert first.get("verdict") == "IMPL_GAPS"
        assert calls["impl"] == 1
        sub_snap = await verify_subgraph.aget_state(sub_config)
        assert sub_snap.next == ()

        impl_before_resume = calls["impl"]
        with (
            patch(
                "agents.verify.impl_verify.agent.collect_deterministic_preflight",
                return_value=preflight,
            ),
            patch(
                "agents.verify.impl_verify.agent.preflight_implies_gaps",
                return_value=["gap"],
            ),
            patch(
                "workflows.verify.graph.plan_artifacts_preflight_node",
                _fixture_plan_ready,
            ),
        ):
            resumed = await invoke_checkpointed_graph(
                main_graph,
                initial_state=state,
                thread_id=thread_id,
                repo_root=tmp_path,
            )

    # Resume идёт в apply (Command или pending subgraph), не с повторного impl_verify на входе.
    assert calls["impl"] >= impl_before_resume
    assert resumed.get("apply_status") == "NO_CHANGE"


@pytest.mark.asyncio
async def test_invoke_checkpointed_graph_pending_nested_apply_uses_subgraph_not_root(
    durable_langgraph_checkpoint: None,
    tmp_path: Path,
):
    """При ``next=('apply',)`` в nested subgraph resume — ``subgraph.ainvoke(None)``, не root."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from workflows.checkpoint_resume import invoke_checkpointed_graph

    root_snapshot = MagicMock()
    root_snapshot.values = {"verdict": "IMPL_GAPS"}
    root_snapshot.next = ()

    sub_snapshot_pending = MagicMock()
    sub_snapshot_pending.values = {"verdict": "IMPL_GAPS"}
    sub_snapshot_pending.next = ("apply",)
    sub_snapshot_pending.config = {
        "configurable": {"checkpoint_ns": "verify:pending-apply-task"},
    }
    sub_snapshot_pending.tasks = ()

    sub_snapshot_done = MagicMock()
    sub_snapshot_done.values = {
        "verdict": "IMPL_GAPS",
        "apply_status": "NO_CHANGE",
    }
    sub_snapshot_done.next = ()

    subgraph_invoke_count = {"n": 0}

    async def _subgraph_ainvoke(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, str]:
        subgraph_invoke_count["n"] += 1
        return {"verdict": "IMPL_GAPS", "apply_status": "NO_CHANGE"}

    subgraph = MagicMock()
    subgraph.aupdate_state = None
    subgraph.ainvoke = _subgraph_ainvoke

    async def sub_aget_state_side_effect(*_a: object, **_k: object) -> MagicMock:
        if subgraph_invoke_count["n"] > 0:
            return sub_snapshot_done
        return sub_snapshot_pending

    subgraph.aget_state = AsyncMock(side_effect=sub_aget_state_side_effect)

    wrapper = MagicMock()
    wrapper.subgraphs = [subgraph]

    root_snapshot_after = MagicMock()
    root_snapshot_after.values = {
        "verdict": "IMPL_GAPS",
        "apply_status": "NO_CHANGE",
    }
    root_snapshot_after.next = ()

    graph = MagicMock()
    graph.aupdate_state = None
    graph.checkpointer = MagicMock()
    graph.nodes = {"verify": wrapper}
    root_with_tasks = MagicMock()
    root_with_tasks.values = {"verdict": "IMPL_GAPS"}
    root_with_tasks.next = ()
    verify_task = MagicMock()
    verify_task.state = sub_snapshot_pending
    root_with_tasks.tasks = (verify_task,)

    _root_aget_calls = {"n": 0}
    _subgraphs_aget_calls = {"n": 0}

    async def aget_state_side_effect(
        _config: object,
        *,
        subgraphs: bool = False,
    ) -> MagicMock:
        if subgraphs:
            _subgraphs_aget_calls["n"] += 1
            if subgraph_invoke_count["n"] > 0:
                root_with_tasks.tasks = ()
            return root_with_tasks
        _root_aget_calls["n"] += 1
        if _root_aget_calls["n"] <= 2:
            return root_snapshot
        return root_snapshot_after

    graph.aget_state = AsyncMock(side_effect=aget_state_side_effect)
    graph.ainvoke = AsyncMock()

    repo_root = tmp_path / "repo"
    (repo_root / "opsx").mkdir(parents=True)

    plan_mock = AsyncMock(return_value=None)
    with patch(
        "workflows.checkpoint_resume.plan_stale_end_resume",
        plan_mock,
    ):
        result = await invoke_checkpointed_graph(
            graph,
            initial_state={
                "repository_path": str(repo_root),
                "workspace": str(repo_root),
                "change_id": "pending-apply",
            },
            thread_id="verify-impl:pending-apply",
            repo_root=repo_root,
        )

    assert subgraph_invoke_count["n"] == 1
    graph.ainvoke.assert_not_awaited()
    plan_mock.assert_not_awaited()
    assert result.get("verdict") == "IMPL_GAPS"


def test_pending_checkpoint_score_prefers_diagnostic_over_fresh_impl_verify():
    from workflows.checkpoint_resume import _pending_checkpoint_score

    diagnostic = _pending_checkpoint_score(
        {"verdict": "IMPL_OK", "repair_order_result": {"ok": True}},
        ("diagnostic",),
    )
    fresh_impl = _pending_checkpoint_score({}, ("impl_verify",))
    assert diagnostic > fresh_impl


def test_nested_pending_worthy_rejects_fresh_impl_verify():
    from workflows.checkpoint_resume import _nested_pending_worthy

    assert not _nested_pending_worthy({}, ("impl_verify",))
    assert _nested_pending_worthy(
        {"verdict": "IMPL_OK", "repair_order_result": {"ok": True}},
        ("diagnostic",),
    )


@pytest.mark.asyncio
async def test_invoke_checkpointed_graph_history_bookmark_over_root_verify_next(
    durable_langgraph_checkpoint: None,
):
    """Ctrl+C head ``next=('verify',)`` не должен затенять older pending ``diagnostic``."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from workflows.checkpoint_resume import invoke_checkpointed_graph

    thread_id = "verify-impl:invest-yalta-max-funnel"
    crash_sub_config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_id": "f0c2bdbc-history",
            "checkpoint_ns": "verify:722b|static_repair:f0c2",
        },
    }
    anchor_invoke_config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_id": "older-root-ckpt",
        },
    }

    root_snapshot = MagicMock()
    root_snapshot.values = {"change_id": "invest-yalta-max-funnel"}
    root_snapshot.next = ("verify",)

    sub_snapshot = MagicMock()
    sub_snapshot.values = {"verdict": "IMPL_OK", "repair_order_result": {"ok": True}}
    sub_snapshot.next = ("diagnostic",)

    subgraph = AsyncMock()
    subgraph.aupdate_state = None
    subgraph.ainvoke = AsyncMock(return_value={"verdict": "IMPL_OK"})
    sub_snapshot_after = MagicMock()
    sub_snapshot_after.values = {"verdict": "IMPL_OK"}
    sub_snapshot_after.next = ()
    subgraph.aget_state = AsyncMock(
        side_effect=lambda *_a, **_k: sub_snapshot_after
        if subgraph.ainvoke.await_count
        else sub_snapshot,
    )

    graph = MagicMock()
    graph.checkpointer = MagicMock()
    graph.nodes = {}
    graph.aupdate_state = AsyncMock()
    graph.ainvoke = AsyncMock(return_value={"verdict": "IMPL_OK"})
    graph.aget_state = AsyncMock(return_value=root_snapshot)

    history_item = MagicMock()
    history_item.config = dict(anchor_invoke_config)

    async def _history(_config: object = None):
        yield history_item

    graph.aget_state_history = MagicMock(return_value=_history())

    diagnostic_pending = (subgraph, crash_sub_config, ("diagnostic",))

    resolve_calls: list[object] = []

    async def _resolve(_graph: object, cfg: object):
        resolve_calls.append(cfg)
        if not isinstance(cfg, dict):
            return None
        ckpt_id = (cfg.get("configurable") or {}).get("checkpoint_id")
        if ckpt_id == "older-root-ckpt":
            return diagnostic_pending
        return None

    with (
        patch(
            "workflows.checkpoint_resume.plan_static_repair_rewind",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume._resolve_nested_pending_subgraph",
            side_effect=_resolve,
        ),
        patch(
            "workflows.checkpoint_resume.resolve_opsx_repository_root",
            lambda path: path,
        ),
        patch(
            "workflows.checkpoint_resume.graph_repo_path_patch",
            lambda _state, _repo: {},
        ),
        patch(
            "workflows.checkpoint_resume.find_wrapper_hop_nested_resume",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume._find_runnable_predecessor_rewind_plan",
            AsyncMock(return_value=None),
        ),
    ):
        _ = await invoke_checkpointed_graph(
            graph,
            initial_state={
                "repository_path": "/tmp",
                "workspace": "/tmp",
                "change_id": "invest-yalta-max-funnel",
            },
            thread_id=thread_id,
        )

    graph.ainvoke.assert_not_awaited()
    subgraph.ainvoke.assert_awaited_once()
    assert subgraph.ainvoke.await_args.args[0] is None
    assert subgraph.ainvoke.await_args.kwargs.get("durability") == "sync"
    assert subgraph.ainvoke.await_args.kwargs["config"]["configurable"]["checkpoint_ns"] == (
        "verify:722b|static_repair:f0c2"
    )
    assert len(resolve_calls) >= 2


@pytest.mark.asyncio
async def test_find_wrapper_hop_nested_resume_returns_inner_dispatch_cycle():
    """При ``next=('dispatch_cycle',)`` на gate_repair берётся live ``next`` вложенного subgraph."""
    from unittest.mock import AsyncMock, MagicMock

    from workflows.checkpoint_resume import find_wrapper_hop_nested_resume

    thread_id = "verify-impl:sample"
    owner_config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": "verify:v1|static_repair:s1",
        },
    }

    dispatch_inner = AsyncMock()
    inner_snap = MagicMock()
    inner_snap.next = ("static_triage",)
    inner_snap.tasks = ()
    dispatch_inner.aget_state = AsyncMock(return_value=inner_snap)

    dispatch_wrapper = MagicMock()
    dispatch_wrapper.subgraphs = [dispatch_inner]

    owner = AsyncMock()
    owner.nodes = {"dispatch_cycle": dispatch_wrapper}
    owner_snap = MagicMock()
    owner_snap.tasks = ()
    owner.aget_state = AsyncMock(return_value=owner_snap)

    main = MagicMock()
    main.checkpointer = MagicMock()
    main.nodes = {
        "verify": MagicMock(subgraphs=[owner]),
        "static_repair": owner,
    }

    found = await find_wrapper_hop_nested_resume(
        main,
        owner,
        owner_config,
        ("dispatch_cycle",),
    )
    assert found is not None
    inner_graph, inner_config, inner_next = found
    assert inner_graph is dispatch_inner
    assert inner_next == ("static_triage",)
    assert inner_config["configurable"]["thread_id"] == thread_id


@pytest.mark.asyncio
async def test_stale_coverage_activation_terminal_resumes_static_repair(
    tmp_path,
    durable_langgraph_checkpoint: None,
):
    """Старый END с ``verify_stop_reason=Coverage Activation`` → ``Command(goto=static_repair)``."""
    from workflows.checkpoint_resume import (
        plan_stale_end_resume,
        stale_successor_after_coverage_activation_gap,
        verify_subgraph_invoke_config,
    )
    from workflows.main_graph import build_main_graph
    from workflows.verify.green_loop import COVERAGE_ACTIVATION_TERMINAL_NODE
    from workflows.verify.tests.test_impl_graph import _stub_verify_agents

    change_id = "ca-stale-resume"
    agents = _stub_verify_agents()
    thread_id = f"verify-impl:{change_id}"
    config = {"configurable": {"thread_id": thread_id}}
    terminal_state: dict[str, object] = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "change_id": change_id,
        "verdict": "IMPL_OK",
        "marker": "✅ IMPL_OK",
        "report_text": "# Impl verify\n\n✅ IMPL_OK\n",
        "verify_stop_reason": "Coverage Activation",
        "owner_attention": "blocked tests",
        "coverage_activation_verdict": "COVERAGE_ACTIVATION_PASS",
    }

    db = tmp_path / "ca-stale.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
        main_graph = build_main_graph(*agents, checkpointer=saver)
        verify_subgraph, sub_config = verify_subgraph_invoke_config(main_graph, config)
        update = getattr(verify_subgraph, "aupdate_state", None)
        assert update is not None
        _ = await update(
            sub_config,
            terminal_state,
            as_node=COVERAGE_ACTIVATION_TERMINAL_NODE,
        )

        gap = stale_successor_after_coverage_activation_gap(verify_subgraph, terminal_state)
        assert gap == "static_repair"

        root_snapshot = await main_graph.aget_state(config)
        assert root_snapshot.next == ()
        with patch(
            "workflows.checkpoint_resume._infer_checkpoint_finished_node",
            AsyncMock(return_value="verify"),
        ):
            plan = await plan_stale_end_resume(
                main_graph,
                config=config,
                snapshot=root_snapshot,
            )
        assert plan is not None
        assert plan.successor == "static_repair"
        assert plan.target_graph is verify_subgraph


@pytest.mark.asyncio
async def test_invoke_continues_coverage_gap_after_fresh_ainvoke(
    tmp_path,
    durable_langgraph_checkpoint: None,
):
    """После первого ``ainvoke`` на END с coverage gap — тот же ``invoke_checkpointed_graph`` идёт в static_repair."""
    from workflows.checkpoint_resume import invoke_checkpointed_graph
    from workflows.verify.cli_outcome import verify_impl_pipeline_complete

    thread_id = "verify-impl:post-invoke-gap"
    incomplete = {
        "verdict": "IMPL_OK",
        "change_id": "post-invoke-gap",
        "verify_stop_reason": "Coverage Activation",
        "coverage_activation_verdict": "COVERAGE_ACTIVATION_PASS",
    }
    complete = {
        **incomplete,
        "verify_stop_reason": "",
        "quality_gates_execution_status": "DETERMINISTIC",
    }

    root_after_first = MagicMock()
    root_after_first.values = dict(incomplete)
    root_after_first.next = ()
    root_after_first.tasks = ()

    root_after_resume = MagicMock()
    root_after_resume.values = dict(complete)
    root_after_resume.next = ()
    root_after_resume.tasks = ()

    verify_subgraph = MagicMock()
    verify_subgraph.nodes = {"static_repair": MagicMock(), "coverage_activation": MagicMock()}
    verify_subgraph.ainvoke = AsyncMock(return_value=complete)

    stale_plan = MagicMock()
    stale_plan.successor = "static_repair"
    stale_plan.target_graph = verify_subgraph
    stale_plan.config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": "verify:abc"}}

    graph = MagicMock()
    graph.checkpointer = MagicMock()
    graph.nodes = {"verify": MagicMock(subgraphs=[verify_subgraph])}
    graph.aupdate_state = AsyncMock()
    graph.ainvoke = AsyncMock(return_value=incomplete)
    graph.aget_state = AsyncMock(side_effect=[None, root_after_first, root_after_resume])

    with (
        patch(
            "workflows.checkpoint_resume.plan_static_repair_rewind",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume.static_repair_rewind_report",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume._resolve_nested_pending_subgraph",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume._find_crash_bookmark_from_history",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume.plan_stale_end_resume",
            AsyncMock(return_value=stale_plan),
        ),
        patch(
            "workflows.checkpoint_resume.resolve_opsx_repository_root",
            lambda path: path,
        ),
        patch(
            "workflows.checkpoint_resume.graph_repo_path_patch",
            lambda _state, _root: {},
        ),
    ):
        result = await invoke_checkpointed_graph(
            graph,
            initial_state={
                "change_id": "post-invoke-gap",
                "repository_path": str(tmp_path),
            },
            thread_id=thread_id,
            repo_root=tmp_path,
        )

    assert verify_impl_pipeline_complete(result)
    assert verify_subgraph.ainvoke.await_count == 1
    assert result.get("quality_gates_execution_status") == "DETERMINISTIC"


@pytest.mark.asyncio
async def test_terminal_active_cursor_run_collision_disables_stale_end_auto_resume():
    from types import SimpleNamespace

    from workflows.checkpoint_resume import plan_stale_end_resume

    snapshot = SimpleNamespace(
        next=(),
        values={
            "change_id": "external-client-notifications-pilot",
            "run_uuid": "exec-123",
            "verify_apply_block_reason": (
                "SDK_ACTIVE_RUN_CONFLICT: agent_id=agent-busy; outcome unknown"
            ),
        },
    )
    result = await plan_stale_end_resume(
        object(),
        config={"configurable": {"thread_id": "verify-impl:test"}},
        snapshot=snapshot,
    )
    assert result is None


@pytest.mark.asyncio
async def test_implementation_lane_paused_disables_stale_end_auto_resume():
    from types import SimpleNamespace

    from workflows.checkpoint_resume import plan_stale_end_resume
    from workflows.implementation.routing_contract import RUN_STATUS_PAUSED

    snapshot = SimpleNamespace(
        next=(),
        values={
            "change_id": "visual",
            "implementation_status": RUN_STATUS_PAUSED,
            "implementation_recovery_code": "DISPATCH_UNCHANGED_AFTER_HOP",
        },
    )
    result = await plan_stale_end_resume(
        object(),
        config={"configurable": {"thread_id": "verify-impl:visual"}},
        snapshot=snapshot,
    )
    assert result is None


@pytest.mark.asyncio
async def test_checkpoint_reset_plan_apply_implement_not_refused_by_lane_pause():
    """После wipe lane PAUSE не блокирует stale end, если plan JSON — forward APPLY_IMPLEMENT."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from workflows.checkpoint_resume import (
        _terminal_implementation_lane_pause,
        plan_stale_end_resume,
    )
    from workflows.implementation.routing_contract import (
        RUN_STATUS_PAUSED,
        STAGE_APPLY_IMPLEMENT,
    )

    snapshot = SimpleNamespace(
        next=(),
        values={
            "change_id": "visual",
            "implementation_status": RUN_STATUS_PAUSED,
            "implementation_recovery_code": "DISPATCH_UNCHANGED_AFTER_HOP",
            "implementation_plan": {
                "status": "RUNNING",
                "next_stage": STAGE_APPLY_IMPLEMENT,
                "next_slice": "SLICE-004",
                "live_execution_authorized": True,
                "plan_only": True,
            },
        },
    )
    assert _terminal_implementation_lane_pause(snapshot.values) is False

    stub_graph = MagicMock()
    stub_graph.nodes = {}
    with (
        patch(
            "workflows.checkpoint_resume._resolve_stale_end_on_snapshot",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume._infer_checkpoint_finished_node",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume._plan_stale_end_in_subgraph_wrappers",
            AsyncMock(return_value=None),
        ),
    ):
        result = await plan_stale_end_resume(
            stub_graph,
            config={"configurable": {"thread_id": "verify-impl:visual"}},
            snapshot=snapshot,
        )
    assert result is not None or _terminal_implementation_lane_pause(snapshot.values) is False


@pytest.mark.asyncio
async def test_stale_resume_running_apply_implement_continues_execute_not_plan_hop():
    """RUNNING + APPLY_IMPLEMENT после PAUSE на execute не должен снова идти в plan route."""
    from types import SimpleNamespace

    from langgraph.checkpoint.memory import MemorySaver
    from unittest.mock import AsyncMock, patch

    from workflows.checkpoint_resume import (
        _resolve_stale_end_on_snapshot,
        _routing_state_for_stale_implementation_resume,
    )
    from workflows.implementation.graph import (
        ImplementationHopAgents,
        build_implementation_graph,
    )
    from workflows.implementation.routing_contract import (
        RUN_STATUS_PAUSED,
        STAGE_APPLY_IMPLEMENT,
    )

    class _StubAgent:
        async def run(self, state):  # type: ignore[no-untyped-def]
            _ = state
            return {}

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
    graph = build_implementation_graph(agents, checkpointer=MemorySaver())
    snapshot = SimpleNamespace(
        next=(),
        values={
            "change_id": "visual",
            "implementation_status": RUN_STATUS_PAUSED,
            "implementation_recovery_code": "DISPATCH_UNCHANGED_AFTER_HOP",
            "implementation_plan": {
                "status": "RUNNING",
                "next_stage": STAGE_APPLY_IMPLEMENT,
                "next_slice": "SLICE-004",
                "live_execution_authorized": True,
                "plan_only": True,
            },
        },
    )
    routing_state = _routing_state_for_stale_implementation_resume(snapshot.values)
    config: dict[str, object] = {"configurable": {"thread_id": "verify-impl:visual"}}
    with patch(
        "workflows.checkpoint_resume._infer_checkpoint_finished_node",
        AsyncMock(return_value="implementation_execute_hop"),
    ):
        successor = await _resolve_stale_end_on_snapshot(
            graph,
            config,
            snapshot,
            routing_state,
        )
    assert successor == "implementation_execute_hop"
    assert successor != "implementation_route"
