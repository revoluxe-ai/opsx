"""Контракты восстановления repair hop: сверка до retry и остановка без прогресса.

Не повторяет hop сам. Возвращает точный статус FAIL/PAUSE, когда побочный
эффект не сверен, причина повторяется, прогресса нет или набор находок растёт.
"""

from __future__ import annotations

from dataclasses import dataclass

PAUSE_UNRECONCILED = "PAUSE_RECOVERY_UNRECONCILED"
PAUSE_NO_PROGRESS = "PAUSE_NO_DEMONSTRABLE_PROGRESS"
PAUSE_EXPANDING = "PAUSE_EXPANDING_FINDINGS"
PAUSE_REGRESSION = "PAUSE_REGRESSION"
FAIL_RECURRING = "FAIL_CLOSED_RECURRING_CAUSE"
UNCERTAIN = "REPAIR_COMPLETION_UNCERTAIN"


@dataclass(frozen=True)
class RecoveryContract:
    """Машиночитаемый исход восстановления. ``reconciled`` обязателен до retry."""

    status: str
    message: str
    reconciled: bool


def contract_for_uncertain(
    *,
    repo_available: bool,
    inspected: bool,
    side_effects_match_baseline: bool | None = None,
    authorized_local_writes_only: bool | None = None,
) -> RecoveryContract:
    """Сверить неопределённый hop перед любым retry.

    ``inspected`` без сравнения со снимком не считается сверкой.
    Расхождение со снимком — ``PAUSE_RECOVERY_UNRECONCILED``, кроме случая когда
    доказаны только разрешённые in-scope локальные записи (без внешних эффектов).
    """

    if not repo_available:
        return RecoveryContract(
            UNCERTAIN,
            "Repair invocation ended ambiguously after possible side effects.",
            False,
        )
    if not inspected or side_effects_match_baseline is None:
        return RecoveryContract(
            PAUSE_UNRECONCILED,
            "Uncertain repair side effects were not compared to the baseline; retry is refused.",
            False,
        )
    if side_effects_match_baseline is False:
        if authorized_local_writes_only:
            return RecoveryContract(
                UNCERTAIN,
                (
                    "Uncertain repair changed only authorized in-scope local paths; "
                    "reconciled for retry without treating verification GREEN as Bugbot PASS."
                ),
                True,
            )
        return RecoveryContract(
            PAUSE_UNRECONCILED,
            "Uncertain repair changed the worktree versus the baseline; retry is refused.",
            False,
        )
    return RecoveryContract(
        UNCERTAIN,
        "Uncertain repair matched the worktree baseline; not a success and not retried blindly.",
        True,
    )


def refuse_unreconciled_retry(previous: RecoveryContract) -> RecoveryContract | None:
    """Запретить retry, пока предыдущий неопределённый hop не сверен."""

    if previous.reconciled:
        return None
    if previous.status in {UNCERTAIN, PAUSE_UNRECONCILED}:
        return RecoveryContract(
            PAUSE_UNRECONCILED,
            "Retry refused until uncertain side effects are reconciled.",
            False,
        )
    return None


def recurring_failure(prior_causes: tuple[str, ...], cause: str) -> RecoveryContract | None:
    """Остановиться, если та же причинная неуспешная терминальная метка повторилась."""

    token = (cause or "").strip()
    if not token:
        return None
    if token in prior_causes:
        return RecoveryContract(
            FAIL_RECURRING,
            f"Recurring causal failure {token}; stopping without another repair attempt.",
            True,
        )
    return None


def progress_contract(*, previous_token: str | None, current_token: str) -> RecoveryContract | None:
    """Требовать отличимый токен прогресса между раундами."""

    if previous_token is None:
        return None
    if previous_token == current_token:
        return RecoveryContract(
            PAUSE_NO_PROGRESS,
            "No demonstrable progress token change since the previous repair round.",
            True,
        )
    return None


def findings_contract(
    *,
    previous_unresolved: int | None,
    current_unresolved: int,
    previous_fingerprint: str | None,
    current_fingerprint: str | None,
) -> RecoveryContract | None:
    """Остановиться, если нерешённые находки расширяются или fingerprint регрессирует после прогресса."""

    if previous_unresolved is not None and current_unresolved > previous_unresolved:
        return RecoveryContract(
            PAUSE_EXPANDING,
            "Unresolved findings expanded versus the previous repair round.",
            True,
        )
    if (
        previous_fingerprint
        and current_fingerprint
        and previous_fingerprint != current_fingerprint
        and previous_unresolved is not None
        and current_unresolved >= previous_unresolved
        and previous_unresolved > 0
    ):
        return RecoveryContract(
            PAUSE_REGRESSION,
            "Review fingerprint changed while unresolved findings expanded.",
            True,
        )
    return None
