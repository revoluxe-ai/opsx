"""Repair-lifecycle performer agents.

These classes execute the AI-backed repair roles and the deterministic repair
terminalization steps. Workflow graphs only orchestrate state transitions and
interrupts around these performers.
"""

from __future__ import annotations

from typing import Any, Mapping

from langgraph.types import interrupt

from agents.adapters.abc_adapter import ABCAdapter
from agents.repair.helpers import (
    CLASSIFIER_TOOLS,
    RED_TEST_WORKER_TOOLS,
    as_state,
    with_receipt,
)
from agents.repair.execute_role import run_role
from model_config.agent_roles import AgentRole
from workflows.repair.red_operative_validation import validate_bug_team_red_operative
from workflows.repair.red_requirement_applicability import (
    build_red_applicability_pack,
    evaluate_red_applicability,
)
from workflows.repair.schemas import (
    ClassificationPayload,
    FindingOutcome,
    OwnerDecision,
    OwnerQuestion,
    RedEvidence,
    RepairState,
)


class RepairNodes:
    """AI-backed repair performers plus deterministic terminal steps."""

    def __init__(self, adapter: ABCAdapter):
        self.adapter = adapter

    async def classify(self, raw_state):
        state = as_state(raw_state)
        if state.active is None:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "CLASSIFIER requires active finding",
            }
        execution = await run_role(
            state,
            adapter=self.adapter,
            role=AgentRole.CLASSIFIER,
            payload_model=ClassificationPayload,
            tools=CLASSIFIER_TOOLS,
            payload={
                "finding": state.active.model_dump(mode="json"),
                "root_invariant": (
                    state.invariants[0].model_dump(mode="json")
                    if state.invariants
                    else None
                ),
            },
            instructions=(
                "Classify exactly one active finding before any implementation. "
                "Use discoverability.source_ids and priority_reads from INPUT CONTEXT "
                "for all read paths (OPSX §21.3); do not guess filenames or directories. "
                "Emit kind + chosen_route per Bug Team contract (see CLASSIFIER.md). "
                "IMPLEMENTATION_BUG → ROUTE_A (code defect in repo). "
                "NEW_REQUIREMENT → ROUTE_NEW_CHANGE (not a bug; new system requirement; "
                "needs /opsx-new, not local repair). "
                "ARCHIVED_PLANNING_BUG → ROUTE_ARCHIVED_DELTA (wrong accepted planning in an "
                "already archived change; fix only via a new delta/change, not archive edit). "
                "SPEC_BUG or SPEC_GAP → ROUTE_B (active/unarchived OpenSpec gap; /opsx-update). "
                "FALSE_POSITIVE → ROUTE_C (no defect). "
                "OUT_OF_SCOPE → ROUTE_D (handoff to ROUTE_TARGET_PROCESS_ID). "
                "INCONCLUSIVE → BLOCKED when evidence is insufficient. "
                "Do not edit tests or production."
            ),
        )
        if execution.payload is None:
            return with_receipt(
                state,
                execution.receipt,
                status="BLOCKED",
                current_stage="finish",
                error=execution.error,
            )
        payload = ClassificationPayload.model_validate(execution.payload)
        from bug_log.context import active_journal
        from bug_log.mint import record_hop

        status = "FALSE_POSITIVE" if payload.kind == "FALSE_POSITIVE" else "CLASSIFIED"
        if payload.kind in {"SPEC_BUG", "SPEC_GAP", "NEW_REQUIREMENT", "ARCHIVED_PLANNING_BUG"}:
            status = "ROUTED_TO_SPEC"
        await record_hop(
            active_journal(),
            bug_id=state.active.bug_id,
            summary=payload.kind,
            status=status,
            role="CLASSIFIER",
            hop="classify",
        )
        return with_receipt(
            state,
            execution.receipt,
            classification=payload,
            status="CLASSIFIED",
            current_stage="classify",
            error=None,
        )

    async def red(self, raw_state):
        state = as_state(raw_state)
        if state.active is None or state.classification is None:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "RED requires active finding + classification",
            }
        execution = await run_role(
            state,
            adapter=self.adapter,
            role=AgentRole.RED_TEST_WORKER,
            payload_model=RedEvidence,
            tools=RED_TEST_WORKER_TOOLS,
            payload={
                "finding": state.active.model_dump(mode="json"),
                "classification": state.classification.model_dump(mode="json"),
            },
            instructions=(
                "Author the smallest regression test that proves this bug on "
                "unchanged production code. Run the authoritative test with the "
                "run_pytest tool (single tests/ node id) and return RED evidence "
                "from that run: test_id, command, expected_failure, actual_failure, "
                "failure_match=true. Do not compute file hashes. Tests/fixtures may "
                "be written; production code must not be changed. Never manufacture "
                "a failure for PASS_ALREADY. Match the obligation harness profile "
                "(production_like vs preview); do not claim production invariants "
                "when the test toggles preview-only env inside the test body."
            ),
        )
        if execution.payload is None:
            return with_receipt(
                state,
                execution.receipt,
                status="BLOCKED",
                current_stage="finish",
                error=execution.error,
            )
        payload = RedEvidence.model_validate(execution.payload)
        if payload.status == "RED_CONFIRMED":
            operative = validate_bug_team_red_operative(
                payload,
                repo_root=state.repository_path,
            )
            if not operative.ok:
                error = "; ".join(operative.errors) or "RED operative gate failed"
                return with_receipt(
                    state,
                    execution.receipt,
                    red=payload,
                    status="BLOCKED",
                    current_stage="finish",
                    error=error,
                )
            req_fp = state.active.id if state.active is not None else ""
            pack = build_red_applicability_pack(
                evidence=payload,
                repo_root=state.repository_path,
                test_content_identity=operative.test_content_identity,
                pytest_result=operative.pytest_result,
                requirement_fingerprint=req_fp,
            )
            applicability = evaluate_red_applicability(pack)
            if not applicability.ok:
                error = "; ".join(applicability.errors) or "RED applicability failed"
                return with_receipt(
                    state,
                    execution.receipt,
                    red=payload,
                    status="BLOCKED",
                    current_stage="finish",
                    error=error,
                )
            return with_receipt(
                state,
                execution.receipt,
                red=payload,
                red_test_content_identity=operative.test_content_identity,
                status=payload.status,
                current_stage="red",
                error=None,
            )
        return with_receipt(
            state,
            execution.receipt,
            red=payload,
            red_test_content_identity=None,
            status=payload.status,
            current_stage="red",
            error=None,
        )

    def owner_gate(self, raw_state):
        state = as_state(raw_state)
        if state.active is None:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "owner gate requires active finding",
            }
        if state.owner_go is not None:
            approved = state.owner_go
            decision = "GO" if approved else "REJECT"
        else:
            question = OwnerQuestion(
                kind="PRODUCTION_GO",
                mission_id=state.mission_id,
                finding_id=state.active.id,
                question=(
                    f"RED is confirmed for {state.active.id}. "
                    "Allow local uncommitted implementation repair?"
                ),
                options=["GO", "REJECT", "STOP"],
            )
            raw = interrupt(question.model_dump(mode="json"))
            if isinstance(raw, bool):
                approved = raw
                decision = "GO" if raw else "REJECT"
            elif isinstance(raw, str):
                decision = raw.strip().upper()
                approved = decision == "GO"
            else:
                parsed = OwnerDecision.model_validate(raw)
                approved = parsed.approved
                decision = parsed.decision

        return {
            "owner_go": approved,
            "status": "OWNER_GO" if approved else "WAITING_OWNER",
            "current_stage": "owner_gate",
            "error": None if approved else f"Owner decision: {decision}",
        }

    def terminalize_nonrepair(self, raw_state):
        """Завершить finding для реализованных non-repair маршрутов (reject/block)."""
        state = as_state(raw_state)
        finding = state.active
        classification = state.classification
        if finding is None or classification is None:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "terminalize requires active classification",
            }
        terminal = {
            "FALSE_POSITIVE": "REJECTED_FALSE_POSITIVE",
            "INCONCLUSIVE": "BLOCKED_INCONCLUSIVE",
        }.get(classification.kind, "BLOCKED")
        if terminal == "BLOCKED":
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": (
                    f"terminalize_nonrepair cannot handle classification kind "
                    f"{classification.kind}; use a dedicated exit stub"
                ),
            }
        outcome = FindingOutcome(
            finding=finding,
            classification=classification,
            terminal_state=terminal,
        )
        rejects = [
            *state.rejects,
            {
                "finding_id": finding.id,
                "classification": classification.kind,
                "terminal_state": terminal,
                "reason": classification.reason,
            },
        ]
        return {
            "finished": [*state.finished, outcome],
            "rejects": rejects,
            "seen": list(dict.fromkeys([*state.seen, finding.id])),
            "active": None,
            "classification": None,
            "red": None,
            "red_test_content_identity": None,
            "fix": None,
            "green": None,
            "status": terminal,
            "current_stage": "complete",
            "revision": state.revision + 1,
        }

    def route_opsx_new(self, raw_state: Mapping[str, Any]) -> dict[str, Any]:
        """Заглушка маршрута NEW_REQUIREMENT → /opsx-new (не реализовано в runtime)."""
        _ = as_state(raw_state)
        raise NotImplementedError(
            "opsx_runtime: exit /opsx-new (new OpenSpec change for a new system "
            "requirement) is not implemented; run /opsx-new outside bug-repair"
        )

    def route_archived_delta(self, raw_state: Mapping[str, Any]) -> dict[str, Any]:
        """Заглушка ARCHIVED_PLANNING_BUG: только новая delta, архив не редактируется."""
        _ = as_state(raw_state)
        raise NotImplementedError(
            "opsx_runtime: archived planning delta (new OpenSpec change/delta only; "
            "archived change is not editable in place) is not implemented"
        )

    def route_opsx_update(self, raw_state: Mapping[str, Any]) -> dict[str, Any]:
        """Заглушка SPEC_BUG/SPEC_GAP → /opsx-update для активного change."""
        _ = as_state(raw_state)
        raise NotImplementedError(
            "opsx_runtime: exit /opsx-update (active change spec bug or spec gap) "
            "is not implemented; run /opsx-update outside bug-repair"
        )

    def route_out_of_scope(self, raw_state: Mapping[str, Any]) -> dict[str, Any]:
        """Заглушка OUT_OF_SCOPE: handoff к ROUTE_TARGET_PROCESS_ID."""
        _ = as_state(raw_state)
        raise NotImplementedError(
            "opsx_runtime: out-of-scope handoff (ROUTE_TARGET_PROCESS_ID) "
            "is not implemented in bug-repair"
        )

    def not_reproducing(self, raw_state):
        state = as_state(raw_state)
        if state.active is None:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "missing active finding",
            }
        outcome = FindingOutcome(
            finding=state.active,
            classification=state.classification,
            red=state.red,
            terminal_state="TEST_NOT_REPRODUCING",
        )
        return {
            "finished": [*state.finished, outcome],
            "seen": list(dict.fromkeys([*state.seen, state.active.id])),
            "active": None,
            "classification": None,
            "red": None,
            "red_test_content_identity": None,
            "fix": None,
            "green": None,
            "status": "TEST_NOT_REPRODUCING",
            "current_stage": "complete",
            "revision": state.revision + 1,
        }

    def complete(self, raw_state):
        state = as_state(raw_state)
        if state.active is None:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "complete requires active finding",
            }
        outcome = FindingOutcome(
            finding=state.active,
            classification=state.classification,
            red=state.red,
            fix=state.fix,
            green=state.green,
            terminal_state=(
                "FIXED"
                if state.green and state.green.status == "GREEN_CONFIRMED"
                else "BLOCKED"
            ),
            invariant_id=state.invariants[0].invariant_id
            if state.invariants
            else None,
        )
        return {
            "finished": [*state.finished, outcome],
            "seen": list(dict.fromkeys([*state.seen, state.active.id])),
            "active": None,
            "classification": None,
            "red": None,
            "red_test_content_identity": None,
            "fix": None,
            "green": None,
            "status": outcome.terminal_state,
            "current_stage": "complete",
            "revision": state.revision + 1,
            "error": state.error if outcome.terminal_state != "FIXED" else None,
        }

