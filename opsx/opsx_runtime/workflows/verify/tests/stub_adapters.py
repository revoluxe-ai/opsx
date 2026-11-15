"""Stub adapters для verify graph tests."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.state import GraphState, BugBotAnswer
from agents.verify.bugbot.agent import BugBotAgent
from agents.verify.project_bug_hunt.agent import ProjectBugHuntAgent
from agents.verify.project_bug_hunt.schemas import ProjectBugHuntAnswer
from agents.verify.security_review.agent import SecurityReviewAgent
from agents.verify.security_review.schemas import SecurityReviewAnswer
from agents.verify.group_error.findings_from_diagnostics import (
    build_findings_from_diagnostics,
)
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    InputIntegrity,
    GroupErrorResult,
    TriageSummary,
)
from agents.verify.repair_order.schemas import RepairOrderAnswer, RepairOrderResult
from agents.verify.diagnostic.schemas import (
    DiagnosticAnswer,
    RepairUnitAnswer,
)
from agents.verify.static_triage.schemas import StaticTriageEvidenceRef, StaticTriageResult
from agents.verify.dispatch_unit.schemas import CausalRepairGroup, RepairUnitTriageResult
from agents.verify.dispatch_unit.queue_items import is_repair_unit_item
from agents.verify.test_interaction.schemas import TestInteractionResult
from agents.verify.test_triage.schemas import TestTriageResult
from agents.verify.test_repair.schemas import TestRepairResult
from agents.verify.typing_repair.schemas import TypingRepairResult


class StubGroupErrorAdapter(ABCAdapter):
    """Минимальный валидный triage result без LLM (все findings в UNGROUPED)."""

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
        if tools:
            raise AssertionError("Group Error Agent must run with tools=()")
        diagnostics = state.get("quality_gate_diagnostics")
        findings = build_findings_from_diagnostics(
            diagnostics if isinstance(diagnostics, dict) else None,
        )
        ungrouped = [f.finding_id for f in findings]
        body = GroupErrorResult(
            analysis_identity=AnalysisIdentity(
                change_id=change_id,
                identity_status="PARTIAL",
            ),
            input_integrity=InputIntegrity(status="COMPLETE", issues=[]),
            ungrouped_finding_ids=ungrouped,
            findings=findings,
            triage_summary=TriageSummary(
                findings_total=len(findings),
                ungrouped_total=len(ungrouped),
            ),
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="group-error-stub",
            run_id="run-group-error-stub",
        )


class StubRepairOrderAdapter(ABCAdapter):
    """Stable-order passthrough: groups then ungrouped, без LLM."""

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        if tools:
            raise AssertionError("Repair Order Agent must run with tools=()")
        raw = state.get("analysis_result")
        if not isinstance(raw, dict):
            raise ValueError("analysis_result required for StubRepairOrderAdapter")
        analysis = GroupErrorResult.model_validate(raw)
        ordered_unit_ids: list[str] = []
        for group in analysis.finding_groups:
            ordered_unit_ids.append(group.group_id)
        ordered_unit_ids.extend(analysis.ungrouped_finding_ids)
        body = RepairOrderAnswer(ordered_unit_ids=ordered_unit_ids)
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="repair-order-stub",
            run_id="run-repair-order-stub",
        )


class StubDiagnosticAdapter(ABCAdapter):
    """Anchor-only repair unit: первый ordered unit, остаток в remaining_queue."""

    _READ_TOOLS = frozenset({"read", "grep", "glob", "ls"})

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        if tools and frozenset(tools) != self._READ_TOOLS:
            raise AssertionError(
                f"Diagnostic Agent must use read-only tools {sorted(self._READ_TOOLS)}, got {tools!r}",
            )
        raw_ro = state.get("repair_order_result")
        if not isinstance(raw_ro, dict):
            raise ValueError("repair_order_result required for StubDiagnosticAdapter")
        repair_order = RepairOrderResult.model_validate(raw_ro)
        if not repair_order.ordered_units:
            raise ValueError("repair_order.ordered_units empty")
        head = repair_order.ordered_units[0]
        body = DiagnosticAnswer(
            status="DIAGNOSED",
            anchor_unit_id=head.unit_id,
            repair_unit=RepairUnitAnswer(
                ordered_positions=[head.position],
                affected_areas=["application_code"],
                root_cause_summary="stub anchor-only diagnosis",
                change_boundaries="stub boundaries",
            ),
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="diagnostic-stub",
            run_id="run-diagnostic-stub",
        )


class StubStaticTriageAdapter(ABCAdapter):
    """Детерминированная классификация без LLM: pyright/mypy → TYPING."""

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        if tools:
            raise AssertionError("Static Triage Agent must run with tools=()")
        item = state.get("dispatcher_current_item")
        if not is_repair_unit_item(item if isinstance(item, dict) else None):
            queue = state.get("dispatcher_pass_queue") or []
            idx = int(state.get("dispatcher_pass_index") or 0)
            if idx < len(queue) and is_repair_unit_item(queue[idx]):
                item = queue[idx]
        if isinstance(item, dict) and (
            is_repair_unit_item(item) or payload_model is RepairUnitTriageResult
        ):
            fids = list(item.get("finding_ids") or [])
            key = str(item.get("repair_unit_key") or "")
            anchor = str(item.get("anchor_unit_id") or (fids[0] if fids else ""))
            if "TWO_CAUSES" in prompt:
                mid = len(fids) // 2
                groups = [
                    CausalRepairGroup(
                        group_id="G-A",
                        finding_ids=fids[:mid],
                        classification="TYPING",
                        repair_route="static",
                        rationale="stub cause A",
                    ),
                    CausalRepairGroup(
                        group_id="G-B",
                        finding_ids=fids[mid:],
                        classification="TYPING",
                        repair_route="static",
                        rationale="stub cause B",
                    ),
                ]
            else:
                groups = [
                    CausalRepairGroup(
                        group_id="G-1",
                        finding_ids=fids,
                        classification="TYPING",
                        repair_route="static",
                        rationale="stub single cause",
                    ),
                ]
            body = RepairUnitTriageResult(
                agent="static_triage",
                repair_unit_key=key,
                anchor_finding_id=anchor,
                status="GROUPED",
                causal_groups=groups,
            )
            return AgentResult(
                text=body.model_dump_json(),
                status="finished",
                agent_id="static-triage-stub",
                run_id="run-static-triage-stub",
            )
        finding_id = "unknown"
        path = None
        line = None
        workflow = ""
        if isinstance(item, dict):
            finding_id = str(item.get("finding_id") or finding_id)
            path = item.get("path")
            line = item.get("line")
            workflow = str(item.get("repair_workflow") or "")
        classification: str = "UNCLEAR"
        if workflow.startswith("WF_PYRIGHT_") or workflow.startswith("WF_MYPY_"):
            classification = "TYPING"
        body = StaticTriageResult(
            finding_id=finding_id,
            classification=classification,
            evidence_refs=StaticTriageEvidenceRef(
                path=str(path) if path else None,
                line=int(line) if isinstance(line, int) else None,
                change_hunk_present=False,
            ),
            rationale="stub: static gate workflows map to TYPING when pyright/mypy.",
            change_slice_status="NOT_IN_DIFF",
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="static-triage-stub",
            run_id="run-static-triage-stub",
        )


class StubTypingRepairAdapter(ABCAdapter):
    """Stub typing repair: FIXED без files (commit skip)."""

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        item = state.get("dispatcher_current_item")
        finding_id = str((item or {}).get("finding_id") or "unknown")
        group_ids = (item or {}).get("group_finding_ids")
        if isinstance(group_ids, list) and group_ids:
            finding_id = str(group_ids[0])
        body = TypingRepairResult(
            finding_id=finding_id,
            status="FIXED",
            summary="stub typing fix",
            files_changed=[],
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="typing-repair-stub",
            run_id="run-typing-repair-stub",
        )


class StubTestTriageAdapter(ABCAdapter):
    """Stub test triage: pytest → LOGIC, marker wrong-test → TEST."""

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        allowed_tools = frozenset({"read"})
        if tools and frozenset(tools) - allowed_tools:
            raise AssertionError("Test Triage Agent allows only read tool in stub")
        item = state.get("dispatcher_current_item")
        if not is_repair_unit_item(item if isinstance(item, dict) else None):
            queue = state.get("dispatcher_pass_queue") or []
            idx = int(state.get("dispatcher_pass_index") or 0)
            if idx < len(queue) and is_repair_unit_item(queue[idx]):
                item = queue[idx]
        if isinstance(item, dict) and (
            is_repair_unit_item(item) or payload_model is RepairUnitTriageResult
        ):
            fids = list(item.get("finding_ids") or [])
            key = str(item.get("repair_unit_key") or "")
            anchor = str(item.get("anchor_unit_id") or (fids[0] if fids else ""))
            groups = [
                CausalRepairGroup(
                    group_id="G-1",
                    finding_ids=fids,
                    classification="TEST",
                    repair_route="test",
                    rationale="stub test unit triage",
                ),
            ]
            body = RepairUnitTriageResult(
                agent="test_triage",
                repair_unit_key=key,
                anchor_finding_id=anchor,
                status="GROUPED",
                causal_groups=groups,
            )
            return AgentResult(
                text=body.model_dump_json(),
                status="finished",
                agent_id="test-triage-stub",
                run_id="run-test-triage-stub",
            )
        finding_id = "unknown"
        observation = ""
        if isinstance(item, dict):
            finding_id = str(item.get("finding_id") or finding_id)
            observation = str(item.get("observation") or "")
        classification = "LOGIC"
        if "wrong-test" in observation.lower():
            classification = "TEST"
        body = TestTriageResult(
            finding_id=finding_id,
            classification=classification,
            evidence_refs=StaticTriageEvidenceRef(),
            rationale="stub test triage",
            change_slice_status="NOT_IN_DIFF",
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="test-triage-stub",
            run_id="run-test-triage-stub",
        )


class StubTestInteractionAdapter(ABCAdapter):
    """Stub: batch FAIL + single PASS → ORDER / test_repair."""

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        item = state.get("dispatcher_current_item")
        finding_id = str((item or {}).get("finding_id") or "F-0001")
        body = TestInteractionResult(
            finding_id=finding_id,
            classification="ORDER",
            repair_owner="test_repair",
            repro_request="ordered_sequence",
            rationale="stub interaction: order dependency",
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="test-interaction-stub",
            run_id="run-test-interaction-stub",
        )


class StubTestRepairAdapter(ABCAdapter):
    """Stub test repair: FIXED без files."""

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        item = state.get("dispatcher_current_item")
        finding_id = str((item or {}).get("finding_id") or "unknown")
        body = TestRepairResult(
            finding_id=finding_id,
            status="FIXED",
            summary="stub test fix",
            files_changed=[],
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="test-repair-stub",
            run_id="run-test-repair-stub",
        )


class StubProjectBugHuntAdapter(ABCAdapter):
    """READ_ONLY project bug hunt: HUNT_COMPLETE без findings."""

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        body = ProjectBugHuntAnswer(
            status="HUNT_COMPLETE",
            findings=[],
            confirmed_production_bug_count=0,
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="project-bug-hunt-stub",
            run_id="run-project-bug-hunt-stub",
        )


class StubBugBotAdapter(ABCAdapter):
    """Bugbot discovery stub: CLEAN без findings."""

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        body = BugBotAnswer(status="CLEAN", findings=[])
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="bugbot-stub",
            run_id="run-bugbot-stub",
        )


class StubSecurityReviewAdapter(ABCAdapter):
    """Security review stub: SECURITY_COMPLETE без findings."""

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        body = SecurityReviewAnswer(
            status="SECURITY_COMPLETE",
            findings=[],
            confirmed_security_issue_count=0,
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="security-review-stub",
            run_id="run-security-review-stub",
        )


class StubVerifyRepairAdapter(ABCAdapter):
    """Bug-team repair stub: не вызывается при CLEAN review (страховка для graph tests)."""

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        raise AssertionError("StubVerifyRepairAdapter must not run in green verify stubs")
