"""Детерминированная классификация отказа workflow (без LLM)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

_META_PATTERNS = (
    # Линия реализации встала, потому что агент не смог сдать принятый артефакт:
    # это дефект процесса (промпт, tool, валидатор, routing), а не решение Owner.
    r"ImplementationLaneTerminalError",
    r"APPLY_BLOCKED",
    r"artifact_missing",
    r"DESIGN_MACHINE_GATE_FAILED",
    r"DESIGN_INCOMPLETE",
    r"DESIGN_DIGEST_CONFLICT",
    r"INVALID_JSON",
    r"InvalidStateError",
    r"OpsxStageStop",
    r"READ_SCOPE_VIOLATION",
    r"SDK_BRIDGE_TIMEOUT",
    r"AgentRunContractError",
    r"VerifyImplProcessExitError",
    r"VerifyImplIncompleteError",
    r"PYTEST_STALL",
    r"PYTEST_SUITE_TIMEOUT",
    r"GATE_REPAIR_NO_PROGRESS",
    r"GREEN_TEST_RUNNER_ENV",
    r"ModuleNotFoundError:.+pytest",
    r"No module named ['\"]pytest['\"]",
    # workflow_pytest_defer не должен требовать matrix до scenario_trace_materialize
    r"test_trace_matrix\.json",
    r"not a valid JSON",
    r"ModuleNotFoundError:.+opsx",
    r"ImportError:.+opsx",
    # Receipt materialize (Node CLI / subprocess wall clock) — процесс OPSX, не PRODUCT gate.
    r"subprocess\.TimeoutExpired",
    r"TimeoutExpired",
    r"opsx-record-slice-receipt-decision-cli",
    r"RECORD_CLI_TIMEOUT",
    r"RECORD_CLI_FAILED",
    r"RECEIPT_NOT_ACCEPTED",
    r"RECEIPT_POST_VALIDATE",
)
_INFRA_PATTERNS = (
    r"PROJECT_ADOPTION_REQUIRED",
    r"NOT_INSTALLED",
    r"RUNTIME_NOT_INSTALLED",
    r"CURSOR_PROVIDER_UNAVAILABLE",
    # Provider identity/authority/recovery ambiguity is not a process
    # defect that an LLM solver may rewrite and retry without evidence.
    r"CODEX_EXECUTION_OUTCOME_UNKNOWN",
    r"CODEX_PENDING_WRITE_RECOVERY_REQUIRED",
    r"CODEX_WORKSPACE_REPLAY_IDENTITY_MISMATCH",
    r"CODEX_CROSS_PROVIDER_ACTIVE_DISPATCH",
    r"CODEX_PROVIDER_IDENTITY_CONFLICT",
    r"CODEX_RESUME_NOT_PROVEN",
    r"CODEX_MODEL_AUTHORITY_MISMATCH",
    r"CODEX_WRITE_SCOPE_ENFORCEMENT_REQUIRED",
    r"CODEX_SOURCE_REVISION_CHANGED",
    r"CODEX_APP_SERVER_RPC_FRAME_TOO_LARGE",
    r"CODEX_APP_SERVER_DISCONNECTED",
    r"CODEX_APP_SERVER_TIMEOUT",
    r"CODEX_TURN_SQLITE_BUSY",
    r"CODEX_WORKSPACE_SQLITE_BUSY",
    r"CODEX_DISPATCH_LEDGER_SQLITE_BUSY",
    r"CODEX_SPLIT_APPLY_STAGE_IDENTITY_REQUIRED",
    r"CODEX_WRITE_SCOPE_REQUIRED",
    r"CODEX_PARENT_EXECUTION_IDENTITY_MISSING",
    r"CODEX_RESUME_PROVIDER_IDENTITY_MISMATCH",
    r"PROVIDER_IDENTITY_MISMATCH",
    r"MODEL_IDENTITY_MISMATCH",
    r"Bridge request failed",
    r"httpx\.ReadError",
    r"PROJECT_RUN_ACTIVE",
    r"execution or installation is active",
)
# Свои строки супервизора в общем логе — не код отказа попытки.
_SELF_TOKENS = frozenset({
    "SUPERVISOR_ATTEMPT",
    "SUPERVISOR_PASS",
    "SUPERVISOR_TIMEOUT",
    "RESTART",
    "COMMIT",
})
_HANG_MARKERS = ("PYTEST_STALL", "PYTEST_SUITE_TIMEOUT", "SILENCE_TIMEOUT", "workflow wall-clock timeout")
# Только явный запрос решения человека. Остановка линии без такого маркера — META.
_OWNER_PATTERNS = (
    r"NEEDS_OWNER",
    r"BLOCKED_NEEDS_OWNER",
    r"OWNER_DECISION",
    r"UNRESOLVED_PRODUCT_SEMANTICS",
    r"OWNER_GO_REQUIRED",
)
# Сбой pytest/ассертов по change — PRODUCT; не путать с падением рантайма до gate.
_PRODUCT_VERIFICATION_PATTERNS = (
    r"named pytest checks failed",
    r"AssertionError",
    r"FAILED tests/",
    r"FAILED .+test_[A-Za-z0-9_]+\.py",
    r"pytest\.PytestAssertionError",
)
# Процессные артефакты / баг-лог: отсутствие файла — дефект OPSX, не «не дописали фичу».
_PLATFORM_MISSING_FILE_MARKERS = (
    r"opsx[/\\]receipts[/\\]",
    r"opsx[/\\]runs[/\\]bug-log[/\\]",
    r"test_trace_matrix\.json",
)
_PROCESS_EXCEPTION_NAMES = (
    "FileNotFoundError",
    "PermissionError",
    "IsADirectoryError",
    "NotADirectoryError",
)


class FailureOwner(str, Enum):
    """Владелец следующего действия по классификации."""

    META = "META"
    HANG = "HANG"
    INFRA = "INFRA"
    OWNER = "OWNER"
    PRODUCT = "PRODUCT"


@dataclass(frozen=True, slots=True)
class FailureClassification:
    """Итог разбора одной неуспешной попытки workflow."""

    owner: FailureOwner
    code: str
    stage: str
    fingerprint: str
    message: str
    repair_eligible: bool

    @property
    def fingerprint_key(self) -> str:
        return self.fingerprint


def _tail_lines(text: str, *, limit: int = 80) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def _extract_stage(tail: str) -> str:
    for pattern in (
        r"stage=([A-Za-z0-9_\-]+)",
        r"last_node_id[=\s]+([A-Za-z0-9_\-]+)",
        r"hop starting \(([^)]+)\)",
    ):
        match = re.search(pattern, tail)
        if match:
            return match.group(1).strip()[:120]
    return "UNKNOWN_STAGE"


def _extract_code(tail: str, record: Mapping[str, Any]) -> str:
    error = str(record.get("error") or "")
    blob = f"{error}\n{tail}"
    for exc in _PROCESS_EXCEPTION_NAMES:
        if error.startswith(f"{exc}:") or error.startswith(exc):
            return exc
    for pattern in (
        r"OpsxStageStop code=([A-Z0-9_]+)",
        r"code=([A-Z0-9_]+)",
        r"VerifyImplProcessExitError:.+terminal_status='([^']+)'",
        r"([A-Z][A-Z0-9_]{3,})",
    ):
        for match in re.finditer(pattern, blob):
            token = match.group(1).strip()[:64]
            if token not in _SELF_TOKENS:
                return token
    workflow = str(record.get("workflow_status") or "WORKFLOW_FAILED")
    return workflow[:64]


def _fingerprint(code: str, stage: str, tail: str) -> str:
    normalized = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.Z+-]+", "<ts>", tail)
    normalized = re.sub(r"run_id=[a-f0-9]+", "run_id=<id>", normalized, flags=re.I)
    normalized = re.sub(r"pid=\d+", "pid=<pid>", normalized)
    digest = hashlib.sha256(f"{code}\n{stage}\n{normalized[-4000:]}".encode("utf-8")).hexdigest()
    return digest[:24]


def _matches_any(patterns: tuple[str, ...], blob: str) -> bool:
    return any(re.search(pat, blob) for pat in patterns)


def _platform_missing_process_artifact(blob: str) -> bool:
    """Отсутствие файла на путях OPSX — дефект порядка hop / рантайма, не PRODUCT gate."""
    if not any(exc in blob for exc in _PROCESS_EXCEPTION_NAMES):
        return False
    return _matches_any(_PLATFORM_MISSING_FILE_MARKERS, blob)


def classify_attempt(
    record: Mapping[str, Any],
    *,
    log_tail: str,
    hang_reason: str | None = None,
) -> FailureClassification:
    """Сопоставить запись worker и хвост лога с владельцем ремонта."""
    tail = _tail_lines(log_tail)
    code = hang_reason or _extract_code(tail, record)
    stage = _extract_stage(tail)
    blob = f"{code}\n{record.get('error') or ''}\n{tail}"

    if hang_reason or code in _HANG_MARKERS or "SILENCE_TIMEOUT" in blob:
        owner = FailureOwner.HANG
        repair = True
    elif _matches_any(_INFRA_PATTERNS, blob):
        owner = FailureOwner.INFRA
        repair = False
    elif _matches_any(_OWNER_PATTERNS, blob):
        owner = FailureOwner.OWNER
        repair = False
    elif _matches_any(_META_PATTERNS, blob) or _platform_missing_process_artifact(blob):
        owner = FailureOwner.META
        repair = True
    elif _matches_any(_PRODUCT_VERIFICATION_PATTERNS, blob):
        owner = FailureOwner.PRODUCT
        repair = False
    elif str(record.get("verification_status") or "") == "FAIL":
        # FAIL без явного pytest/ассерта: если в error — stdlib exception, это сбой процесса.
        error = str(record.get("error") or "")
        if any(error.startswith(f"{exc}:") or error.startswith(exc) for exc in _PROCESS_EXCEPTION_NAMES):
            owner = FailureOwner.META
            repair = True
        else:
            owner = FailureOwner.PRODUCT
            repair = False
    else:
        owner = FailureOwner.PRODUCT
        repair = False

    fp = _fingerprint(code, stage, tail)
    msg = str(record.get("error") or code or record.get("workflow_status") or "unknown failure")
    return FailureClassification(
        owner=owner,
        code=code,
        stage=stage,
        fingerprint=fp,
        message=msg[:2000],
        repair_eligible=repair,
    )