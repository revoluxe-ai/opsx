"""Детерминированные контракты надёжности супервизора: domain, lifecycle, convergence."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from enum import Enum
from typing import Any, Mapping

from .supervisor_classify import FailureClassification, FailureOwner


class FailureDomain(str, Enum):
    """Домен причины отказа (отдельно от repair authority и legacy FailureOwner)."""

    OPSX_CORE = "OPSX_CORE"
    PRODUCT_CODE = "PRODUCT_CODE"
    ENVIRONMENT = "ENVIRONMENT"
    PROVIDER = "PROVIDER"
    CONTRACT_CONFLICT = "CONTRACT_CONFLICT"
    EXPECTED_PAUSE = "EXPECTED_PAUSE"
    UNKNOWN = "UNKNOWN"


class RepairAuthority(str, Enum):
    """Кто может инициировать META repair-hop (не совпадает с domain)."""

    META_REPAIR = "META_REPAIR"
    WORKFLOW_ONLY = "WORKFLOW_ONLY"
    OWNER = "OWNER"
    NONE = "NONE"


class IncidentPhase(str, Enum):
    """Фазы recovery state machine (журналируются без зависимости от LLM-текста)."""

    DIAGNOSED = "DIAGNOSED"
    PATCH_PREPARED = "PATCH_PREPARED"
    PATCH_VERIFIED = "PATCH_VERIFIED"
    REVIEW_ACCEPTED = "REVIEW_ACCEPTED"
    COMMITTED = "COMMITTED"
    REPLAY_VERIFIED = "REPLAY_VERIFIED"
    INCIDENT_RESOLVED = "INCIDENT_RESOLVED"
    FAIL = "FAIL"
    PAUSE = "PAUSE"


# Causal evidence: ошибки рантайма OPSX, не product pytest gate.
_OPSX_CORE_PATTERNS = (
    r"ImplementationLaneTerminalError",
    r"APPLY_BLOCKED",
    r"artifact_missing",
    r"DESIGN_MACHINE_GATE_FAILED",
    r"READ_SCOPE_VIOLATION",
    r"VerifyImplProcessExitError",
    r"OpsxStageStop",
    r"GATE_REPAIR_NO_PROGRESS",
    r"GREEN_TEST_RUNNER_ENV",
    r"RED_COMMANDS_EMPTY",
    r"RED_MIXED_SUITE",
    r"RECEIPT_NOT_ACCEPTED",
    r"RECEIPT_POST_VALIDATE",
    r"RECEIPT_PUBLISH_MISSING",
    r"RECORD_CLI_TIMEOUT",
    r"RECORD_CLI_FAILED",
    r"opsx-record-slice-receipt-decision-cli",
    r"SDK_BRIDGE_TIMEOUT",
    r"AgentRunContractError",
    r"test_trace_matrix\.json",
    r"UnboundLocalError:.+worker\.py",
    r"ImportError:.+opsx",
    r"ModuleNotFoundError:.+opsx",
    r"ModuleNotFoundError:.+cursor_sdk",
    r"ImportError:.+cursor_sdk",
    r"ModuleNotFoundError:.+langgraph",
    r"ImportError:.+langgraph",
    r"AUTHOR_COMPLETE",
    r"PYTEST_STALL",
    r"PYTEST_SUITE_TIMEOUT",
)
_PROVIDER_PATTERNS = (
    r"APITimeoutError",
    r"InternalServerError",
    r"SILENCE_TIMEOUT",
    r"CURSOR_PROVIDER_UNAVAILABLE",
    r"Bridge request failed",
    r"httpx\.ReadError",
    r"SDK_BRIDGE_TIMEOUT",
    r"subprocess\.TimeoutExpired",
    r"TimeoutExpired",
    r"REPAIR_EXECUTOR_UNAVAILABLE",
    r"connection reset",
    r"RemoteDisconnected",
)
_ENVIRONMENT_PATTERNS = (
    r"PROJECT_ADOPTION_REQUIRED",
    r"NOT_INSTALLED",
    r"RUNTIME_NOT_INSTALLED",
    r"PROJECT_RUN_ACTIVE",
    r"execution or installation is active",
    r"Poetry env",
    r"cannot import langgraph",
)
_OWNER_PATTERNS = (
    r"NEEDS_OWNER",
    r"BLOCKED_NEEDS_OWNER",
    r"OWNER_DECISION",
    r"UNRESOLVED_PRODUCT_SEMANTICS",
    r"OWNER_GO_REQUIRED",
)
_PRODUCT_PATTERNS = (
    r"named pytest checks failed",
    r"AssertionError",
    r"FAILED tests/",
    r"pytest\.PytestAssertionError",
)
_CONTRACT_PATTERNS = (
    r"DESIGN_DIGEST_CONFLICT",
    r"INVALID_JSON",
    r"contract conflict",
    r"Architecture Decision Missing",
)


def _matches(patterns: tuple[str, ...], blob: str) -> bool:
    return any(re.search(pat, blob, flags=re.I) for pat in patterns)


def owner_to_legacy_domain(owner: FailureOwner) -> FailureDomain:
    """Стабильное сопоставление legacy owner → domain при отсутствии causal evidence."""
    table = {
        FailureOwner.META: FailureDomain.OPSX_CORE,
        FailureOwner.HANG: FailureDomain.OPSX_CORE,
        FailureOwner.INFRA: FailureDomain.ENVIRONMENT,
        FailureOwner.OWNER: FailureDomain.EXPECTED_PAUSE,
        FailureOwner.PRODUCT: FailureDomain.PRODUCT_CODE,
    }
    return table.get(owner, FailureDomain.UNKNOWN)


def derive_failure_domain(
    classification: FailureClassification,
    record: Mapping[str, Any],
    *,
    log_tail: str = "",
    hang_reason: str | None = None,
) -> FailureDomain:
    """Классификация по causal evidence; owner — только fallback."""
    blob = "\n".join(
        filter(
            None,
            (
                classification.code,
                classification.message,
                str(record.get("error") or ""),
                log_tail[-8000:],
                hang_reason or "",
            ),
        )
    )
    if hang_reason or classification.owner == FailureOwner.HANG:
        if _matches(_PROVIDER_PATTERNS, blob):
            return FailureDomain.PROVIDER
        return FailureDomain.OPSX_CORE
    if _matches(_OWNER_PATTERNS, blob):
        return FailureDomain.EXPECTED_PAUSE
    if _matches(_CONTRACT_PATTERNS, blob):
        return FailureDomain.CONTRACT_CONFLICT
    if _matches(_ENVIRONMENT_PATTERNS, blob):
        return FailureDomain.ENVIRONMENT
    if _matches(_OPSX_CORE_PATTERNS, blob) or classification.owner == FailureOwner.META:
        return FailureDomain.OPSX_CORE
    if _matches(_PROVIDER_PATTERNS, blob):
        return FailureDomain.PROVIDER
    if _matches(_PRODUCT_PATTERNS, blob) or classification.owner == FailureOwner.PRODUCT:
        return FailureDomain.PRODUCT_CODE
    return owner_to_legacy_domain(classification.owner)


def derive_repair_authority(
    domain: FailureDomain,
    classification: FailureClassification,
    *,
    clean_product_gap: bool = False,
) -> RepairAuthority:
    """Отделить право META repair от ярлыка domain."""
    if domain in {FailureDomain.EXPECTED_PAUSE, FailureDomain.CONTRACT_CONFLICT}:
        return RepairAuthority.OWNER
    if domain == FailureDomain.ENVIRONMENT:
        return RepairAuthority.NONE
    if domain == FailureDomain.PROVIDER:
        return RepairAuthority.NONE
    if domain == FailureDomain.PRODUCT_CODE and not clean_product_gap:
        return RepairAuthority.WORKFLOW_ONLY
    if domain in {FailureDomain.OPSX_CORE, FailureDomain.UNKNOWN}:
        if classification.owner in {FailureOwner.META, FailureOwner.HANG}:
            return RepairAuthority.META_REPAIR
        return RepairAuthority.NONE
    if clean_product_gap:
        return RepairAuthority.META_REPAIR
    return RepairAuthority.NONE


def causal_signature(
    *,
    domain: FailureDomain,
    code: str,
    stage: str,
    failing_component: str = "",
) -> str:
    """Стабильный ключ для no-progress / convergence (не сырой fingerprint лога)."""
    wire = f"{domain.value}|{code}|{stage}|{failing_component}".lower()
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()[:24]


def infer_failing_component(code: str, blob: str) -> str:
    """Извлечь компонент из кода/лога для causal signature."""
    for pattern in (
        r"opsx_cli/([A-Za-z0-9_]+)",
        r"agents/([A-Za-z0-9_]+)/",
        r"node[=\s]+([A-Za-z0-9_\-]+)",
    ):
        match = re.search(pattern, blob)
        if match:
            return match.group(1)[:80]
    return code[:80]


def classification_enriched(
    classification: FailureClassification,
    record: Mapping[str, Any],
    *,
    log_tail: str = "",
    hang_reason: str | None = None,
    clean_product_gap: bool = False,
) -> dict[str, Any]:
    """Сериализация с domain + repair_authority для журнала (QUALIFY совместим)."""
    blob = f"{classification.code}\n{log_tail}"
    domain = derive_failure_domain(classification, record, log_tail=log_tail, hang_reason=hang_reason)
    authority = derive_repair_authority(domain, classification, clean_product_gap=clean_product_gap)
    component = infer_failing_component(classification.code, blob)
    causal = causal_signature(
        domain=domain,
        code=classification.code,
        stage=classification.stage,
        failing_component=component,
    )
    payload = {
        "owner": classification.owner.value,
        "code": classification.code,
        "stage": classification.stage,
        "fingerprint": classification.fingerprint,
        "message": classification.message,
        "repair_eligible": classification.repair_eligible,
        "failure_domain": domain.value,
        "repair_authority": authority.value,
        "causal_signature": causal,
        "failing_component": component,
    }
    return payload


def fix_hop_allowed_for_authority(authority: RepairAuthority) -> bool:
    return authority == RepairAuthority.META_REPAIR


def provider_executor_interrupted(error: str) -> bool:
    """Исполнитель repair оборвался — это не CANNOT_FIX по исходному дефекту."""
    return _matches(_PROVIDER_PATTERNS, error)


def map_fix_verdict_on_executor_failure(verdict: str, error: str) -> tuple[str, str]:
    """Нормализовать вердикт fix-hop при provider/timeout."""
    if verdict == "CANNOT_FIX" and provider_executor_interrupted(error):
        return "FIX_INTERRUPTED", "REPAIR_EXECUTOR_UNAVAILABLE"
    return verdict, error


@dataclass(frozen=True, slots=True)
class ConvergencePredicate:
    """Machine-checkable convergence после commit+replay."""

    original_causal_signature: str
    before_failure_identity: str
    repair_revision: str | None
    replay_evidence: str

    def satisfied(
        self,
        *,
        after_failure_identity: str,
        replay_ok: bool,
        original_cause_still_present: bool,
        sibling_failure: bool,
        regression: bool,
    ) -> tuple[bool, str]:
        if not replay_ok:
            return False, "REPLAY_NOT_VERIFIED"
        if original_cause_still_present:
            return False, "ORIGINAL_CAUSE_STILL_PRESENT"
        if after_failure_identity == self.before_failure_identity:
            return False, "FAILURE_IDENTITY_UNCHANGED"
        if sibling_failure:
            return False, "SIBLING_FAILURE"
        if regression:
            return False, "REGRESSION"
        if not self.repair_revision:
            return False, "MISSING_REPAIR_REVISION"
        return True, "CONVERGED"


def workflow_record_identity(record: Mapping[str, Any]) -> str:
    """Короткая revision-ish идентичность outcome worker."""
    parts = (
        str(record.get("workflow_status") or ""),
        str(record.get("verification_status") or ""),
        str(record.get("process_status") or ""),
        str(record.get("exit_code") or ""),
        str(record.get("error") or "")[:200],
    )
    wire = "|".join(parts)
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()[:24]


def git_head_sha(repo: Path) -> str | None:
    """Текущий HEAD SHA в checkout (для baseline / repair revision)."""
    if not (repo / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=True,
        )
        return out.stdout.strip()[:40]
    except (OSError, subprocess.CalledProcessError):
        return None


def entry_incident_resolved(entry_status: str, commit_status: str | None, phase: str | None) -> bool:
    """INCIDENT_RESOLVED только при подтверждённом replay (phase), не при COMMITTED."""
    if entry_status == "COMMIT_FAILED":
        return False
    if commit_status != "COMMITTED":
        return False
    if entry_status != "FIXED":
        return False
    if phase is None:
        return False
    return phase in {IncidentPhase.INCIDENT_RESOLVED.value, IncidentPhase.REPLAY_VERIFIED.value}


@dataclass(frozen=True, slots=True)
class ReplayConvergenceVerdict:
    """Итог проверки convergence после replay PASS."""

    incident_resolved: bool
    convergence_verdict: str
    supervisor_stop: str | None
    workflow_status: str
    record_status: str


def revisions_match(repair_revision: str | None, current_revision: str | None) -> bool:
    """Сравнить repair revision и текущий HEAD (short/full)."""
    if not repair_revision or not current_revision:
        return False
    left = repair_revision.strip().lower()
    right = current_revision.strip().lower()
    if left == right:
        return True
    return left.startswith(right[:12]) or right.startswith(left[:12])


def evaluate_replay_convergence(
    pending: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    run_id: str,
    attempt: int,
    current_revision: str | None,
) -> ReplayConvergenceVerdict:
    """Проверить, что PASS workflow — recovery инцидента, а не «первый раз сдали»."""
    if not pending:
        return ReplayConvergenceVerdict(
            incident_resolved=False,
            convergence_verdict="NO_PENDING_REPLAY",
            supervisor_stop=None,
            workflow_status="SUPERVISED_PASS",
            record_status="PASS",
        )
    causal = str(pending.get("causal_signature") or pending.get("original_causal_signature") or "")
    repair_revision = pending.get("repair_revision")
    before_id = str(pending.get("before_failure_identity") or "")
    incident_id = str(pending.get("incident_id") or "")
    if not causal or not before_id or not incident_id:
        return ReplayConvergenceVerdict(
            incident_resolved=False,
            convergence_verdict="MISSING_INCIDENT_EVIDENCE",
            supervisor_stop="REPLAY_BLOCKED",
            workflow_status="REPLAY_BLOCKED",
            record_status="FAILED",
        )
    if not repair_revision:
        return ReplayConvergenceVerdict(
            incident_resolved=False,
            convergence_verdict="MISSING_REPAIR_REVISION",
            supervisor_stop="REPLAY_BLOCKED",
            workflow_status="REPLAY_BLOCKED",
            record_status="FAILED",
        )
    bound_execution = str(pending.get("execution_id") or "")
    if bound_execution and bound_execution != run_id:
        return ReplayConvergenceVerdict(
            incident_resolved=False,
            convergence_verdict="REPLAY_EXECUTION_MISMATCH",
            supervisor_stop="REPLAY_BLOCKED",
            workflow_status="REPLAY_BLOCKED",
            record_status="FAILED",
        )
    if pending.get("replay_status") == "RESOLVED":
        return ReplayConvergenceVerdict(
            incident_resolved=False,
            convergence_verdict="ALREADY_RESOLVED",
            supervisor_stop="REPLAY_BLOCKED",
            workflow_status="REPLAY_BLOCKED",
            record_status="FAILED",
        )
    if not revisions_match(str(repair_revision), current_revision):
        return ReplayConvergenceVerdict(
            incident_resolved=False,
            convergence_verdict="REPLAY_REVISION_MISMATCH",
            supervisor_stop="REPLAY_BLOCKED",
            workflow_status="REPLAY_BLOCKED",
            record_status="FAILED",
        )
    after_id = workflow_record_identity(record)
    original_code = str(pending.get("original_code") or "")
    err = str(record.get("error") or "")
    original_still = bool(original_code and original_code in err)
    sibling = str(record.get("verification_status") or "").upper() == "FAIL"
    regression = after_id == before_id
    predicate = ConvergencePredicate(
        original_causal_signature=causal,
        before_failure_identity=before_id,
        repair_revision=str(repair_revision),
        replay_evidence=f"{run_id}:{attempt}",
    )
    ok, code = predicate.satisfied(
        after_failure_identity=after_id,
        replay_ok=True,
        original_cause_still_present=original_still,
        sibling_failure=sibling,
        regression=regression,
    )
    if ok:
        return ReplayConvergenceVerdict(
            incident_resolved=True,
            convergence_verdict=code,
            supervisor_stop=None,
            workflow_status="SUPERVISED_INCIDENT_RESOLVED",
            record_status="PASS",
        )
    return ReplayConvergenceVerdict(
        incident_resolved=False,
        convergence_verdict=code,
        supervisor_stop="REPLAY_FAIL",
        workflow_status="REPLAY_FAIL",
        record_status="FAILED",
    )


def map_entry_phase(status: str, *, patch_verified: bool, committed: bool, replay_verified: bool) -> str:
    """Совместимые journal status → lifecycle phase."""
    if status in {"FIX_REJECTED", "FIX_INTERRUPTED"}:
        return IncidentPhase.FAIL.value
    if status == "COMMIT_FAILED":
        return IncidentPhase.FAIL.value
    if replay_verified and committed:
        return IncidentPhase.INCIDENT_RESOLVED.value
    if committed:
        return IncidentPhase.COMMITTED.value
    if patch_verified and status == "FIXED":
        return IncidentPhase.PATCH_VERIFIED.value
    if status == "FIX_PENDING":
        return IncidentPhase.PATCH_PREPARED.value
    return IncidentPhase.DIAGNOSED.value