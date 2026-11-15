"""Verify graph: impl_verify → scenario coverage (построчный repair) → coverage_activation → gate_repair → review.

Соглашение имён: Python-callable **узлов** LangGraph — суффикс ``_node``; **сборщики**
compiled graph — ``build_*_graph``. Строковые id узлов в checkpoint (``impl_verify``, …)
не меняются.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.adapters.abc_adapter import ABCAdapter, ActiveCursorAgentRunError
from agents.implementation.apply.agent import ApplyAgent
from agents.implementation.apply.schemas import ApplyAnswer
from agents.repair.scenario_test_repair.agent import ScenarioTestRepairAgent
from agents.state import GraphState
from agents.verify.bugbot.agent import BugBotAgent
from agents.verify.diagnostic.agent import DiagnosticAgent
from agents.verify.group_error.agent import GroupErrorAgent
from agents.verify.archive_verify.agent import ArchiveVerifyAgent
from agents.verify.impl_verify.agent import ImplVerifyAgent
from agents.verify.scenario_coverage_verify.agent import ScenarioCoverageVerifyAgent
from agents.verify.project_bug_hunt.agent import ProjectBugHuntAgent
from agents.verify.project_bug_hunt.review_mapper import (
    bugbot_review_to_finding_report,
    project_bug_hunt_review_to_finding_report,
)
from agents.verify.repair_order.agent import RepairOrderAgent
from agents.verify.security_review.agent import SecurityReviewAgent
from agents.verify.security_review.review_mapper import (
    security_review_to_finding_report,
)
from agents.verify.static_triage.agent import StaticTriageAgent
from agents.verify.coverage_activation.agent import CoverageActivationAgent
from agents.verify.graph_validation import (
    resolve_scenario_coverage_route_target,
    resolve_impl_verify_route_target,
    resolve_coverage_activation_route_target,
    validate_impl_verify_route,
    validate_scenario_coverage_route,
    validate_coverage_activation_route,
)
from agents.verify.test_repair.agent import TestRepairAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from workflows.verify.apply_commit_node import run_verify_apply_commit
from check_workflow_receipt import (
    bugbot_workflow_receipt,
    infra_tests_workflow_receipt,
    project_bug_workflow_receipt,
    security_workflow_receipt,
)
from workflows.verify.infra_tests.pipeline import infra_tests_node
from workflows.verify.check_workflow import build_check_workflow_graph
from workflows.verify.cycle_checks import register_check_workflow_receipt
from workflows.verify.gate_repair.subgraph import build_gate_repair_graph
from workflows.repair.schemas import FixEvidence
from workflows.verify.green_loop import (
    IMPL_VERIFY_ENTRY_NODE,
    VERIFY_PREPARE_MAIN_RESTART_NODE,
    SCENARIO_COVERAGE_VERIFY_NODE,
    SCENARIO_TEST_REPAIR_NODE,
    COVERAGE_ACTIVATION_NODE,
    COVERAGE_ACTIVATION_TERMINAL_NODE,
    APPLY_NODE,
    PREPARE_IMPL_RESTART_NODE,
    VERIFY_APPLY_COMMIT_NODE,
    VERIFY_OWNER_ATTENTION_NODE,
    ARCHIVE_VERIFY_NODE,
    VERIFY_APPLY_GAP_SOURCE_ARCHIVE,
    VERIFY_APPLY_GAP_SOURCE_IMPL,
    VERIFY_APPLY_GAP_SOURCE_SCENARIO,
    is_gate_repair_workflow_green,
    prepare_impl_restart_node,
    patch_verify_shared_apply_loop,
    patch_establish_verify_check_circle,
    resolve_verify_prepare_main_restart_target,
    coverage_activation_terminal_stop_node,
    verify_owner_attention_stop_node,
    verify_prepare_main_restart_node,
    route_after_gate_repair_circle,
    route_after_bugbot_circle,
    route_after_infra_tests_circle,
    route_after_security_circle,
)
from workflows.verify.pipeline_routing import (
    VERIFY_SKIP_FORWARD_NODE,
    resolve_verify_skip_forward_target,
)
from workflows.verify.plan_artifacts import (
    plan_artifacts_preflight_node,
    route_after_plan_artifacts_preflight,
    plan_artifacts_terminal_node,
)
from workflows.verify.cycle_gate import (
    route_after_verify_cycle_gate,
    verify_cycle_gate_node,
    verify_finalize_green_cycle_node,
)
from workflows.verify.cycle_checks import CheckName
from workflows.verify.routing_log import (
    log_impl_verify_post_route,
    log_scenario_coverage_post_route,
    log_scenario_test_repair_post_route,
    log_static_repair_post_route,
)


def route_after_static_repair(state: GraphState) -> str:
    target = route_after_gate_repair_circle(state)
    log_static_repair_post_route(state, target=str(target))
    return target


async def impl_verify_node(state: GraphState, *, agent: ImplVerifyAgent) -> dict[str, Any]:
    """Узел ``impl_verify``; при ``IMPL_OK`` фиксирует зелёный impl check→apply круг."""
    patch = await agent.run(state)
    if str(patch.get("verdict") or "") == "IMPL_OK":
        merged: GraphState = {**state, **patch}
        patch = {**patch, **patch_establish_verify_check_circle(merged, CheckName.IMPL_VERIFY)}
    return patch


async def archive_verify_node(
    state: GraphState,
    *,
    agent: ArchiveVerifyAgent,
) -> dict[str, Any]:
    """Узел PHASE_ARCHIVE; при ``NO_GO`` готовит shared apply-loop."""
    patch = await agent.run(state)
    if str(patch.get("mode")) == "archive" and str(patch.get("verdict")) == "NO_GO":
        patch = {
            **patch,
            **patch_verify_shared_apply_loop(
                gap_source=VERIFY_APPLY_GAP_SOURCE_ARCHIVE,
                restart_target=IMPL_VERIFY_ENTRY_NODE,
            ),
        }
    if str(patch.get("mode")) == "archive" and str(patch.get("verdict")) == "GO":
        from pathlib import Path

        from workflows.artifact_paths import purge_change_runtime_artifacts
        from workflows.repo_paths import resolve_opsx_repository_root

        change_id = str(state.get("change_id") or "").strip()
        if change_id:
            repo = state.get("repository_path") or state.get("workspace")
            if repo:
                purge_change_runtime_artifacts(
                    resolve_opsx_repository_root(Path(str(repo))),
                    change_id,
                )
    return patch


def route_after_archive_verify(state: GraphState) -> str:
    """После archive_verify: только ``GO`` → END; любой не-GO → ``apply``."""
    if str(state.get("verdict")) == "GO":
        return END
    return APPLY_NODE


async def scenario_coverage_verify_node(
    state: GraphState,
    *,
    agent: ScenarioCoverageVerifyAgent,
    repair_agent: ScenarioTestRepairAgent | None = None,
    commit_adapter: ABCAdapter | None = None,
) -> dict[str, Any]:
    """Построчный check → repair → recheck. Пакетный repair-узел не вызывается."""
    if repair_agent is None:
        patch = await agent.run(state)
    else:
        from agents.verify.scenario_coverage_verify.row_cycle import run_scenario_row_walk

        patch = await run_scenario_row_walk(
            state,
            audit_agent=agent,
            repair_agent=repair_agent,
            commit_adapter=commit_adapter,
        )
    if str(patch.get("scenario_coverage_verdict") or "") == "SCENARIO_TEST_COVERAGE_PASS":
        merged: GraphState = {**state, **patch}
        patch = {
            **patch,
            **patch_establish_verify_check_circle(merged, CheckName.SCENARIO_COVERAGE),
        }
    return patch


async def coverage_activation_node(
    state: GraphState,
    *,
    agent: CoverageActivationAgent,
) -> dict[str, Any]:
    """Узел ``coverage_activation``: inventory + static/test activation."""
    from workflows.verify.routing_log import log_coverage_activation_node_start

    log_coverage_activation_node_start(state)
    return await agent.run(state)


async def apply_node(state: GraphState, *, agent: ApplyAgent) -> dict[str, Any]:
    """Shared ``apply`` (`/opsx-apply`): IMPL_GAPS, scenario FAIL, archive NO_GO."""
    try:
        patch = await agent.run(state)
    except ActiveCursorAgentRunError as exc:
        # The previous SDK run may still own writes. This is a PAUSE, not a fix
        # failure and never permission to re-send to another agent.
        gap_source = VERIFY_APPLY_GAP_SOURCE_IMPL
        reason = (
            f"SDK_ACTIVE_RUN_CONFLICT: agent_id={exc.agent_id}; "
            f"change_id={state.get('change_id') or 'unknown'}; "
            f"execution_id={state.get('run_uuid') or 'unknown'}; "
            "existing Cursor run may still mutate the workspace. "
            "No new send, commit, retry or PASS is authorized. "
            "Recovery: inspect existing run's terminal status and writes, "
            "reconcile workspace/checkpoint revision, then authorize a new execution."
        )
        answer = ApplyAnswer(
            status="BLOCKED",
            tasks_summary="Apply not executed: existing adapter run is active or unresolved.",
            gap_closure_summary=reason,
            files_changed=[],
            remaining_gaps=["SDK_ACTIVE_RUN_CONFLICT: prior apply outcome unknown"],
            next_command="STOP: reconcile the existing adapter run before a new execution",
            local_hop_copy_block=reason,
        )
        return {
            "apply_status": "BLOCKED",
            "apply_marker": "⏸ APPLY_PAUSED_SDK_ACTIVE_RUN",
            "apply_report_text": reason,
            "apply_answer": answer.model_dump(mode="json"),
            "verify_finding_fix": None,
            "verify_apply_gap_source": gap_source,
            "verify_apply_block_reason": reason,
        }
    verdict = str(state.get("verdict") or "")
    if verdict == "IMPL_GAPS":
        patch = {
            **patch,
            **patch_verify_shared_apply_loop(
                gap_source=VERIFY_APPLY_GAP_SOURCE_IMPL,
                restart_target=IMPL_VERIFY_ENTRY_NODE,
            ),
        }
    return patch


async def scenario_test_repair_node(
    state: GraphState,
    *,
    agent: ScenarioTestRepairAgent,
) -> dict[str, Any]:
    """Test-only repair после scenario FAIL; commit hop при ``CANDIDATE_PENDING_VERIFY``."""
    run_uuid = str(state.get("run_uuid") or "")
    merged: GraphState = {
        **state,
        "scenario_test_repair_authorized": True,
        "scenario_test_repair_graph_hop": True,
        "verify_scenario_repair_audit_run_uuid": run_uuid,
    }
    try:
        patch = await agent.run(merged)
    except ActiveCursorAgentRunError as exc:
        reason = (
            f"SDK_ACTIVE_RUN_CONFLICT: agent_id={exc.agent_id}; "
            f"change_id={state.get('change_id') or 'unknown'}; "
            f"execution_id={run_uuid or 'unknown'}; "
            "existing Cursor run may still mutate the workspace. "
            "No new send, commit, retry or PASS is authorized."
        )
        return {
            "scenario_test_repair_status": "BLOCKED",
            "scenario_test_repair_owner_attention": reason,
            "scenario_test_repair_changed_paths": [],
            "verify_apply_gap_source": VERIFY_APPLY_GAP_SOURCE_SCENARIO,
            "verify_apply_block_reason": reason,
        }
    status = str(patch.get("scenario_test_repair_status") or "")
    if status == "CANDIDATE_PENDING_VERIFY":
        answer = patch.get("scenario_test_repair_answer")
        changed = patch.get("scenario_test_repair_changed_paths")
        if not isinstance(changed, list):
            changed = answer.get("files_changed") if isinstance(answer, dict) else []
        if isinstance(changed, list) and changed:
            summary = ""
            if isinstance(answer, dict):
                summary = str(answer.get("summary") or "Scenario test repair candidate")
            fix = FixEvidence(
                status="FIXED",
                summary=summary,
                files_changed=[str(p) for p in changed],
                notes=(
                    "Candidate pending independent ScenarioCoverageVerify; "
                    "not SCENARIO_TEST_COVERAGE_PASS"
                ),
            ).model_dump(mode="json")
            patch = {
                **patch,
                "verify_finding_fix": fix,
                **patch_verify_shared_apply_loop(
                    gap_source=VERIFY_APPLY_GAP_SOURCE_SCENARIO,
                    restart_target=SCENARIO_COVERAGE_VERIFY_NODE,
                ),
                "verify_scenario_episode_used_repair": True,
                "verify_scenario_repair_run_uuid": run_uuid,
            }
        else:
            patch = {
                **patch,
                "verify_apply_gap_source": VERIFY_APPLY_GAP_SOURCE_SCENARIO,
            }
    else:
        patch = {
            **patch,
            "verify_apply_gap_source": VERIFY_APPLY_GAP_SOURCE_SCENARIO,
        }
    return patch


def route_after_apply(state: GraphState) -> str:
    """Never commit or retry without a valid Apply result and changed artifacts."""
    status = str(state.get("apply_status") or "")
    answer = state.get("apply_answer")
    changed = answer.get("files_changed") if isinstance(answer, dict) else None
    if status in ("APPLY_COMPLETE", "APPLY_PARTIAL") and isinstance(changed, list) and changed:
        return VERIFY_APPLY_COMMIT_NODE
    return VERIFY_OWNER_ATTENTION_NODE


def route_after_impl_verify(state: GraphState) -> str:
    """Маршрут после ``impl_verify``: apply, scenario subgraph, skip в activation или owner attention."""
    target = resolve_impl_verify_route_target(state)
    validate_impl_verify_route(state, target)
    log_impl_verify_post_route(state, target=target)
    return target


def route_after_scenario_coverage_verify(state: GraphState) -> str:
    """Маршрут после scenario audit: repair loop, impl re-check, coverage_activation или stop."""
    target = resolve_scenario_coverage_route_target(state)
    validate_scenario_coverage_route(state, target)
    log_scenario_coverage_post_route(state, target=target)
    return target


def route_after_scenario_test_repair(state: GraphState) -> str:
    """После scenario test repair: commit при candidate с файлами, иначе owner attention."""
    status = str(state.get("scenario_test_repair_status") or "")
    changed = state.get("scenario_test_repair_changed_paths")
    if (
        status == "CANDIDATE_PENDING_VERIFY"
        and isinstance(changed, list)
        and changed
    ):
        target = VERIFY_APPLY_COMMIT_NODE
    else:
        target = VERIFY_OWNER_ATTENTION_NODE
    log_scenario_test_repair_post_route(state, target=target)
    return target


def route_after_prepare_impl_restart(state: GraphState) -> str:
    """После commit: scenario walk не смотрит на старый ``apply_status``."""
    if str(state.get("verify_loop_status") or "") == "VERIFY_LOOP_CAP":
        return VERIFY_OWNER_ATTENTION_NODE
    gap_source = str(state.get("verify_apply_gap_source") or "")
    if gap_source == VERIFY_APPLY_GAP_SOURCE_SCENARIO:
        if state.get("scenario_row_walk_settled") is True:
            if str(state.get("scenario_coverage_verdict") or "") == "SCENARIO_TEST_COVERAGE_PASS":
                return COVERAGE_ACTIVATION_NODE
            return VERIFY_OWNER_ATTENTION_NODE
        repair_status = str(state.get("scenario_test_repair_status") or "")
        if repair_status != "CANDIDATE_PENDING_VERIFY":
            return VERIFY_OWNER_ATTENTION_NODE
    return "verify_prepare_main_restart"


def route_after_coverage_activation(state: GraphState) -> str:
    """Маршрут после coverage activation: всегда ``static_repair`` (quality gates)."""
    target = resolve_coverage_activation_route_target(state)
    validate_coverage_activation_route(state, target)
    return target


async def gate_repair_establish_circle_node(state: GraphState) -> dict[str, Any]:
    """После subgraph ``static_repair``: метка ``GATE_REPAIR`` на HEAD при зелёном круге."""
    if not is_gate_repair_workflow_green(state):
        return {}
    return patch_establish_verify_check_circle(state, CheckName.GATE_REPAIR)


async def verify_skip_forward_node(_state: GraphState) -> dict[str, Any]:
    """No-op: маршрутизация skip выполняется conditional edge."""
    return {}


def build_verify_graph(
    impl_agent: ImplVerifyAgent,
    scenario_coverage_agent: ScenarioCoverageVerifyAgent,
    coverage_activation_agent: CoverageActivationAgent,
    apply_agent: ApplyAgent,
    scenario_test_repair_agent: ScenarioTestRepairAgent,
    group_error_agent: GroupErrorAgent,
    repair_order_agent: RepairOrderAgent,
    diagnostic_agent: DiagnosticAgent,
    static_triage_agent: StaticTriageAgent,
    typing_repair_agent: TypingRepairAgent,
    test_triage_agent: TestTriageAgent,
    test_repair_agent: TestRepairAgent,
    project_bug_hunt_agent: ProjectBugHuntAgent,
    bugbot_agent: BugBotAgent,
    security_review_agent: SecurityReviewAgent,
    archive_verify_agent: ArchiveVerifyAgent,
    adapter: ABCAdapter,
    *,
    checkpointer=None,
):
    """Собрать verify-subgraph: impl_verify → scenario → coverage_activation → gate_repair → END."""
    gate_repair_graph = build_gate_repair_graph(
        group_error_agent,
        repair_order_agent,
        diagnostic_agent,
        static_triage_agent,
        typing_repair_agent,
        test_triage_agent,
        test_repair_agent,
        repair_adapter=adapter,
        checkpointer=checkpointer,
    )

    project_bug_receipt = project_bug_workflow_receipt()
    register_check_workflow_receipt(project_bug_receipt)
    project_bug_graph = build_check_workflow_graph(
        project_bug_hunt_agent,
        adapter,
        review_mapper=project_bug_hunt_review_to_finding_report,
        name_prefix="project_bug",
        checkpointer=checkpointer,
        receipt=project_bug_receipt,
    )
    bugbot_receipt = bugbot_workflow_receipt()
    register_check_workflow_receipt(bugbot_receipt)
    register_check_workflow_receipt(infra_tests_workflow_receipt())
    bugbot_graph = build_check_workflow_graph(
        bugbot_agent,
        adapter,
        review_mapper=bugbot_review_to_finding_report,
        name_prefix="bugbot",
        checkpointer=checkpointer,
        receipt=bugbot_receipt,
    )
    security_receipt = security_workflow_receipt()
    register_check_workflow_receipt(security_receipt)
    security_graph = build_check_workflow_graph(
        security_review_agent,
        adapter,
        review_mapper=security_review_to_finding_report,
        name_prefix="security",
        checkpointer=checkpointer,
        receipt=security_receipt,
    )

    restart_node = VERIFY_PREPARE_MAIN_RESTART_NODE

    finalize_node = "verify_finalize_green_cycle"

    builder = StateGraph(GraphState)
    builder.add_node("plan_artifacts_preflight", plan_artifacts_preflight_node)
    builder.add_node("plan_artifacts_terminal", plan_artifacts_terminal_node)
    _ = builder.add_node("verify_cycle_gate", verify_cycle_gate_node)
    _= builder.add_node(finalize_node, verify_finalize_green_cycle_node)
    _= builder.add_node(VERIFY_PREPARE_MAIN_RESTART_NODE, verify_prepare_main_restart_node)
    _= builder.add_node(PREPARE_IMPL_RESTART_NODE, prepare_impl_restart_node)
    _= builder.add_node(
        IMPL_VERIFY_ENTRY_NODE,
        partial(impl_verify_node, agent=impl_agent),
    )
    _= builder.add_node(
        SCENARIO_COVERAGE_VERIFY_NODE,
        partial(
            scenario_coverage_verify_node,
            agent=scenario_coverage_agent,
            repair_agent=scenario_test_repair_agent,
            commit_adapter=adapter,
        ),
    )
    builder.add_node(
        COVERAGE_ACTIVATION_NODE,
        partial(coverage_activation_node, agent=coverage_activation_agent),
    )
    builder.add_node(COVERAGE_ACTIVATION_TERMINAL_NODE, coverage_activation_terminal_stop_node)
    builder.add_node(APPLY_NODE, partial(apply_node, agent=apply_agent))
    builder.add_node(
        SCENARIO_TEST_REPAIR_NODE,
        partial(scenario_test_repair_node, agent=scenario_test_repair_agent),
    )
    builder.add_node(
        VERIFY_APPLY_COMMIT_NODE,
        partial(run_verify_apply_commit, repair_adapter=adapter),
    )
    builder.add_node(VERIFY_OWNER_ATTENTION_NODE, verify_owner_attention_stop_node)
    builder.add_node("static_repair", gate_repair_graph)
    builder.add_node("gate_repair_establish_circle", gate_repair_establish_circle_node)
    builder.add_node(VERIFY_SKIP_FORWARD_NODE, verify_skip_forward_node)
    builder.add_node("project_bug", project_bug_graph)
    builder.add_node("bugbot", bugbot_graph)
    builder.add_node("infra_tests", infra_tests_node)
    builder.add_node("security", security_graph)
    builder.add_node(
        ARCHIVE_VERIFY_NODE,
        partial(archive_verify_node, agent=archive_verify_agent),
    )

    builder.add_edge(START, "plan_artifacts_preflight")
    builder.add_conditional_edges(
        "plan_artifacts_preflight",
        route_after_plan_artifacts_preflight,
        {
            "verify_cycle_gate": "verify_cycle_gate",
            "plan_artifacts_terminal": "plan_artifacts_terminal",
        },
    )
    builder.add_edge("plan_artifacts_terminal", END)
    builder.add_conditional_edges(
        "verify_cycle_gate",
        route_after_verify_cycle_gate,
        {
            END: END,
            IMPL_VERIFY_ENTRY_NODE: IMPL_VERIFY_ENTRY_NODE,
        },
    )
    builder.add_conditional_edges(
        IMPL_VERIFY_ENTRY_NODE,
        route_after_impl_verify,
        {
            SCENARIO_COVERAGE_VERIFY_NODE: SCENARIO_COVERAGE_VERIFY_NODE,
            COVERAGE_ACTIVATION_NODE: COVERAGE_ACTIVATION_NODE,
            APPLY_NODE: APPLY_NODE,
            VERIFY_OWNER_ATTENTION_NODE: VERIFY_OWNER_ATTENTION_NODE,
        },
    )
    builder.add_conditional_edges(
        SCENARIO_COVERAGE_VERIFY_NODE,
        route_after_scenario_coverage_verify,
        {
            IMPL_VERIFY_ENTRY_NODE: IMPL_VERIFY_ENTRY_NODE,
            COVERAGE_ACTIVATION_NODE: COVERAGE_ACTIVATION_NODE,
            VERIFY_APPLY_COMMIT_NODE: VERIFY_APPLY_COMMIT_NODE,
            VERIFY_OWNER_ATTENTION_NODE: VERIFY_OWNER_ATTENTION_NODE,
        },
    )
    builder.add_conditional_edges(
        SCENARIO_TEST_REPAIR_NODE,
        route_after_scenario_test_repair,
        {
            VERIFY_APPLY_COMMIT_NODE: VERIFY_APPLY_COMMIT_NODE,
            VERIFY_OWNER_ATTENTION_NODE: VERIFY_OWNER_ATTENTION_NODE,
        },
    )
    builder.add_conditional_edges(
        COVERAGE_ACTIVATION_NODE,
        route_after_coverage_activation,
        {
            "static_repair": "static_repair",
        },
    )
    builder.add_edge(COVERAGE_ACTIVATION_TERMINAL_NODE, END)
    builder.add_conditional_edges(
        APPLY_NODE,
        route_after_apply,
        {
            VERIFY_APPLY_COMMIT_NODE: VERIFY_APPLY_COMMIT_NODE,
            VERIFY_OWNER_ATTENTION_NODE: VERIFY_OWNER_ATTENTION_NODE,
        },
    )
    builder.add_edge(VERIFY_APPLY_COMMIT_NODE, PREPARE_IMPL_RESTART_NODE)
    builder.add_conditional_edges(
        PREPARE_IMPL_RESTART_NODE,
        route_after_prepare_impl_restart,
        {
            VERIFY_OWNER_ATTENTION_NODE: VERIFY_OWNER_ATTENTION_NODE,
            COVERAGE_ACTIVATION_NODE: COVERAGE_ACTIVATION_NODE,
            restart_node: restart_node,
        },
    )
    builder.add_edge(VERIFY_OWNER_ATTENTION_NODE, END)

    builder.add_edge("static_repair", "gate_repair_establish_circle")
    builder.add_conditional_edges(
        "gate_repair_establish_circle",
        route_after_static_repair,
        {
            "verify_prepare_main_restart": "verify_prepare_main_restart",
            END: END,
            "project_bug": "project_bug",
            "bugbot": "bugbot",
            "security": "security",
            finalize_node: finalize_node,
        },
    )
    builder.add_conditional_edges(
        VERIFY_SKIP_FORWARD_NODE,
        resolve_verify_skip_forward_target,
        {
            "static_repair": "static_repair",
            "project_bug": "project_bug",
            "bugbot": "bugbot",
            "security": "security",
            finalize_node: finalize_node,
        },
    )

    def route_after_project_bug(state: GraphState) -> str:
        """После project_bug subgraph: green/pause gate, затем impl_verify или bugbot."""
        from workflows.stop_errors import VerifyStageStop
        from workflows.verify.green_loop import is_project_bug_workflow_green

        if state.get("verify_workflow_paused") is True:
            raise VerifyStageStop(
                "project bug workflow paused",
                code="VERIFY_PAUSED",
                stage="project_bug",
                evidence=str(state.get("verify_pause_reason") or ""),
            )
        if not is_project_bug_workflow_green(state):
            raise VerifyStageStop(
                "project bug workflow not green",
                code="PROJECT_BUG_NOT_GREEN",
                stage="project_bug",
                evidence=str(state.get("review") or ""),
            )
        if state.get("verify_workflow_code_changed") is True:
            return IMPL_VERIFY_ENTRY_NODE
        return "bugbot"

    _ = builder.add_conditional_edges(
        "project_bug",
        route_after_project_bug,
        {
            IMPL_VERIFY_ENTRY_NODE: IMPL_VERIFY_ENTRY_NODE,
            "bugbot": "bugbot",
        },
    )
    
    builder.add_conditional_edges(
        "bugbot",
        route_after_bugbot_circle,
        {
            "verify_prepare_main_restart": "verify_prepare_main_restart",
            "infra_tests": "infra_tests",
            finalize_node: finalize_node,
        },
    )
    builder.add_conditional_edges(
        "infra_tests",
        route_after_infra_tests_circle,
        {
            "verify_prepare_main_restart": "verify_prepare_main_restart",
            "security": "security",
            finalize_node: finalize_node,
        },
    )
    builder.add_conditional_edges(
        "security",
        route_after_security_circle,
        {
            "verify_prepare_main_restart": "verify_prepare_main_restart",
            END: END,
            finalize_node: finalize_node,
            ARCHIVE_VERIFY_NODE: ARCHIVE_VERIFY_NODE,
            "static_repair": "static_repair",
            "project_bug": "project_bug",
            "bugbot": "bugbot",
            VERIFY_SKIP_FORWARD_NODE: VERIFY_SKIP_FORWARD_NODE,
        },
    )
    builder.add_conditional_edges(
        ARCHIVE_VERIFY_NODE,
        route_after_archive_verify,
        {
            END: END,
            APPLY_NODE: APPLY_NODE,
        },
    )
    builder.add_edge(finalize_node, END)

    builder.add_conditional_edges(
        restart_node,
        resolve_verify_prepare_main_restart_target,
        {
            IMPL_VERIFY_ENTRY_NODE: IMPL_VERIFY_ENTRY_NODE,
            SCENARIO_COVERAGE_VERIFY_NODE: SCENARIO_COVERAGE_VERIFY_NODE,
        },
    )
    return builder.compile(checkpointer=checkpointer)
