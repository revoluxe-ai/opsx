"""Маршрутизация verify-цепочки: receipt → check workflow → restart или дальше.

Хвост impl-verify после gates (после ``project_bug`` subgraph):
``bugbot`` → ``infra_tests`` → ``security`` → ``verify_finalize_green_cycle``.
Узел ``archive_verify`` в эту цепочку не входит (только ``/opsx-verify mode=archive``).
"""

from __future__ import annotations

from langgraph.graph import END

from agents.state import GraphState
from workflows.stop_errors import VerifyStageStop
from workflows.verify.cycle_checks import CheckName, cycle_checks
from workflows.verify.green_loop import verify_pass_had_code_changes

VERIFY_PREPARE_MAIN_RESTART = "verify_prepare_main_restart"

VERIFY_SKIP_FORWARD_NODE = "verify_skip_forward"
VERIFY_FINALIZE_NODE = "verify_finalize_green_cycle"

_PIPELINE_AFTER: dict[CheckName, list[tuple[str, CheckName]]] = {
    CheckName.GATE_REPAIR: [
        ("project_bug", CheckName.PROJECT_BUG),
        ("bugbot", CheckName.BUGBOT),
        ("infra_tests", CheckName.INFRA_TESTS),
        ("security", CheckName.SECURITY),
    ],
    CheckName.PROJECT_BUG: [
        ("bugbot", CheckName.BUGBOT),
        ("infra_tests", CheckName.INFRA_TESTS),
        ("security", CheckName.SECURITY),
    ],
    CheckName.BUGBOT: [
        ("infra_tests", CheckName.INFRA_TESTS),
        ("security", CheckName.SECURITY),
    ],
    CheckName.INFRA_TESTS: [
        ("security", CheckName.SECURITY),
    ],
    CheckName.SECURITY: [],
}

_REQUIRED_FOR_FULL_CYCLE: tuple[CheckName, ...] = (
    CheckName.IMPL_VERIFY,
    CheckName.SCENARIO_COVERAGE,
    CheckName.GATE_REPAIR,
    CheckName.PROJECT_BUG,
    CheckName.BUGBOT,
    CheckName.INFRA_TESTS,
    CheckName.SECURITY,
)


def resolve_skip_forward_after(state: GraphState, after: CheckName) -> str:
    """Следующий узел цепочки после успешного ``after`` (без повторного запуска ``after``)."""
    _ = state
    steps = _PIPELINE_AFTER.get(after, [])
    return steps[0][0] if steps else VERIFY_FINALIZE_NODE


def check_receipt_valid(state: GraphState, check: CheckName) -> bool:
    """Рецепт check зелёный на текущем fingerprint (``cycle_checks.established``)."""
    return cycle_checks(state).is_established(check)


def route_verify_pipeline_hop(state: GraphState, hop: str) -> str:
    """Единый роутер цепочки gate_repair → project_bug → bugbot → infra_tests → security."""
    from workflows.verify.check_workflow import is_bugbot_workflow_green
    from workflows.verify.green_loop import (
        is_infra_tests_workflow_green,
        is_security_workflow_green,
    )

    if hop == "after_bugbot":
        if not is_bugbot_workflow_green(state):
            raise VerifyStageStop(
                "bugbot workflow not green",
                code="BUGBOT_NOT_GREEN",
                stage="bugbot",
                evidence=str(state.get("review") or ""),
                next_action="fix bugbot findings or repair hop",
            )
        if verify_pass_had_code_changes(state):
            return VERIFY_PREPARE_MAIN_RESTART
        return resolve_skip_forward_after(state, CheckName.BUGBOT)

    if hop == "after_infra_tests":
        if not is_infra_tests_workflow_green(state):
            raise VerifyStageStop(
                "infra_tests workflow not green",
                code="INFRA_TESTS_NOT_GREEN",
                stage="infra_tests",
                evidence=str(state.get("infra_tests_result") or ""),
                next_action="fix infra failures or env blocked",
            )
        if verify_pass_had_code_changes(state):
            return VERIFY_PREPARE_MAIN_RESTART
        return resolve_skip_forward_after(state, CheckName.INFRA_TESTS)

    if hop == "after_security":
        if not is_security_workflow_green(state):
            raise VerifyStageStop(
                "security workflow not green",
                code="SECURITY_NOT_GREEN",
                stage="security",
                evidence=str(state.get("review") or ""),
                next_action="fix security findings or repair hop",
            )
        if verify_pass_had_code_changes(state):
            return VERIFY_PREPARE_MAIN_RESTART
        return resolve_skip_forward_after(state, CheckName.SECURITY)

    msg = f"unknown verify pipeline hop: {hop!r}"
    raise ValueError(msg)


def all_verify_checks_established(state: GraphState) -> bool:
    """True, если все проверки цепочки (кроме FULL_VERIFY_CYCLE) зелёные на HEAD."""
    checks = cycle_checks(state)
    return all(checks.is_established(name) for name in _REQUIRED_FOR_FULL_CYCLE)


def resolve_verify_skip_forward_target(state: GraphState) -> str:
    """Узел ``verify_skip_forward``: пропуск gate_repair и далее по established."""
    # Re-enter the gate workflow to validate its own receipt; do not trust
    # a HEAD-free routing stamp as check-specific completion evidence.
    _ = state
    return "static_repair"
