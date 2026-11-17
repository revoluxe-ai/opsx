"""PP check-repair: injectable discovery agent → existing bug-team repair graph."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.abc_agent import ABCAgent
from agents.adapters.abc_adapter import ABCAdapter
from agents.state import GraphState
from agents.verify.project_bug_hunt.review_mapper import bugbot_review_to_finding_report
from workflows.repair.graph import build_bug_team_graph
from workflows.repair.schemas import FindingReport, ModelSelection, RepairState
from workflows.verify.check_repair_outcome import (
    derive_check_repair_hop_outcome,
    compute_repair_task_id,
    outcome_to_graph_patch,
    record_address_retry,
)
from workflows.verify.green_loop import patch_workflow_code_changed
from workflows.stop_errors import OpsxStageStop
from workflows.verify.repair_task_persistence import (
    persist_repair_attempt,
    save_latest_repair_state,
    try_reload_repair_state,
    write_repair_task_meta,
)

ReviewMapper = Callable[[Mapping[str, Any]], FindingReport]


def _maybe_raise_green_blocked_stage_stop(final: RepairState) -> None:
    """§8.1: FIX+commit, но GREEN не подтверждён — типизированный stop, не тихий END."""
    for outcome in final.finished:
        fix = outcome.fix
        green = outcome.green
        if fix is None or fix.status != "FIXED":
            continue
        if green is None:
            continue
        if green.status == "GREEN_CONFIRMED":
            continue
        err_text = str(final.error or "")
        if green.status != "BLOCKED" and "GREEN_STATUS" not in err_text:
            continue
        evidence_bits = [bit for bit in (err_text, green.notes) if bit]
        evidence = "; ".join(evidence_bits) or f"green.status={green.status}"
        raise OpsxStageStop(
            f"GREEN verification blocked ({outcome.finding.id})",
            code="GREEN_STATUS_BLOCKED",
            stage="green",
            evidence=evidence,
            owner="verify",
            next_action=(
                "opsx stop; opsx start после META "
                "и повторить opsx repair --bug '…' change_id=visual"
            ),
        )


def _repair_final_changed_files(final: RepairState) -> bool:
    """True, если bug-team завершил хотя бы один fix с ``files_changed``."""
    for outcome in final.finished:
        fix = outcome.fix
        if fix is not None and fix.files_changed:
            return True
    return False


def _repair_summary(final: RepairState) -> dict[str, Any]:
    """Build the owner-facing result without exposing prompts or raw reasoning."""

    files_changed: list[str] = []
    findings: list[dict[str, Any]] = []
    for outcome in final.finished:
        fix = outcome.fix
        red = outcome.red
        green = outcome.green
        if fix is not None:
            files_changed.extend(fix.files_changed)
        findings.append(
            {
                "finding": outcome.finding.finding,
                "location": outcome.finding.location,
                "terminal_state": outcome.terminal_state,
                "classification": (
                    outcome.classification.kind
                    if outcome.classification is not None
                    else None
                ),
                "classification_reason": (
                    outcome.classification.reason
                    if outcome.classification is not None
                    else None
                ),
                "red_status": red.status if red is not None else None,
                "red_test": red.test_id if red is not None else None,
                "fix_summary": fix.summary if fix is not None else None,
                "files_changed": fix.files_changed if fix is not None else [],
                "green_status": green.status if green is not None else None,
                "green_test": green.test_id if green is not None else None,
                "targeted_regressions": (
                    green.targeted_regressions if green is not None else []
                ),
            }
        )
    return {
        "status": final.status,
        "error": final.error,
        "mission_id": final.mission_id,
        "current_stage": final.current_stage,
        "finished_count": len(final.finished),
        "findings": findings,
        "files_changed": list(dict.fromkeys(files_changed)),
        "commit_status": final.commit_status,
        "commit_sha": final.commit_sha,
        "commit_subject": final.commit_subject,
        "hunt_count": len(final.hunts),
        "hunt_clear": bool(final.hunts and final.hunts[-1].hunt_result == "CLEAR"),
        "rejects_count": len(final.rejects),
        "output_dir": final.output_dir,
    }


def build_repair_state_from_graph_review(
    state: GraphState,
    report: FindingReport,
    *,
    repair_run_segment: str = "project-bug-repair",
    mission_prefix: str = "verify-pbh",
) -> RepairState:
    """Собрать ``RepairState`` для nested ``build_bug_team_graph`` из verify state."""
    change_id = str(state.get("change_id") or "verify").strip()
    workspace = str(state.get("workspace") or state["repository_path"])
    output_dir = (
        Path(workspace)
        / "opsx"
        / "runs"
        / "verify"
        / repair_run_segment
        / change_id
    )
    model_raw = state.get("model")
    model = (
        ModelSelection.model_validate(model_raw)
        if isinstance(model_raw, dict)
        else ModelSelection()
    )
    base = RepairState(
        mission_id=f"{mission_prefix}-{change_id}",
        repository_path=str(state["repository_path"]),
        workspace=workspace,
        isolated_workspace=False,
        model=model,
        source_review=report,
        output_dir=str(output_dir),
        owner_go=True,
        status="CREATED",
        current_stage="materialize",
    )
    repair_task_id = compute_repair_task_id(report)
    write_repair_task_meta(output_dir, repair_task_id=repair_task_id)
    return try_reload_repair_state(output_dir, report, base=base)


def make_bug_team_repair_hop(
    repair_adapter: ABCAdapter,
    *,
    review_mapper: ReviewMapper | None = None,
    checkpointer=None,
    repair_run_segment: str = "project-bug-repair",
    mission_prefix: str = "verify-pbh",
    state_key_prefix: str = "bug",
):
    """Repair-only hop: ожидает ``state['review']`` от discovery-агента."""
    mapper = review_mapper or bugbot_review_to_finding_report
    bug_team = build_bug_team_graph(
        repair_adapter,
        include_preflight=False,
        checkpointer=checkpointer,
    )

    async def bug_team_repair_hop(state: GraphState) -> dict[str, Any]:
        review_raw = state.get("review")
        if not isinstance(review_raw, dict):
            return {
                f"{state_key_prefix}_check_repair_status": "SKIPPED_NO_REVIEW",
                f"{state_key_prefix}_check_repair_error": (
                    "review dict missing in GraphState"
                ),
            }
        report = mapper(review_raw)
        from bug_log.context import active_journal
        from bug_log.mint import register_text_finding

        journal = active_journal()
        if journal is not None:
            for row in report.findings:
                bug_id = await register_text_finding(
                    journal,
                    lane="bugbot",
                    local_id=row.id,
                    location=row.location,
                    text=row.finding,
                    alias_scheme="review",
                )
                if bug_id:
                    row.bug_id = bug_id
        repair_state = build_repair_state_from_graph_review(
            state,
            report,
            repair_run_segment=repair_run_segment,
            mission_prefix=mission_prefix,
        )
        repair_task_id = compute_repair_task_id(report)
        result = await bug_team.ainvoke(repair_state.model_dump(mode="json"))
        final = RepairState.model_validate(result)
        save_latest_repair_state(final)
        _maybe_raise_green_blocked_stage_stop(final)
        outcome = derive_check_repair_hop_outcome(
            final,
            report=report,
            repair_task_id=repair_task_id,
        )
        if outcome.next_allowed == "ADDRESS_RETRY" and outcome.continuation_id:
            record_address_retry(
                final.output_dir,
                repair_task_id,
                outcome.continuation_id,
            )
        persist_repair_attempt(
            Path(final.output_dir),
            repair_task_id=repair_task_id,
            stage="check_repair",
            outcome=outcome.model_dump(mode="json"),
        )
        patch: dict[str, Any] = {
            f"{state_key_prefix}_check_repair_status": final.status,
            f"{state_key_prefix}_check_repair_mission_id": final.mission_id,
            f"{state_key_prefix}_check_repair_error": final.error,
            f"{state_key_prefix}_check_repair_finished_count": len(final.finished),
            f"{state_key_prefix}_check_repair_summary": _repair_summary(final),
        }
        patch.update(outcome_to_graph_patch(outcome, state_key_prefix=state_key_prefix))
        if _repair_final_changed_files(final):
            patch.update(patch_workflow_code_changed())
        return patch

    return bug_team_repair_hop


def build_check_repair_graph(
    discovery_agent: ABCAgent,
    repair_adapter: ABCAdapter,
    *,
    review_mapper: ReviewMapper | None = None,
    checkpointer=None,
):
    """Bugbot workflow 1:1: discovery agent → ``build_bug_team_graph`` repair loop."""
    repair_hop = make_bug_team_repair_hop(
        repair_adapter,
        review_mapper=review_mapper,
        checkpointer=checkpointer,
    )
    builder = StateGraph(GraphState)
    builder.add_node("discovery", discovery_agent.run)
    builder.add_node("bug_team_repair", repair_hop)
    builder.add_edge(START, "discovery")
    builder.add_edge("discovery", "bug_team_repair")
    builder.add_edge("bug_team_repair", END)
    return builder.compile(checkpointer=checkpointer)
