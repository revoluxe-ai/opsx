"""Verify workflow graph smoke tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest.mock import AsyncMock, patch

import pytest

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.state import GraphState
from agents.verify.impl_verify.agent import ImplVerifyAgent
from agents.verify.impl_verify.schemas import VerifyImplAnswer
from agents.verify.scenario_coverage_verify.agent import ScenarioCoverageVerifyAgent
from agents.verify.scenario_coverage_verify.evidence_contract import (
    input_snapshot,
    trace_mappings,
)
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    trace_matrix_path,
)
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageRowReview,
    ScenarioCoverageVerifyAnswer,
)
from agents.verify.coverage_activation.agent import CoverageActivationAgent
from agents.implementation.apply.agent import ApplyAgent
from agents.implementation.apply.schemas import ApplyAnswer
from agents.repair.scenario_test_repair.agent import ScenarioTestRepairAgent
from agents.verify.group_error.agent import GroupErrorAgent
from agents.verify.repair_order.agent import RepairOrderAgent
from agents.verify.diagnostic.agent import DiagnosticAgent
from agents.verify.static_triage.agent import StaticTriageAgent
from agents.verify.test_repair.agent import TestRepairAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from agents.verify.bugbot.agent import BugBotAgent
from agents.verify.project_bug_hunt.agent import ProjectBugHuntAgent
from agents.verify.archive_verify.agent import ArchiveVerifyAgent
from agents.verify.security_review.agent import SecurityReviewAgent
from workflows.main_graph import build_main_graph
from workflows.main_graph import build_verify_hop_agents
from workflows.verify.graph import build_verify_graph
from workflows.verify.impl_graph import build_impl_verify_graph
from workflows.verify.green_loop import (
    APPLY_NODE,
    SCENARIO_TEST_REPAIR_NODE,
    VERIFY_APPLY_COMMIT_NODE,
)
from workflows.checkpoint_resume import invoke_checkpointed_graph
from workflows.verify.tests.stub_adapters import (
    StubGroupErrorAdapter,
    StubRepairOrderAdapter,
    StubDiagnosticAdapter,
    StubStaticTriageAdapter,
    StubTypingRepairAdapter,
    StubTestTriageAdapter,
    StubTestRepairAdapter,
    StubProjectBugHuntAdapter,
    StubBugBotAdapter,
    StubSecurityReviewAdapter,
    StubVerifyRepairAdapter,
)


@pytest.fixture(autouse=True)
def _synthetic_scenario_verifier_policy(monkeypatch):
    """Синтетические tmp_path-репозитории без OPSX policy vendor digest."""
    monkeypatch.setattr(
        "agents.verify.scenario_coverage_verify.agent.scenario_verifier_contract_digest",
        lambda *_args: "synthetic-scenario-verifier-v1",
    )


@pytest.fixture(autouse=True)
def _downstream_graph_tests_have_ready_plan_inputs(monkeypatch):
    """These synthetic graph tests exercise downstream hops, not plan evidence.

    The real readiness gate and its writer are covered independently by
    test_plan_artifact_readiness.py. Supply a valid preflight response here
    rather than silently treating missing fixture plan files as production input.
    """
    async def fixture_ready(_state):
        return {
            "plan_artifact_status": "READY",
            "plan_artifact_source_digest": "synthetic-test-source",
            "plan_artifact_result": {"status": "READY", "code": "TEST_FIXTURE_READY"},
        }

    monkeypatch.setattr(
        "workflows.verify.graph.plan_artifacts_preflight_node", fixture_ready,
    )


class _StubReviewAdapter(ABCAdapter):
    """Distinct review adapter marker for hop wiring smoke."""

    def __init__(self, *, allow_git_shell: bool = True) -> None:
        self.allow_git_shell = allow_git_shell

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
        tool_call_guard: Any = None,
        read_scope: Any = None,
    ) -> AgentResult:
        raise NotImplementedError("stub review adapter")


class _StubMutatingAdapter(ABCAdapter):
    """Distinct mutating adapter marker for apply hop wiring smoke."""

    def __init__(self, *, timeout_seconds: float = 60) -> None:
        self.timeout_seconds = timeout_seconds

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
        tool_call_guard: Any = None,
        read_scope: Any = None,
    ) -> AgentResult:
        raise NotImplementedError("stub mutating adapter")


class _StubImplAdapter(ABCAdapter):
    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        answer = VerifyImplAnswer(
            verdict="IMPL_OK",
            behavior_summary="stub ok",
            architecture_placement="stub ok",
            tasks_consistency="stub ok",
            gap_details=[],
            next_command="/opsx-compose-verification-plan change=stub profile=static_fast",
            local_hop_copy_block="Workspace: /tmp\nChange: stub",
        )
        return AgentResult(
            text=answer.model_dump_json(),
            status="finished",
            agent_id="agent-stub",
            run_id="run-stub",
        )


class _StubCoverageActivationAgent:
    """Deterministic PASS stub для graph smoke (без git inventory)."""

    async def run(self, state: Mapping[str, Any]) -> dict[str, Any]:
        change_id = str(state.get("change_id") or "stub")
        return {
            "coverage_activation_verdict": "COVERAGE_ACTIVATION_PASS",
            "coverage_activation_marker": "✅ COVERAGE_ACTIVATION_PASS",
            "coverage_activation_report_text": "stub coverage activation pass",
            "coverage_activation_answer": {
                "verdict": "COVERAGE_ACTIVATION_PASS",
                "summary": "stub",
                "approve_test_reactivation": [],
                "approve_static_paths": [],
                "block_reason": "",
                "test_coverage_gaps": [],
                "local_hop_copy_block": f"Change: {change_id}",
            },
            "coverage_activation_inventory": {"status": "NOT_APPLICABLE"},
            "coverage_activation_metrics": {},
            "coverage_activation_checks": {},
            "coverage_activation_complete": True,
        }


class _StubScenarioCoverageAdapter(ABCAdapter):
    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        change_id = str(state.get("change_id") or "stub")
        repo_root = Path(str(state.get("repository_path") or state.get("workspace") or "/tmp"))
        snapshot = input_snapshot(repo_root, change_id)
        mapping = trace_mappings(repo_root, change_id)
        scenario_id = next(iter(mapping), "stub-scenario")
        test_node = mapping.get(scenario_id, "tests/test_stub_scenario.py::test_stub")
        answer = ScenarioCoverageVerifyAnswer(
            verdict="SCENARIO_TEST_COVERAGE_PASS",
            plan_evidence_id="stub-plan-evidence",
            source_digest=str(snapshot["source_digest"]),
            row_reviews=[
                ScenarioCoverageRowReview(
                    scenario_id=scenario_id,
                    test_node_id=test_node,
                    assessment="SUPPORTED",
                    evidence="stub row supported",
                    when_proofs=[1],
                    outcome_proofs=[2],
                ),
            ],
            summary="stub scenario coverage pass",
            gaps=[],
            block_reason="",
            local_hop_copy_block=(
                f"Workspace: {repo_root}\nChange: {change_id}\nVerdict: PASS"
            ),
        )
        return AgentResult(
            text=answer.model_dump_json(),
            status="finished",
            agent_id="agent-stub-scenario",
            run_id="run-stub-scenario",
        )


class _StubScenarioTestRepairAgent:
    """Stub hop: no SDK; graph smoke tests do not reach scenario FAIL."""

    async def run(self, state: Mapping[str, Any]) -> dict[str, Any]:
        _ = state
        return {
            "scenario_test_repair_status": "NO_CHANGE",
            "scenario_test_repair_answer": {},
            "scenario_test_repair_changed_paths": [],
        }


class _StubApplyAdapter(ABCAdapter):
    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        answer = ApplyAnswer(
            status="NO_CHANGE",
            tasks_summary="stub apply skipped",
            gap_closure_summary="stub no file edits",
            files_changed=[],
            remaining_gaps=["stub gap"],
            next_command="/opsx-apply stub-change",
            local_hop_copy_block="Workspace: /tmp\nChange: stub",
        )
        return AgentResult(
            text=answer.model_dump_json(),
            status="finished",
            agent_id="agent-stub-apply",
            run_id="run-stub-apply",
        )


def _write_minimal_scenario_plan(change_dir: Path) -> None:
    """Минимальный durable plan + trace matrix для preflight ``ScenarioCoverageVerify``."""
    import json

    change_id = change_dir.name
    repo_root = change_dir.parent.parent.parent
    scenario_id = f"openspec/changes/{change_id}/specs/core/spec.md::Intent stub"
    test_node = "tests/test_stub_scenario.py::test_stub"
    spec = change_dir / "specs" / "core" / "spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "### Requirement: stub\n"
        "#### Scenario: Intent stub\n"
        "- **WHEN** stub event\n"
        "- **THEN** stub outcome\n",
        encoding="utf-8",
    )
    sc_dir = change_dir / "scenario-coverage"
    sc_dir.mkdir(parents=True, exist_ok=True)
    ensure_machine_receipt_dirs(sc_dir)
    (sc_dir / "plan.md").write_text(
        "SCENARIO_COVERAGE_PLAN:\n  SCENARIOS:\n"
        f"    - SCENARIO_ID: {scenario_id}\n",
        encoding="utf-8",
    )
    plan_receipt_path(sc_dir).write_text(
        json.dumps(
            {
                "PLAN_EVIDENCE_ID": "stub-plan-evidence",
                "SCENARIO_COVERAGE_RECEIPT": True,
                "PLAN_STATUS": "ready",
                "CHANGE_ID": change_id,
                "INPUTS": {
                    "SPEC_REVISION": [{
                        "path": "specs/core/spec.md",
                        "content_hash": hashlib.sha256(spec.read_bytes()).hexdigest(),
                    }],
                    "DESIGN_REVISION": {
                        "path": "design.md",
                        "content_hash": hashlib.sha256(b"design.md\n").hexdigest(),
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    trace_matrix_path(sc_dir).write_text(
        json.dumps({"entries": [{"scenario_id": scenario_id, "test_node_id": test_node}]}),
        encoding="utf-8",
    )
    tests_dir = repo_root / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    (tests_dir / "test_stub_scenario.py").write_text(
        "def test_stub():\n    assert True\n",
        encoding="utf-8",
    )
    for name in ("design.md", "tasks.md"):
        (change_dir / name).write_text(f"{name}\n", encoding="utf-8")
    from agents.common.scenario_files import materialize_scenario_files

    materialize_scenario_files(sc_dir)


def _stub_verify_agents() -> tuple[
    ImplVerifyAgent,
    ScenarioCoverageVerifyAgent,
    CoverageActivationAgent,
    ApplyAgent,
    _StubScenarioTestRepairAgent,
    GroupErrorAgent,
    RepairOrderAgent,
    DiagnosticAgent,
    StaticTriageAgent,
    TypingRepairAgent,
    TestTriageAgent,
    TestRepairAgent,
    ProjectBugHuntAgent,
    BugBotAgent,
    SecurityReviewAgent,
    ArchiveVerifyAgent,
    ABCAdapter,
]:
    repair_adapter = StubVerifyRepairAdapter()
    return (
        ImplVerifyAgent(_StubImplAdapter()),
        ScenarioCoverageVerifyAgent(_StubScenarioCoverageAdapter()),
        _StubCoverageActivationAgent(),  # type: ignore[arg-type]
        ApplyAgent(_StubApplyAdapter()),
        _StubScenarioTestRepairAgent(),
        GroupErrorAgent(StubGroupErrorAdapter()),
        RepairOrderAgent(StubRepairOrderAdapter()),
        DiagnosticAgent(StubDiagnosticAdapter()),
        StaticTriageAgent(StubStaticTriageAdapter()),
        TypingRepairAgent(StubTypingRepairAdapter()),
        TestTriageAgent(StubTestTriageAdapter()),
        TestRepairAgent(StubTestRepairAdapter()),
        ProjectBugHuntAgent(StubProjectBugHuntAdapter()),
        BugBotAgent(StubBugBotAdapter()),
        SecurityReviewAgent(StubSecurityReviewAdapter()),
        ArchiveVerifyAgent(_StubImplAdapter()),
        repair_adapter,
    )


def test_build_main_graph_is_impl_verify_not_bugbot_only() -> None:
    agents = _stub_verify_agents()
    main = build_main_graph(*agents)
    main_nodes = main.get_graph().nodes
    assert "verify" in main_nodes
    assert "impl_verify" not in main_nodes
    assert "static_repair" not in main_nodes
    assert "bugbot" not in main_nodes

    verify = build_verify_graph(*agents)
    verify_nodes = verify.get_graph().nodes
    assert "plan_artifacts_preflight" in verify_nodes
    assert "scenario_trace_materialize" not in verify_nodes
    assert "plan_artifacts_terminal" in verify_nodes
    assert "impl_verify" in verify_nodes
    assert "scenario_coverage_verify" in verify_nodes
    assert "coverage_activation" in verify_nodes
    assert "static_repair" in verify_nodes
    assert "verify_prepare_main_restart" in verify_nodes
    assert "verify_owner_attention" in verify_nodes
    assert APPLY_NODE in verify_nodes
    assert SCENARIO_TEST_REPAIR_NODE in verify_nodes
    assert VERIFY_APPLY_COMMIT_NODE in verify_nodes
    assert "project_bug" in verify_nodes
    assert "bugbot" in verify_nodes
    assert "security" in verify_nodes
    assert "archive_verify" in verify_nodes


def test_build_verify_hop_agents_uses_mutating_adapter_for_apply() -> None:
    """Apply hop получает mutating adapter, impl_verify — review-only."""
    review = _StubReviewAdapter(allow_git_shell=False)
    mutating = _StubMutatingAdapter(timeout_seconds=60)
    impl, scenario, coverage, apply, scenario_repair, archive = build_verify_hop_agents(
        review_adapter=review,
        apply_adapter=mutating,
    )
    assert impl._adapter is review
    assert scenario._adapter is review
    assert isinstance(coverage, CoverageActivationAgent)
    assert isinstance(scenario_repair, ScenarioTestRepairAgent)
    assert apply._adapter is mutating
    assert archive._adapter is review


@pytest.mark.asyncio
async def test_impl_verify_graph_static_repair_subgraph(tmp_path):
    change_id = "stub-change"
    change_dir = tmp_path / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True)
    (tmp_path / "opsx" / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "opsx" / "project").mkdir(parents=True, exist_ok=True)
    (change_dir / "proposal.md").write_text("# stub\n", encoding="utf-8")
    _write_minimal_scenario_plan(change_dir)

    import workflows.verify.gate_repair.subgraph as gate_repair_subgraph

    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "change_id": change_id,
        "run_uuid": "verify-graph-stub-run",
        "model": {"id": "composer-2.5", "params": []},
    }
    clean_preflight = {
        "change_id": change_id,
        "change_exists": True,
        "config_contract": {"status": "PASS"},
        "split_apply_completion": {"marker": "IMPL_OK", "status": "PASS"},
    }
    with (
        patch(
            "agents.verify.impl_verify.agent.collect_deterministic_preflight",
            return_value=clean_preflight,
        ),
        patch(
            "agents.verify.impl_verify.agent.preflight_implies_gaps",
            return_value=[],
        ),
        patch.object(
            gate_repair_subgraph,
            "run_quality_gates_node",
            new=AsyncMock(
                return_value={
                    "quality_gate_verdict": "PASS",
                    "quality_gate_diagnostics": {
                        "total_count": 0,
                        "pytest": [],
                        "pyright": [],
                        "mypy": [],
                    },
                    "quality_gates_execution_status": "DETERMINISTIC",
                    "quality_gate_marker": "✅ QUALITY_PASS",
                },
            ),
        ),
    ):
        graph = build_impl_verify_graph(*_stub_verify_agents())
        result = await graph.ainvoke(state)
    assert result["verdict"] == "IMPL_OK"
    assert result.get("scenario_coverage_verdict") == "SCENARIO_TEST_COVERAGE_PASS"
    assert result["mode"] == "impl"
    assert result.get("static_repair_marker") == "✅ STATIC_REPAIR_SKIP"
    assert result.get("verify_loop_status") == "VERIFY_OK"
    assert result["static_repair_status"] == "SKIPPED_PASS"
    assert result.get("analysis_result") is None


@pytest.mark.asyncio
async def test_impl_verify_graph_stops_on_impl_gaps_without_static_repair(tmp_path):
    """При ``verdict=IMPL_GAPS`` subgraph идёт в apply→commit, не в static_repair."""
    change_id = "gaps-change"
    change_dir = tmp_path / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True)
    (tmp_path / "opsx" / "project").mkdir(parents=True, exist_ok=True)

    gap_line = "Missing proposal.md, design.md, tasks.md, owner-contract.md"

    class _GapsImplAdapter(ABCAdapter):
        async def _run_turn(
            self,
            state: Mapping[str, Any],
            *,
            prompt: str,
            tools: Sequence[str],
            payload_model: Any = None,
        resume_agent_id: str | None = None,
        ) -> AgentResult:
            answer = VerifyImplAnswer(
                verdict="IMPL_GAPS",
                behavior_summary="stub gaps",
                architecture_placement="stub",
                tasks_consistency="stub",
                gap_details=[gap_line],
                next_command=f"/opsx-apply {change_id}",
                local_hop_copy_block="Workspace: /tmp\nChange: gaps",
            )
            return AgentResult(
                text=answer.model_dump_json(),
                status="finished",
                agent_id="agent-stub-gaps",
                run_id="run-stub-gaps",
            )

    agents = _stub_verify_agents()
    impl_agent = ImplVerifyAgent(_GapsImplAdapter())
    graph = build_impl_verify_graph(impl_agent, *agents[1:])
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "change_id": change_id,
        "model": {"id": "composer-2.5", "params": []},
        "verify_green_max_restarts": 1,
    }
    preflight = {
        "change_id": change_id,
        "change_exists": True,
        "config_contract": {"status": "FAIL"},
    }
    with (
        patch(
            "agents.verify.impl_verify.agent.collect_deterministic_preflight",
            return_value=preflight,
        ),
        patch(
            "agents.verify.impl_verify.agent.preflight_implies_gaps",
            return_value=[gap_line],
        ),
    ):
        result = await graph.ainvoke(state)

    assert result["verdict"] == "IMPL_GAPS"
    assert result.get("apply_status") == "NO_CHANGE"
    # ``route_after_apply``: NO_CHANGE без файлов → owner attention, commit hop не вызывается.
    assert result.get("commit_status") is None
    assert int(result.get("verify_green_restart_count") or 0) == 0
    assert result.get("verify_stop_reason") == "Owner Attention"
    assert gap_line in str(result.get("owner_attention") or "")


@pytest.mark.asyncio
async def test_impl_verify_graph_restart_skips_completed_impl_verify(tmp_path):
    """Повторный запуск с тем же thread_id не вызывает ``impl_verify`` снова."""
    change_id = "resume-change"
    change_dir = tmp_path / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True)
    (tmp_path / "opsx" / "project").mkdir(parents=True, exist_ok=True)

    calls = {"impl": 0}

    class _CountingImplAdapter(ABCAdapter):
        async def _run_turn(
            self,
            state: Mapping[str, Any],
            *,
            prompt: str,
            tools: Sequence[str],
            payload_model: Any = None,
        resume_agent_id: str | None = None,
        ) -> AgentResult:
            calls["impl"] += 1
            answer = VerifyImplAnswer(
                verdict="IMPL_GAPS",
                behavior_summary="stub gaps",
                architecture_placement="stub",
                tasks_consistency="stub",
                gap_details=["gap"],
                next_command=f"/opsx-apply {change_id}",
                local_hop_copy_block="Workspace: /tmp\nChange: resume",
            )
            return AgentResult(
                text=answer.model_dump_json(),
                status="finished",
                agent_id="agent-stub-resume",
                run_id="run-stub-resume",
            )

    agents = _stub_verify_agents()
    impl_agent = ImplVerifyAgent(_CountingImplAdapter())
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "change_id": change_id,
        "model": {"id": "composer-2.5", "params": []},
        "verify_green_max_restarts": 1,
    }
    preflight = {
        "change_id": change_id,
        "change_exists": True,
        "config_contract": {"status": "FAIL"},
    }
    checkpoint = tmp_path / "checkpoints.sqlite"
    thread_id = "verify-impl:resume-change"
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
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
            graph = build_impl_verify_graph(
                impl_agent,
                *agents[1:],
                checkpointer=saver,
            )
            first = await invoke_checkpointed_graph(
                graph,
                initial_state=state,
                thread_id=thread_id,
            )
            second = await invoke_checkpointed_graph(
                graph,
                initial_state=state,
                thread_id=thread_id,
            )

    assert calls["impl"] == 1
    assert first.get("verdict") == "IMPL_GAPS"
    assert first.get("commit_status") is None
    assert second.get("verify_stop_reason") == "Owner Attention"

