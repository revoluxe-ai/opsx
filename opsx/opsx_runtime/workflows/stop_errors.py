"""Типизированная остановка workflow. Ошибка не заканчивается тихим END.

``END`` графа оставлен для подтверждённого успеха и штатного возврата в parent.
Любой отказ несёт код, стадию, evidence, владельца и следующий шаг.
"""

from __future__ import annotations


class OpsxStageStop(RuntimeError):
    """Остановка стадии. CLI печатает поля и завершает процесс с ненулевым кодом."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "STAGE_STOP",
        stage: str = "",
        evidence: str = "",
        owner: str = "",
        next_action: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.evidence = evidence or message
        self.owner = owner
        self.next_action = next_action


class VerifyStageStop(OpsxStageStop):
    """Verify остановился до зелёного итога. Следующая стадия не стартует."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "VERIFY_STOP",
        stage: str = "verify",
        evidence: str = "",
        owner: str = "verify",
        next_action: str = "",
    ) -> None:
        super().__init__(
            message,
            code=code,
            stage=stage,
            evidence=evidence,
            owner=owner,
            next_action=next_action,
        )


class InfraTestsStageError(VerifyStageStop):
    """Infra-стадия не зелёная: FAIL или среда не поднялась. Security не запускается."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "INFRA_TESTS_FAIL",
        evidence: str = "",
        owner: str = "infra_tests",
        next_action: str = "",
    ) -> None:
        super().__init__(
            message,
            code=code,
            stage="infra_tests",
            evidence=evidence,
            owner=owner,
            next_action=next_action,
        )


def format_stage_stop(exc: OpsxStageStop) -> str:
    """Текст для stderr: код, стадия, evidence, владелец, следующий шаг."""
    return (
        f"OPSX_STAGE_STOP code={exc.code} stage={exc.stage} owner={exc.owner}\n"
        f"evidence={exc.evidence}\n"
        f"next_action={exc.next_action}\n"
        f"{exc}"
    )
