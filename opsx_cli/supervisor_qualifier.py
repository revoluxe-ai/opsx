"""Qualifier супервизора: META vs PRODUCT и допуск solver-hop (детерминированно)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .supervisor_classify import FailureClassification, FailureOwner, classify_attempt

_RECEIPT_GATE_CODES = frozenset({
    "RECEIPT_NOT_ACCEPTED",
    "RECEIPT_POST_VALIDATE",
    "RECEIPT_PUBLISH_MISSING",
    "RECORD_CLI_TIMEOUT",
})

_ABNORMAL_ERROR_MARKERS = (
    "TimeoutExpired",
    "Traceback (most recent call last)",
    "KeyboardInterrupt",
    "VerifyImplProcessExitError",
    "ImplementationLaneTerminalError",
    "OpsxStageStop",
    "subprocess.",
    "SDK_BRIDGE_TIMEOUT",
    "AgentRunContractError",
    "ModuleNotFoundError",
    "ImportError",
    "RECEIPT_NOT_ACCEPTED",
    "RECEIPT_POST_VALIDATE",
    "RECORD_CLI_",
)


@dataclass(frozen=True, slots=True)
class QualifierVerdict:
    """Структурированный вердикт qualifier-hop перед solver-hop."""

    owner: str
    code: str
    stage: str
    fingerprint: str
    root_cause: str
    message: str
    fix_hop_allowed: bool

    def as_classification(self) -> FailureClassification:
        """Совместимость с fix-hop, ожидающим ``FailureClassification``."""
        owner = FailureOwner(self.owner)
        repair = self.fix_hop_allowed and owner in {FailureOwner.META, FailureOwner.HANG}
        return FailureClassification(
            owner=owner,
            code=self.code,
            stage=self.stage,
            fingerprint=self.fingerprint,
            message=self.message,
            repair_eligible=repair,
        )


def _workflow_abnormal_exit(
    record: Mapping[str, Any],
    *,
    hang_reason: str | None,
) -> bool:
    """Нештатный выход workflow (сбой процесса), не чистый product-verdict."""
    if hang_reason:
        return True
    process_status = str(record.get("process_status") or "").upper()
    if process_status == "FAILED":
        return True
    workflow_status = str(record.get("workflow_status") or "")
    if workflow_status in {"HANG", "UNKNOWN"}:
        return True
    if workflow_status and workflow_status != "WORKFLOW_TERMINAL":
        return True
    error = str(record.get("error") or "")
    if any(marker in error for marker in _ABNORMAL_ERROR_MARKERS):
        return True
    exit_code = record.get("exit_code")
    if process_status != "COMPLETED" and exit_code not in (None, 0):
        if workflow_status != "WORKFLOW_TERMINAL":
            return True
    return False


def _clean_product_acceptance_failure(record: Mapping[str, Any]) -> bool:
    """Штатный WORKFLOW_TERMINAL + verification FAIL: задача не закрыта (OPSX gap)."""
    if _workflow_abnormal_exit(record, hang_reason=None):
        return False
    if str(record.get("workflow_status") or "") != "WORKFLOW_TERMINAL":
        return False
    if str(record.get("verification_status") or "") != "FAIL":
        return False
    process_status = str(record.get("process_status") or "").upper()
    return process_status in {"", "COMPLETED"}


def _root_cause(
    owner: str,
    classification: FailureClassification,
    record: Mapping[str, Any],
    *,
    clean_product: bool,
) -> str:
    """Краткая причина для журнала qualifier (без секретов и токенов)."""
    err = str(record.get("error") or classification.message or "")
    err = re.sub(r"(token|api[_-]?key)\s*[=:]\s*\S+", r"\1=<redacted>", err, flags=re.I)
    if "opsx-record-slice-receipt-decision-cli" in err or "RECORD_CLI_TIMEOUT" in err:
        return (
            "OPSX receipt materialize CLI не завершился в бюджете subprocess "
            "(типично: повторный live shape/pytest в record CLI)."
        )
    if classification.code in _RECEIPT_GATE_CODES or "RECEIPT_NOT_ACCEPTED" in err:
        return "OPSX receipt gate отверг или не принял stage receipt — дефект процесса."
    if clean_product:
        return (
            "Штатный WORKFLOW_TERMINAL с отказом acceptance — OPSX gap: workflow "
            "не закрыл задачу; solver дорабатывает агентов/промпты/gates для "
            "следующего прогона (продукт — через workflow, не руками супервизора)."
        )
    if owner == FailureOwner.HANG.value:
        return "Процесс workflow завис без строки хода — META hang repair."
    if owner == FailureOwner.INFRA.value:
        return "Инфраструктура адаптера/окружения недоступна — solver-hop не откроется."
    if owner == FailureOwner.OWNER.value:
        return "Требуется явное решение Owner по семантике change."
    if _workflow_abnormal_exit(record, hang_reason=None):
        return (
            "Нештатный выход или обрыв workflow на hop — дефект процесса OPSX, "
            "не product gate."
        )
    return err[:1000] or classification.message[:1000]


def qualify_failure(
    record: Mapping[str, Any],
    *,
    log_tail: str,
    hang_reason: str | None = None,
) -> QualifierVerdict:
    """Классифицировать отказ workflow и решить, открывать ли solver-hop."""
    row = classify_attempt(record, log_tail=log_tail, hang_reason=hang_reason)
    clean_product = _clean_product_acceptance_failure(record)

    if row.owner in {FailureOwner.INFRA, FailureOwner.OWNER}:
        owner = row.owner.value
        fix_allowed = False
    elif hang_reason or row.owner == FailureOwner.HANG or _workflow_abnormal_exit(
        record, hang_reason=hang_reason
    ) or clean_product:
        owner = FailureOwner.HANG.value if hang_reason else FailureOwner.META.value
        fix_allowed = True
    else:
        owner = FailureOwner.META.value
        fix_allowed = True

    root = _root_cause(owner, row, record, clean_product=clean_product)
    return QualifierVerdict(
        owner=owner,
        code=row.code,
        stage=row.stage,
        fingerprint=row.fingerprint,
        root_cause=root,
        message=row.message,
        fix_hop_allowed=fix_allowed,
    )


def verdict_to_dict(verdict: QualifierVerdict) -> dict[str, Any]:
    """Сериализация для worker request / journal."""
    return asdict(verdict)