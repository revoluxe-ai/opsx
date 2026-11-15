"""Acceptance tests: repair unit group triage + sequential group repair."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Mapping, Sequence
from unittest.mock import patch

import pytest

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.state import GraphState
from agents.verify.diagnostic.schemas import (
    DiagnosticResult,
    DiagnosticSourceIdentity,
    RepairUnit,
)
from agents.verify.dispatch_unit.schemas import FindingMemberStatus
from agents.verify.dispatch_unit.validation import validate_repair_unit_triage_partition
from agents.verify.dispatch_unit.schemas import CausalRepairGroup, RepairUnitTriageResult
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)
from agents.verify.static_triage.agent import StaticTriageAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from agents.verify.test_repair.agent import TestRepairAgent
from workflows.verify.dispatch_cycle.subgraph import build_dispatch_cycle_subgraph
from workflows.verify.tests.stub_adapters import (
    StubStaticTriageAdapter,
    StubTestTriageAdapter,
    StubTypingRepairAdapter,
    StubTestRepairAdapter,
)


@contextmanager
def _patch_group_flow(*, verify_side_effect):
    """Pre-repair absent check отдельно от post-repair verify (не путать RESOLVED)."""
    with patch(
        "agents.verify.dispatch_unit.group_flow.verify_findings_already_absent",
        return_value=[],
    ), patch(
        "agents.verify.dispatch_unit.group_flow.verify_group_findings",
        side_effect=verify_side_effect,
    ):
        yield


class _CallCounter(ABCAdapter):
    """Считает вызовы LLM и делегирует stub."""

    def __init__(self, delegate: ABCAdapter) -> None:
        self.delegate = delegate
        self.calls: list[str] = []

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        name = getattr(payload_model, "__name__", str(payload_model))
        self.calls.append(name)
        return await self.delegate._run_turn(
            state,
            prompt=prompt,
            tools=tools,
            payload_model=payload_model,
            resume_agent_id=resume_agent_id,
        )


def _six_finding_state(tmp_path) -> GraphState:
    fids = [f"F-{i:04d}" for i in range(2, 8)]
    findings = [
        Finding(
            finding_id=fid,
            source_tool="pyright",
            observation=f"src/stroy/a.py:{10 + i}:1 - error: stub {fid}",
            repair_workflow="WF_PYRIGHT_TYPE",
        )
        for i, fid in enumerate(fids)
    ]
    analysis = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1", identity_status="PARTIAL"),
        input_integrity=InputIntegrity(status="COMPLETE", issues=[]),
        ungrouped_finding_ids=fids,
        findings=findings,
        triage_summary=TriageSummary(findings_total=6, ungrouped_total=6),
    )
    diagnostic = DiagnosticResult(
        status="DIAGNOSED",
        source_identity=DiagnosticSourceIdentity(identity_status="PARTIAL"),
        anchor_unit_id=fids[0],
        repair_unit=RepairUnit(
            unit_ids=fids,
            finding_ids=fids,
            root_cause_summary="shared typing root",
            change_boundaries="src/stroy/a.py only",
        ),
    )
    return {
        "verify_dispatch_mode": "repair_unit",
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "change_id": "c1",
        "analysis_result": analysis.model_dump(mode="json"),
        "diagnostic_result": diagnostic.model_dump(mode="json"),
        "quality_gate_diagnostics": {
            "pytest": [],
            "pyright": [],
            "mypy": [],
            "total_count": 0,
        },
    }


def _all_resolved(_state, *, finding_ids, files_changed, pytest_modules=None):
    return [
        FindingMemberStatus(finding_id=fid, status="RESOLVED", detail="test")
        for fid in finding_ids
    ]


@pytest.mark.asyncio
async def test_t1_common_cause_one_triage_one_repair(tmp_path):
    """T1: 6 findings, one cause → 1 triage + 1 repair."""
    triage_counter = _CallCounter(StubStaticTriageAdapter())
    typing_counter = _CallCounter(StubTypingRepairAdapter())
    graph = build_dispatch_cycle_subgraph(
        StaticTriageAgent(triage_counter),
        TypingRepairAgent(typing_counter),
        TestTriageAgent(StubTestTriageAdapter()),
        TestRepairAgent(StubTestRepairAdapter()),
        repair_adapter=None,
    )
    state = _six_finding_state(tmp_path)
    with _patch_group_flow(verify_side_effect=_all_resolved):
        result = await graph.ainvoke(state)

    triage_unit = [c for c in triage_counter.calls if c == "RepairUnitTriageResult"]
    assert len(triage_unit) == 1
    assert typing_counter.calls.count("TypingRepairResult") == 1
    assert result.get("dispatcher_pass_complete") is True
    assert len(result.get("dispatcher_resolved_finding_ids") or []) == 6


def _partial_resolved(_state, *, finding_ids, files_changed, pytest_modules=None):
    out = []
    for i, fid in enumerate(finding_ids):
        st = "RESOLVED" if i < 3 else "UNRESOLVED"
        out.append(FindingMemberStatus(finding_id=fid, status=st, detail="partial"))
    return out


@pytest.mark.asyncio
async def test_t3_partial_fix_not_all_resolved(tmp_path):
    """T3: 3/6 fixed → только 3 в resolved_finding_ids."""
    graph = build_dispatch_cycle_subgraph(
        StaticTriageAgent(_CallCounter(StubStaticTriageAdapter())),
        TypingRepairAgent(_CallCounter(StubTypingRepairAdapter())),
        TestTriageAgent(StubTestTriageAdapter()),
        TestRepairAgent(StubTestRepairAdapter()),
        repair_adapter=None,
    )
    with _patch_group_flow(verify_side_effect=_partial_resolved):
        result = await graph.ainvoke(_six_finding_state(tmp_path))
    resolved = set(result.get("dispatcher_resolved_finding_ids") or [])
    assert len(resolved) == 3
    assert result.get("dispatcher_pass_complete") is True


@pytest.mark.asyncio
async def test_t5_repair_fixed_verify_unresolved(tmp_path):
    """T5: repair FIXED, verify UNRESOLVED → finding не в resolved set."""

    def _all_unresolved(_state, *, finding_ids, files_changed):
        return [
            FindingMemberStatus(finding_id=fid, status="UNRESOLVED", detail="still broken")
            for fid in finding_ids
        ]

    graph = build_dispatch_cycle_subgraph(
        StaticTriageAgent(_CallCounter(StubStaticTriageAdapter())),
        TypingRepairAgent(_CallCounter(StubTypingRepairAdapter())),
        TestTriageAgent(StubTestTriageAdapter()),
        TestRepairAgent(StubTestRepairAdapter()),
        repair_adapter=None,
    )
    with _patch_group_flow(verify_side_effect=_all_unresolved):
        result = await graph.ainvoke(_six_finding_state(tmp_path))
    assert not result.get("dispatcher_resolved_finding_ids")


@pytest.mark.asyncio
async def test_t4_cascade_second_group_skips_repair(tmp_path):
    """T4: после fix группы 1 fresh check группы 2 → ALREADY_RESOLVED, без repair."""
    absent_calls = {"n": 0}

    def _already_absent(state, finding_ids):
        absent_calls["n"] += 1
        if absent_calls["n"] >= 2:
            return [
                FindingMemberStatus(finding_id=fid, status="ALREADY_RESOLVED", detail="cascade")
                for fid in finding_ids
            ]
        return [
            FindingMemberStatus(finding_id=fid, status="UNKNOWN", detail="needs repair")
            for fid in finding_ids
        ]

    class _TwoCauseAdapter(StubStaticTriageAdapter):
        async def _run_turn(self, state, *, prompt, tools, payload_model=None, resume_agent_id=None):
            item = state.get("dispatcher_current_item")
            if isinstance(item, dict) and item.get("item_kind") == "repair_unit":
                prompt = prompt + "\nTWO_CAUSES"
            return await super()._run_turn(
                state,
                prompt=prompt,
                tools=tools,
                payload_model=payload_model,
                resume_agent_id=resume_agent_id,
            )

    typing_counter = _CallCounter(StubTypingRepairAdapter())
    graph = build_dispatch_cycle_subgraph(
        StaticTriageAgent(_TwoCauseAdapter()),
        TypingRepairAgent(typing_counter),
        TestTriageAgent(StubTestTriageAdapter()),
        TestRepairAgent(StubTestRepairAdapter()),
        repair_adapter=None,
    )
    with patch(
        "agents.verify.dispatch_unit.group_flow.verify_group_findings",
        side_effect=_all_resolved,
    ), patch(
        "agents.verify.dispatch_unit.group_flow.verify_findings_already_absent",
        side_effect=_already_absent,
    ):
        await graph.ainvoke(_six_finding_state(tmp_path))
    assert typing_counter.calls.count("TypingRepairResult") == 1


@pytest.mark.asyncio
async def test_t9_mixed_unit_splits_to_explicit_routes(tmp_path):
    """T9: mixed static+test unit → heuristic split, два repair hop без LLM PAUSE."""
    fids = ["F-0001", "F-0002"]
    findings = [
        Finding(
            finding_id="F-0001",
            source_tool="pyright",
            observation="src/stroy/a.py:1:1 - error: a",
            repair_workflow="WF_PYRIGHT_TYPE",
        ),
        Finding(
            finding_id="F-0002",
            source_tool="pytest",
            observation="FAILED tests/test_x.py::test_y - assert False",
            repair_workflow="WF_PYTEST_FAILURE",
        ),
    ]
    analysis = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1", identity_status="PARTIAL"),
        input_integrity=InputIntegrity(status="COMPLETE", issues=[]),
        ungrouped_finding_ids=fids,
        findings=findings,
        triage_summary=TriageSummary(findings_total=2, ungrouped_total=2),
    )
    diagnostic = DiagnosticResult(
        status="DIAGNOSED",
        source_identity=DiagnosticSourceIdentity(identity_status="PARTIAL"),
        anchor_unit_id="F-0001",
        repair_unit=RepairUnit(
            unit_ids=fids,
            finding_ids=fids,
            root_cause_summary="mixed",
            change_boundaries="x",
        ),
    )
    state: GraphState = {
        "verify_dispatch_mode": "repair_unit",
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "change_id": "c1",
        "analysis_result": analysis.model_dump(mode="json"),
        "diagnostic_result": diagnostic.model_dump(mode="json"),
    }
    typing_counter = _CallCounter(StubTypingRepairAdapter())
    test_repair_counter = _CallCounter(StubTestRepairAdapter())
    triage_counter = _CallCounter(StubStaticTriageAdapter())
    graph = build_dispatch_cycle_subgraph(
        StaticTriageAgent(triage_counter),
        TypingRepairAgent(typing_counter),
        TestTriageAgent(StubTestTriageAdapter()),
        TestRepairAgent(test_repair_counter),
        repair_adapter=None,
    )
    with _patch_group_flow(verify_side_effect=_all_resolved):
        result = await graph.ainvoke(state)
    outcomes = list(result.get("dispatcher_outcomes") or [])
    assert outcomes
    last = outcomes[-1] if isinstance(outcomes[-1], dict) else {}
    triage_raw = (last.get("outcome") or {}).get("triage") or {}
    assert triage_raw.get("status") == "GROUPED"
    assert len(triage_raw.get("causal_groups") or []) == 2
    assert len([c for c in triage_counter.calls if c == "RepairUnitTriageResult"]) == 0
    routes = {
        g["finding_ids"][0]: g["repair_route"]
        for g in (triage_raw.get("causal_groups") or [])
        if g.get("finding_ids")
    }
    assert routes == {"F-0001": "static", "F-0002": "test"}
    assert result.get("dispatcher_pass_complete") is True


@pytest.mark.asyncio
async def test_t8_no_repeat_triage_on_unresolved(tmp_path):
    """T8: verify UNRESOLVED → один unit triage, один repair (не 6)."""

    def _never_resolved(_state, *, finding_ids, files_changed, pytest_modules=None):
        return [
            FindingMemberStatus(finding_id=fid, status="UNRESOLVED", detail="stuck")
            for fid in finding_ids
        ]

    triage_counter = _CallCounter(StubStaticTriageAdapter())
    typing_counter = _CallCounter(StubTypingRepairAdapter())
    graph = build_dispatch_cycle_subgraph(
        StaticTriageAgent(triage_counter),
        TypingRepairAgent(typing_counter),
        TestTriageAgent(StubTestTriageAdapter()),
        TestRepairAgent(StubTestRepairAdapter()),
        repair_adapter=None,
    )
    with _patch_group_flow(verify_side_effect=_never_resolved):
        await graph.ainvoke(_six_finding_state(tmp_path))
    assert len([c for c in triage_counter.calls if c == "RepairUnitTriageResult"]) == 1
    assert typing_counter.calls.count("TypingRepairResult") == 1


@pytest.mark.asyncio
async def test_t6_checkpoint_resume_skips_repeat_repair(tmp_path):
    """T6: interrupt после repair → resume → verify без второго typing repair."""
    from langgraph.checkpoint.memory import MemorySaver

    typing_counter = _CallCounter(StubTypingRepairAdapter())
    graph = build_dispatch_cycle_subgraph(
        StaticTriageAgent(_CallCounter(StubStaticTriageAdapter())),
        TypingRepairAgent(typing_counter),
        TestTriageAgent(StubTestTriageAdapter()),
        TestRepairAgent(StubTestRepairAdapter()),
        repair_adapter=None,
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "t6-repair-unit"}}
    with _patch_group_flow(verify_side_effect=_all_resolved):
        first = await graph.ainvoke(
            _six_finding_state(tmp_path),
            config=config,
            interrupt_before=["group_verify"],
        )
    assert typing_counter.calls.count("TypingRepairResult") == 1
    assert first.get("verify_finding_fix") is not None or first.get("verify_finding_repair_outcome")
    second = await graph.ainvoke(None, config=config)
    assert typing_counter.calls.count("TypingRepairResult") == 1
    assert second.get("dispatcher_pass_complete") is True


@pytest.mark.asyncio
async def test_t2_two_causes_two_repairs_one_triage(tmp_path):
    """T2: two causal groups → one triage, two repairs."""
    triage_stub = StubStaticTriageAdapter()

    class _TwoCauseAdapter(StubStaticTriageAdapter):
        async def _run_turn(self, state, *, prompt, tools, payload_model=None, resume_agent_id=None):
            item = state.get("dispatcher_current_item")
            if isinstance(item, dict) and item.get("item_kind") == "repair_unit":
                prompt = prompt + "\nTWO_CAUSES"
            return await super()._run_turn(
                state,
                prompt=prompt,
                tools=tools,
                payload_model=payload_model,
                resume_agent_id=resume_agent_id,
            )

    triage_counter = _CallCounter(_TwoCauseAdapter())
    typing_counter = _CallCounter(StubTypingRepairAdapter())
    graph = build_dispatch_cycle_subgraph(
        StaticTriageAgent(triage_counter),
        TypingRepairAgent(typing_counter),
        TestTriageAgent(StubTestTriageAdapter()),
        TestRepairAgent(StubTestRepairAdapter()),
        repair_adapter=None,
    )
    state = _six_finding_state(tmp_path)
    with _patch_group_flow(verify_side_effect=_all_resolved):
        result = await graph.ainvoke(state)

    assert len([c for c in triage_counter.calls if c == "RepairUnitTriageResult"]) == 1
    assert typing_counter.calls.count("TypingRepairResult") == 2
    assert result.get("dispatcher_pass_complete") is True


def test_t7_partition_validation():
    """T7: каждый finding ровно в одной группе."""
    result = RepairUnitTriageResult(
        repair_unit_key="k",
        anchor_finding_id="F-0001",
        status="GROUPED",
        causal_groups=[
            CausalRepairGroup(
                group_id="G1",
                finding_ids=["F-0001", "F-0002"],
                classification="TYPING",
                repair_route="static",
                rationale="r",
            ),
            CausalRepairGroup(
                group_id="G2",
                finding_ids=["F-0003"],
                classification="TYPING",
                repair_route="static",
                rationale="r2",
            ),
        ],
    )
    validate_repair_unit_triage_partition(result, expected_finding_ids=["F-0001", "F-0002", "F-0003"])
    with pytest.raises(ValueError):
        validate_repair_unit_triage_partition(
            result,
            expected_finding_ids=["F-0001", "F-0002", "F-0003", "F-0004"],
        )
