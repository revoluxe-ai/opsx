"""Маршрутизация «до зелёного» между под-workflow verify.

Счётчики:
- ``verify_outer_max_iterations`` (``outer_loop``, по умолчанию 20) — внутренний цикл
  gate-repair (gates → dispatch group) внутри одного прохода impl-verify.
- ``verify_green_max_restarts`` (по умолчанию 15) — полные возвраты на ``impl_verify`` после
  зелёного под-workflow, в котором менялись файлы.

Точка входа verify-subgraph: ``impl_verify``; scenario coverage — после ``IMPL_OK``.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END

from agents.state import GraphState
from workflows.stop_errors import VerifyStageStop
from workflows.verify.continuation import (
    VerifyContinuation,
    continuation_end_patch,
    continuation_restart_patch,
)
from workflows.verify.outer_loop import (
    is_verify_clean,
    reset_dispatcher_pass_patch,
)
from workflows.verify.cycle_checks import CheckName

IMPL_VERIFY_ENTRY_NODE = "impl_verify"
"""Первый агент главного verify-workflow (implementation verification)."""

VERIFY_PREPARE_MAIN_RESTART_NODE = "verify_prepare_main_restart"
"""Сброс inner loop после правок в check-workflow; далее conditional → ``impl_verify`` или scenario."""

SCENARIO_COVERAGE_VERIFY_NODE = "scenario_coverage_verify"
"""Scenario check-loop (``scenario_test_repair`` → commit) после ``IMPL_OK``."""

SCENARIO_TEST_REPAIR_NODE = "scenario_test_repair"
"""Test-only repair после ``SCENARIO_TEST_COVERAGE_FAIL`` (до shared commit hop)."""

COVERAGE_ACTIVATION_NODE = "coverage_activation"
"""Inventory + static/test activation после ``IMPL_OK``."""

COVERAGE_ACTIVATION_TERMINAL_NODE = "coverage_activation_terminal"
"""Terminal stop при BLOCKED/NEEDS_OWNER coverage activation."""

DEFAULT_VERIFY_GREEN_MAX_RESTARTS = 15

CONFIRMED_PRODUCTION_BUG = "confirmed production bug"
CONFIRMED_SECURITY_ISSUE = "confirmed security issue"

ROUTE_SUBGRAPH_END = "subgraph_end"
ROUTE_IMPL_RESTART = "prepare_impl_restart"
ROUTE_PROJECT_BUG = "begin_project_bug_workflow"
ROUTE_SECURITY = "begin_security_workflow"


def resolve_green_max_restarts(state: GraphState) -> int:
    """Лимит полных возвратов на ``impl_verify`` из state или default."""
    raw = state.get("verify_green_max_restarts")
    if isinstance(raw, int) and raw > 0:
        return raw
    return DEFAULT_VERIFY_GREEN_MAX_RESTARTS


def verify_pass_had_code_changes(state: GraphState) -> bool:
    """Были ли изменения файлов в текущем под-workflow (только ``verify_workflow_code_changed``)."""
    return state.get("verify_workflow_code_changed") is True


def commit_outcome_implies_code_changed(patch: dict[str, Any]) -> bool:
    """True, если patch commit-hop реально закоммитил файлы."""
    status = str(patch.get("commit_status") or "")
    if status not in ("COMMITTED", "committed"):
        commit = patch.get("verify_finding_commit_outcome")
        if isinstance(commit, dict):
            status = str(commit.get("commit_status") or "")
    files = patch.get("commit_files")
    if not isinstance(files, list):
        commit = patch.get("verify_finding_commit_outcome")
        if isinstance(commit, dict):
            files = commit.get("commit_files")
    return status in ("COMMITTED", "committed") and isinstance(files, list) and bool(files)


def patch_workflow_code_changed() -> dict[str, Any]:
    """Пометить, что текущий под-workflow изменил файлы."""
    return {"verify_workflow_code_changed": True}


def is_gate_repair_workflow_green(state: GraphState) -> bool:
    """Gate-repair зелёный: ``VERIFY_OK`` и чистые gates или терминал ``STATIC_REPAIR_SKIP``.

    После ``verify_mark_verify_ok`` в state часто ``static_repair_status=SKIPPED_PASS`` без
    повторного snapshot quality gates; без этой ветки ``route_after_static_repair`` уходит в
    END и цепочка ``project_bug`` → ``bugbot`` → ``security`` не стартует.
    """
    status = str(state.get("verify_loop_status") or "")
    if status == "VERIFY_LOOP_CAP":
        return False
    if status != "VERIFY_OK":
        return False
    if str(state.get("static_repair_status") or "") == "SKIPPED_PASS":
        return True
    return is_verify_clean(state)


def _confirmed_findings_in_review(
    review: dict[str, Any],
    *,
    classification_field: str,
    confirmed_value: str,
) -> bool:
    findings = review.get("findings")
    if not isinstance(findings, list):
        return False
    for row in findings:
        if isinstance(row, dict) and row.get(classification_field) == confirmed_value:
            return True
    return False


def is_project_bug_workflow_green(state: GraphState) -> bool:
    """Project bug check-repair зелёный: нет подтверждённых production bug в review."""
    if state.get("project_bug_hunt_status") is None:
        return True
    review = state.get("review")
    if not isinstance(review, dict):
        return True
    return not _confirmed_findings_in_review(
        review,
        classification_field="production_classification",
        confirmed_value=CONFIRMED_PRODUCTION_BUG,
    )


def is_infra_tests_workflow_green(state: GraphState) -> bool:
    """Infra-only pytest stage зелёный: PASS или PASS_NO_SCOPE."""
    status = str(state.get("infra_tests_status") or "").strip().upper()
    if not status:
        return True
    return status in {"PASS", "PASS_NO_SCOPE", "GREEN", "SKIPPED_PASS"}


def is_security_workflow_green(state: GraphState) -> bool:
    """Security check-repair зелёный: нет подтверждённых security issue в review."""
    if state.get("security_review_status") is None:
        return True
    review = state.get("review")
    if not isinstance(review, dict):
        return True
    return not _confirmed_findings_in_review(
        review,
        classification_field="security_classification",
        confirmed_value=CONFIRMED_SECURITY_ISSUE,
    )


async def verify_begin_child_workflow(_state: GraphState) -> dict[str, Any]:
    """Сбросить флаг изменений перед project-bug или security под-workflow."""
    return {"verify_workflow_code_changed": False}


async def prepare_impl_restart_node(state: GraphState) -> dict[str, Any]:
    """После зелёного под-workflow с правками: +1 restart или ``VERIFY_LOOP_CAP``."""
    count = int(state.get("verify_green_restart_count") or 0) + 1
    max_restarts = resolve_green_max_restarts(state)
    if count > max_restarts:
        return {
            "verify_green_restart_count": count,
            **continuation_end_patch(),
            "verify_loop_status": "VERIFY_LOOP_CAP",
            "verify_workflow_code_changed": False,
        }
    return {
        "verify_green_restart_count": count,
        **continuation_restart_patch(),
        "verify_workflow_code_changed": False,
    }


async def verify_subgraph_terminal(state: GraphState) -> dict[str, Any]:
    """Завершить вложенный subgraph; маршрут END только если restart не запланирован."""
    raw = state.get("verify_continuation")
    if raw is not None:
        cont = VerifyContinuation.model_validate(raw)
        if cont.next == "verify_prepare_main_restart":
            return {}
    return continuation_end_patch()


async def verify_prepare_main_restart_node(_state: GraphState) -> dict[str, Any]:
    """Перед повторным ``impl_verify``: сброс inner gate-loop и флага workflow."""
    return {
        "verify_outer_iteration": 0,
        "verify_loop_terminal": False,
        "verify_workflow_code_changed": False,
        **reset_dispatcher_pass_patch(),
    }


VERIFY_OWNER_ATTENTION_NODE = "verify_owner_attention"
"""Терминальный узел: cap / apply без прогресса — маркер для оператора."""

APPLY_NODE = "apply"
"""Apply-hop ``/opsx-apply`` после ``IMPL_GAPS`` (до commit)."""

VERIFY_APPLY_COMMIT_NODE = "verify_apply_commit"
"""Commit-hop после apply; далее ``prepare_impl_restart`` → restart target из state."""

VERIFY_APPLY_GAP_SOURCE_IMPL = "impl_verify"
VERIFY_APPLY_GAP_SOURCE_SCENARIO = "scenario_coverage_verify"
VERIFY_APPLY_GAP_SOURCE_ARCHIVE = "archive_verify"

ARCHIVE_VERIFY_NODE = "archive_verify"
"""PHASE_ARCHIVE hop после зелёного security (read-only archive audit)."""

PREPARE_IMPL_RESTART_NODE = "prepare_impl_restart"
"""Счётчик green restart перед повторным ``impl_verify``."""


def impl_gaps_restart_cap_reached(state: GraphState) -> bool:
    """True, если лимит полных apply→commit→restart циклов исчерпан."""
    count = int(state.get("verify_green_restart_count") or 0)
    return count >= resolve_green_max_restarts(state)


def resolve_verify_fail_apply_target(state: GraphState) -> str:
    """Общий исход fail-check: ``apply`` или ``verify_owner_attention`` при cap."""
    if impl_gaps_restart_cap_reached(state):
        return VERIFY_OWNER_ATTENTION_NODE
    return APPLY_NODE


def resolve_verify_fail_scenario_repair_target(state: GraphState) -> str:
    """После scenario FAIL: ``scenario_test_repair`` или ``verify_owner_attention`` при cap."""
    if impl_gaps_restart_cap_reached(state):
        return VERIFY_OWNER_ATTENTION_NODE
    return SCENARIO_TEST_REPAIR_NODE


def patch_verify_shared_apply_loop(
    *,
    gap_source: str,
    restart_target: str,
) -> dict[str, Any]:
    """Patch для shared ``apply`` → commit → возврат на ``restart_target`` check-node."""
    return {
        "verify_apply_restart_target": restart_target,
        "verify_apply_gap_source": gap_source,
    }


def patch_establish_verify_check_circle(state: GraphState, name: CheckName) -> dict[str, Any]:
    """Обёртка: ``verify_check_circle_code_changed`` + ``CycleChecks.establish_check_circle``."""
    from workflows.verify.cycle_checks import cycle_checks

    cc = cycle_checks(state)
    circle_changed = state.get("verify_check_circle_code_changed") is True
    return {
        "verify_check_circle_code_changed": False,
        **cc.establish_check_circle(name, circle_changed_code=circle_changed),
    }


def _impl_verify_gap_summary(state: GraphState) -> str:
    """Краткое описание gaps из результата ``impl_verify`` (answer или report)."""
    answer = state.get("answer")
    if isinstance(answer, dict):
        raw = answer.get("gap_details")
        if isinstance(raw, list):
            lines = [str(item).strip() for item in raw if str(item).strip()]
            if lines:
                return "; ".join(lines)
    marker = str(state.get("marker") or "").strip()
    if marker:
        return marker
    report = str(state.get("report_text") or "").strip()
    if report:
        return report[:500]
    return "IMPL_GAPS"


async def verify_owner_attention_stop_node(state: GraphState) -> dict[str, Any]:
    """Остановить verify-subgraph после cap или apply без прогресса; repair не запускать."""
    gap_source = str(state.get("verify_apply_gap_source") or "")
    scenario_verdict = str(state.get("scenario_coverage_verdict") or "")
    sc_fail = scenario_verdict == "SCENARIO_TEST_COVERAGE_FAIL"
    sc_unknown = scenario_verdict == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    collision = str(state.get("verify_apply_block_reason") or "").strip()
    if collision:
        summary = collision
    elif sc_unknown:
        # A blocked audit cannot be sent into Apply; preserve its exact
        # machine-readable reason instead of treating IMPL_OK as the failure.
        summary = _scenario_coverage_stop_summary(state)
    elif gap_source == VERIFY_APPLY_GAP_SOURCE_SCENARIO or (not gap_source and sc_fail):
        summary = _scenario_coverage_stop_summary(state)
        repair_status = str(state.get("scenario_test_repair_status") or "")
        if repair_status in ("PARTIAL", "BLOCKED", "NO_CHANGE"):
            answer = state.get("scenario_test_repair_answer") or {}
            remaining = answer.get("remaining_gap_ids") if isinstance(answer, dict) else []
            owner = str(state.get("scenario_test_repair_owner_attention") or "").strip()
            summary = (
                f"Scenario test repair {repair_status}; remaining={remaining}; "
                f"detail={owner or 'n/a'}; prior_audit={summary}"
            )[:2000]
        else:
            apply_status = str(state.get("apply_status") or "")
            if apply_status in ("APPLY_PARTIAL", "BLOCKED", "NO_CHANGE"):
                answer = state.get("apply_answer") or {}
                remaining = answer.get("remaining_gaps") if isinstance(answer, dict) else []
                unverified = answer.get("unverified_tests") if isinstance(answer, dict) else []
                summary = (
                    f"Scenario Apply {apply_status}; remaining={remaining}; "
                    f"unverified_tests={unverified}; prior_audit={summary}"
                )[:2000]
    else:
        summary = _impl_verify_gap_summary(state)
    return {
        "verify_stop_reason": "Owner Attention",
        "owner_attention": summary,
        **continuation_end_patch(),
    }


def resolve_verify_prepare_main_restart_target(state: GraphState) -> str:
    """Узел после ``verify_prepare_main_restart``: apply-loop target или ``impl_verify``."""
    restart = str(state.get("verify_apply_restart_target") or "").strip()
    if restart == SCENARIO_COVERAGE_VERIFY_NODE:
        return SCENARIO_COVERAGE_VERIFY_NODE
    return IMPL_VERIFY_ENTRY_NODE


def _scenario_coverage_stop_summary(state: GraphState) -> str:
    """Краткое описание gaps/blocked из ``scenario_coverage_answer``."""
    answer = state.get("scenario_coverage_answer")
    if isinstance(answer, dict):
        raw = answer.get("gaps")
        if isinstance(raw, list) and raw:
            parts: list[str] = []
            for item in raw[:8]:
                if isinstance(item, dict):
                    sid = str(item.get("scenario_id") or "").strip()
                    gap_class = str(item.get("gap_class") or "").strip()
                    if sid or gap_class:
                        parts.append(f"{sid} ({gap_class})".strip())
            if parts:
                return "; ".join(parts)
        block = str(answer.get("block_reason") or "").strip()
        if block:
            return block
        summary = str(answer.get("summary") or "").strip()
        if summary:
            return summary[:500]
    marker = str(state.get("scenario_coverage_marker") or "").strip()
    if marker:
        return marker
    return str(state.get("scenario_coverage_verdict") or "SCENARIO_TEST_COVERAGE_FAIL")


def _coverage_activation_stop_summary(state: GraphState) -> str:
    answer = state.get("coverage_activation_answer")
    if isinstance(answer, dict):
        block = str(answer.get("block_reason") or "").strip()
        if block:
            return block
        summary = str(answer.get("summary") or "").strip()
        if summary:
            return summary[:500]
    return str(state.get("coverage_activation_verdict") or "COVERAGE_ACTIVATION_BLOCKED")


async def coverage_activation_terminal_stop_node(state: GraphState) -> dict[str, Any]:
    """Legacy terminal + stop после BLOCKED/NEEDS_OWNER; resume через ``checkpoint_resume``."""
    return {
        "verify_stop_reason": "Coverage Activation",
        "owner_attention": _coverage_activation_stop_summary(state),
        **continuation_end_patch(),
    }


def make_route_after_gate_repair_workflow(
    *,
    has_project_bug_hunt: bool,
    has_security: bool,
):
    """Фабрика routing после gate-repair (узел ``static_repair`` agent)."""

    def route_after_gate_repair_workflow(state: GraphState) -> str:
        """После gate-repair: следующий workflow, restart impl или raise."""
        if not is_gate_repair_workflow_green(state):
            raise VerifyStageStop(
                "gate repair workflow not green",
                code="GATE_REPAIR_NOT_GREEN",
                stage="gate_repair",
                evidence=str(state.get("quality_gate_verdict") or ""),
            )
        if verify_pass_had_code_changes(state):
            return ROUTE_IMPL_RESTART
        if has_project_bug_hunt:
            return ROUTE_PROJECT_BUG
        if has_security:
            return ROUTE_SECURITY
        return ROUTE_SUBGRAPH_END

    route_after_gate_repair_workflow.__doc__ = (
        "После зелёного gate-repair без правок — project bug; с правками — impl_verify."
    )
    return route_after_gate_repair_workflow


def make_route_after_project_bug_workflow(*, has_security: bool):
    """Фабрика routing после ``bug_check_repair``."""

    def route_after_project_bug_workflow(state: GraphState) -> str:
        if not is_project_bug_workflow_green(state):
            raise VerifyStageStop(
                "project bug workflow not green",
                code="PROJECT_BUG_NOT_GREEN",
                stage="project_bug",
                evidence=str(state.get("review") or ""),
            )
        if verify_pass_had_code_changes(state):
            return ROUTE_IMPL_RESTART
        if has_security:
            return ROUTE_SECURITY
        return ROUTE_SUBGRAPH_END

    route_after_project_bug_workflow.__doc__ = (
        "После зелёного project-bug workflow: security, restart или END."
    )
    return route_after_project_bug_workflow


def route_after_security_workflow(state: GraphState) -> str:
    """После ``security_check_repair``: restart impl_verify или raise."""
    if not is_security_workflow_green(state):
        raise VerifyStageStop(
            "security workflow not green",
            code="SECURITY_NOT_GREEN",
            stage="security",
            evidence=str(state.get("review") or ""),
        )
    if verify_pass_had_code_changes(state):
        return ROUTE_IMPL_RESTART
    return ROUTE_SUBGRAPH_END


def route_after_gate_repair_circle(state: GraphState) -> str:
    if not is_gate_repair_workflow_green(state):
        raise VerifyStageStop(
            "gate repair circle not green",
            code="GATE_REPAIR_NOT_GREEN",
            stage="gate_repair",
            evidence=str(state.get("verify_loop_status") or ""),
        )
    if verify_pass_had_code_changes(state):
        return "verify_prepare_main_restart"
    from workflows.verify.cycle_checks import CheckName, cycle_checks

    if cycle_checks(state).is_established(CheckName.PROJECT_BUG):
        return "bugbot"
    return "project_bug"


def route_after_project_bug_circle(state: GraphState) -> str:
    if state.get("project_bug_hunt_status") is not None:
        review = state.get("review")
        if isinstance(review, dict):
            findings = review.get("findings")
            if isinstance(findings, list):
                for row in findings:
                    if (
                        isinstance(row, dict)
                        and row.get("production_classification") == CONFIRMED_PRODUCTION_BUG
                    ):
                        raise VerifyStageStop(
                            "confirmed production bug",
                            code="CONFIRMED_PRODUCTION_BUG",
                            stage="project_bug",
                            evidence=str(row),
                            owner="product",
                            next_action="fix production bug before verify continues",
                        )
    if verify_pass_had_code_changes(state):
        return "verify_prepare_main_restart"
    return "bugbot"


def route_after_bugbot_circle(state: GraphState) -> str:
    from workflows.verify.pipeline_routing import route_verify_pipeline_hop

    return route_verify_pipeline_hop(state, "after_bugbot")


def route_after_infra_tests_circle(state: GraphState) -> str:
    from workflows.verify.pipeline_routing import route_verify_pipeline_hop

    return route_verify_pipeline_hop(state, "after_infra_tests")


def route_after_security_circle(state: GraphState) -> str:
    from workflows.verify.pipeline_routing import route_verify_pipeline_hop

    return route_verify_pipeline_hop(state, "after_security")
