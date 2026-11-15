"""CLI-вывод и признак завершения verify_impl pipeline (impl → gates → review).

Финальный stdout не включает отчёт фазы ``impl_verify``. Вердикт соответствия
плану остаётся во внутреннем state и не печатается как итог всего прогона.
"""

from __future__ import annotations

from typing import Any, Mapping

from workflows.implementation.routing import ImplementationLaneTerminalError
from workflows.implementation.routing_contract import (
    RUN_STATUS_FAILED,
    RUN_STATUS_PAUSED,
    format_current_implementation_procedure,
    implementation_lane_error_detail,
)
from workflows.verify.cycle_checks import CheckName, cycle_checks
from workflows.verify.routing_log import quality_gate_issue_lines


class VerifyImplIncompleteError(RuntimeError):
    """Прогон ``verify_impl`` закончился до quality gates и полного цикла.

    LangGraph при planning PAUSE делает штатный ``END``. Для CLI это ошибка:
    тихий stdout и ``SystemExit`` без traceback запрещены.
    """


class VerifyImplProcessExitError(RuntimeError):
    """CLI verify_impl: нормальный выход процесса допустим только при security pass.

    Любой иной терминальный статус графа (PAUSED, BLOCKED, незавершённый цикл,
    ``IMPL_OK`` без ``SECURITY_COMPLETE`` и т.д.) обязан завершаться исключением,
    а не ``SystemExit(0)``.
    """

_SECURITY_PASS_VERDICT = "SECURITY_COMPLETE"
_SECURITY_EVIDENCE_SOURCES = frozenset({"fresh_audit", "durable_pass"})


def verify_impl_security_pass(state: Mapping[str, Any]) -> bool:
    """True, если verify_impl завершился единственным допустимым CLI-исходом — security pass.

    Критерии совпадают с ``SecurityReviewAgent`` (``SECURITY_COMPLETE``, ноль
    подтверждённых findings, валидный ``check_receipt``).
    """
    status = str(state.get("security_review_status") or "").strip()
    if status != _SECURITY_PASS_VERDICT:
        return False
    review = state.get("review")
    if not isinstance(review, dict):
        return False
    if str(review.get("status") or "").strip() != _SECURITY_PASS_VERDICT:
        return False
    findings = review.get("findings")
    if isinstance(findings, list) and findings:
        return False
    try:
        confirmed = int(review.get("confirmed_security_issue_count") or 0)
    except (TypeError, ValueError):
        return False
    if confirmed != 0:
        return False
    receipt = state.get("check_receipt")
    if not isinstance(receipt, dict):
        return False
    source = str(receipt.get("source") or "").strip()
    if source not in _SECURITY_EVIDENCE_SOURCES:
        return False
    if source == "fresh_audit":
        if receipt.get("saved") is not True:
            return False
    elif not str(receipt.get("evidence_id") or receipt.get("origin_execution_id") or "").strip():
        return False
    return True


def _verify_impl_terminal_status_and_gate(state: Mapping[str, Any]) -> tuple[str, str]:
    """Статус и gate/reason для сообщения об ошибке, если security pass не достигнут."""
    impl_status = str(state.get("implementation_status") or "").strip()
    if impl_status in (RUN_STATUS_PAUSED, RUN_STATUS_FAILED):
        gate = str(state.get("implementation_recovery_code") or "").strip()
        detail = implementation_lane_error_detail(state)
        if detail and detail != gate:
            gate = f"{gate}:{detail}" if gate else detail
        return impl_status, gate or "(none)"
    for key in (
        "apply_scaffold_status",
        "apply_red_status",
        "apply_implement_status",
        "apply_green_status",
    ):
        hop = str(state.get(key) or "").strip()
        if hop == "BLOCKED":
            return "APPLY_BLOCKED", key
    sec = str(state.get("security_review_status") or "").strip()
    if sec and sec != _SECURITY_PASS_VERDICT:
        review = state.get("review")
        reason = ""
        if isinstance(review, dict):
            reason = str(review.get("block_reason") or "").strip()
        gate = f"security_review:{sec}"
        if reason:
            gate = f"{gate}:{reason}"
        return sec, gate
    if not verify_impl_pipeline_complete(state):
        stop = str(state.get("verify_stop_reason") or "").strip()
        planning = state.get("planning_readiness_result")
        code = ""
        if isinstance(planning, dict):
            code = str(planning.get("code") or "").strip()
        gate = stop or code or "PIPELINE_INCOMPLETE"
        return "INCOMPLETE", gate
    verdict = str(state.get("verdict") or "").strip() or "(none)"
    loop = str(state.get("verify_loop_status") or "").strip()
    qg = str(state.get("quality_gate_verdict") or "").strip()
    if not sec:
        return "SECURITY_NOT_RUN", verdict
    return "SECURITY_PASS_REQUIRED", f"verdict={verdict} loop={loop} qg={qg}"


def raise_unless_security_pass(
    state: Mapping[str, Any],
    *,
    change_id: str,
) -> None:
    """CLI: единственный штатный return без исключения — ``verify_impl_security_pass``."""
    if verify_impl_security_pass(state):
        return
    terminal_status, gate = _verify_impl_terminal_status_and_gate(state)
    raise VerifyImplProcessExitError(
        f"verify_impl process exit refused change_id={change_id!r}: "
        f"terminal_status={terminal_status!r} gate={gate!r}; "
        f"only {_SECURITY_PASS_VERDICT} with zero confirmed findings and "
        f"check_receipt evidence may return normally",
    )


def format_verify_impl_security_pass_cli_status(
    state: Mapping[str, Any],
    *,
    change_id: str,
) -> str:
    """Stdout после единственного допустимого CLI-исхода verify_impl."""
    receipt = state.get("check_receipt")
    source = "(none)"
    if isinstance(receipt, dict):
        source = str(receipt.get("source") or "(none)")
    return (
        f"verify_impl security pass change={change_id}\n"
        f"security_review_status={_SECURITY_PASS_VERDICT}\n"
        f"check_receipt_source={source}\n"
        f"confirmed_security_issue_count=0"
    )


def raise_if_implementation_lane_terminal(
    state: Mapping[str, Any],
    *,
    change_id: str,
) -> None:
    """CLI: PAUSED/FAILED implementation lane — ``raise``, не только exit code.

    В LangGraph ``PAUSED`` — штатный ``END`` (checkpoint/resume). Для
    ``run_verify_impl`` Owner ожидает loud failure с traceback. Текст
    ``error`` берётся из machine gate, если поле ``error`` пустое.
    """
    status = str(state.get("implementation_status") or "").strip()
    if status not in (RUN_STATUS_PAUSED, RUN_STATUS_FAILED):
        return
    code = str(state.get("implementation_recovery_code") or "")
    stage = str(state.get("implementation_stage") or "")
    slice_id = str(state.get("implementation_slice_id") or "")
    err = implementation_lane_error_detail(state)
    raise ImplementationLaneTerminalError(
        f"verify_impl terminal stop change_id={change_id!r}: "
        f"status={status!r} stage={stage!r} slice_id={slice_id!r} "
        f"recovery_code={code!r} error={err!r}",
    )


def verify_impl_pipeline_complete(state: Mapping[str, Any]) -> bool:
    """True, если verify_impl дошёл до quality gates или легитимного FULL_VERIFY_CYCLE skip.

    Сохранённый ``verdict=IMPL_OK`` после ``impl_verify`` без ``quality_gates_execution_status``
    или с ``verify_stop_reason`` не считается завершённым прогоном.
    """
    if str(state.get("verify_stop_reason") or "").strip():
        return False
    if str(state.get("quality_gates_execution_status") or "").strip():
        return True
    return cycle_checks(state).is_established(CheckName.FULL_VERIFY_CYCLE)


def format_verify_impl_terminal_cli_status(
    state: Mapping[str, Any],
    *,
    change_id: str,
) -> str:
    """Stdout после прогона, дошедшего до quality gates.

    Печатает статус цикла и вердикт pytest/mypy. Отчёт фазы сравнения кода
    с планом сюда не входит: он не говорит, что гейты зелёные.

    Args:
        state: Итоговое состояние verify-графа.
        change_id: Идентификатор OpenSpec change.

    Returns:
        Короткий многострочный статус для stdout.
    """
    loop = str(state.get("verify_loop_status") or "").strip() or "(none)"
    gate = str(state.get("quality_gate_verdict") or "").strip() or "(none)"
    gates_exec = str(state.get("quality_gates_execution_status") or "").strip() or "(none)"
    issues = quality_gate_issue_lines(state)
    issue_block = "\n".join(f"- {line}" for line in issues) if issues else "- none"
    owner = state.get("owner_attention")
    owner_line = ""
    if isinstance(owner, str) and owner.strip():
        snippet = owner.strip().replace("\n", " ")[:240]
        owner_line = f"\nowner_attention={snippet}"
    return (
        f"verify_impl finished change={change_id}\n"
        f"verify_loop_status={loop}\n"
        f"quality_gate_verdict={gate}\n"
        f"quality_gates_execution_status={gates_exec}"
        f"{owner_line}\n"
        f"remaining_gate_issues:\n{issue_block}"
    )


def format_verify_impl_incomplete_cli_status(
    state: Mapping[str, Any],
    *,
    change_id: str,
) -> str:
    """Краткий честный статус для stdout, когда полный IMPL-отчёт вводит в заблуждение."""
    verdict = str(state.get("verdict") or "-")
    stop = str(state.get("verify_stop_reason") or "").strip() or "(none)"
    impl_status = str(state.get("implementation_status") or "").strip() or "(none)"
    impl_recovery = str(state.get("implementation_recovery_code") or "").strip() or "(none)"
    impl_stage = str(state.get("implementation_stage") or "").strip() or None
    impl_slice = str(state.get("implementation_slice_id") or "").strip() or None
    procedure = format_current_implementation_procedure(impl_stage, impl_slice)
    impl_error = implementation_lane_error_detail(state) or "(none)"
    gates = str(state.get("quality_gates_execution_status") or "").strip() or "(not run)"
    ca = str(state.get("coverage_activation_verdict") or "").strip() or "-"
    planning_status = str(state.get("planning_readiness_status") or "").strip() or "(none)"
    planning = state.get("planning_readiness_result")
    planning_line = f"planning_readiness_status={planning_status}"
    if isinstance(planning, dict):
        planning_line += (
            f"\nplanning_code={planning.get('code') or '(none)'}"
            f"\nplanning_detail={planning.get('detail') or '(none)'}"
        )
    recovery = state.get("verify_recovery_contract")
    owner = state.get("owner_attention")
    owner_line = ""
    if isinstance(owner, str) and owner.strip():
        snippet = owner.strip().replace("\n", " ")[:240]
        owner_line = f"\nOwner note: {snippet}"

    next_instruction = (
        "Next: " + str(recovery) + "; re-run verify only after prerequisite closure."
        if isinstance(recovery, str) and recovery.strip()
        else "Next: re-run verify after checks, or use -r only for an explicit checkpoint reset."
    )
    return (
        f"⚠ verify_impl incomplete for change={change_id}\n"
        f"Saved verdict={verdict} (impl phase only — not a finished verify run).\n"
        f"verify_stop_reason={stop}\n"
        f"implementation_status={impl_status}\n"
        f"implementation_procedure={procedure}\n"
        f"implementation_recovery_code={impl_recovery}\n"
        f"implementation_error={impl_error}\n"
        f"quality_gates_execution_status={gates}\n"
        f"coverage_activation_verdict={ca}\n"
        f"{planning_line}"
        f"{owner_line}\n"
        f"{next_instruction}"
    )


def raise_if_verify_impl_incomplete(
    state: Mapping[str, Any],
    *,
    change_id: str,
) -> None:
    """Незавершённый прогон — исключение, не баннер в stdout.

    ``PAUSED``/``FAILED`` implementation lane поднимает
    ``ImplementationLaneTerminalError`` отдельно, до этого вызова.
    """
    if verify_impl_pipeline_complete(state):
        return
    status = str(state.get("implementation_status") or "").strip()
    if status in (RUN_STATUS_PAUSED, RUN_STATUS_FAILED):
        return
    raise VerifyImplIncompleteError(
        format_verify_impl_incomplete_cli_status(state, change_id=change_id),
    )
