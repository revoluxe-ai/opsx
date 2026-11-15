"""Валидатор MINIMAL_AUTHORITY_GRANT_V0 для OPSX execution authority (S0+S1.5).

Проверяет форму grant, `checkpoint_publish`, protected refs, worker-mint,
неизвестные model_tier и попытки обойти стабильный контракт supervisor (§27).
Не является live enforcement hook. `LIVE_AUTHORITY: NO`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, NoReturn, Sequence

SCHEMA_ID = "MINIMAL_AUTHORITY_GRANT_V0"
GRANT_ID_RE = re.compile(r"^mag-[0-9A-HJKMNP-TV-Z]{26}$")
AGENT_ID_RE = re.compile(r"^bc-[0-9a-f-]{36}$")
OID_RE = re.compile(r"^[0-9a-f]{7,40}$")
REF_RE = re.compile(r"^refs/heads/")
OWNER_ATTESTATION_RE = re.compile(
    r"^(store:|cursor-stores/|/cursor/stores/|owner-message:)"
)

PUBLICATION_ACTIONS = frozenset(
    {
        "push_ref",
        "create_pr",
        "update_pr_body",
        "merge",
        "local_commit",
        "checkpoint_publish",
    }
)
FORBID_TOKENS = frozenset(
    {
        "force_push",
        "push_unlisted_oid",
        "merge",
        "deploy",
        "migrate",
        "semantic_write_guess",
    }
)
ALLOWED_ACTIONS = frozenset(
    {
        "local_commit",
        "push_ref",
        "create_pr",
        "update_pr_body",
        "merge",
        "checkpoint_publish",
    }
)


class InvocationSurface(str, Enum):
    """Поверхность, через которую предъявлен grant."""

    OPSX_COMMIT = "opsx_commit"
    OPSX_CREATE_PR = "opsx_create_pr"
    SUPERVISOR_SUBAGENT = "supervisor_subagent"
    CLOUD_PARENT = "cloud_parent"
    VALIDATION_ONLY = "validation_only"


class GrantRejectReason(str, Enum):
    """Стабильные коды отказа grant (fail-closed)."""

    MALFORMED = "GRANT_REJECTED_MALFORMED"
    WORKER_MINT = "GRANT_REJECTED_WORKER_MINT"
    MODEL_TIER_UNDEFINED = "GRANT_REJECTED_MODEL_TIER_UNDEFINED"
    STABLE_POLICY_OVERRIDES_GRANT = "GRANT_REJECTED_STABLE_POLICY_DOMINATES"
    ACTION_FORBID_COLLISION = "GRANT_REJECTED_ACTION_FORBID_COLLISION"
    PUBLICATION_OID_REQUIRED = "GRANT_REJECTED_PUBLICATION_OID_REQUIRED"
    EXPIRED = "GRANT_REJECTED_EXPIRED"
    ATTESTATION_INVALID = "GRANT_REJECTED_ATTESTATION_INVALID"
    PROTECTED_REF = "GRANT_REJECTED_PROTECTED_REF"
    CHECKPOINT_ACTION_MIX = "GRANT_REJECTED_CHECKPOINT_ACTION_MIX"
    CHECKPOINT_CONFIG = "GRANT_REJECTED_CHECKPOINT_CONFIG"


@dataclass(frozen=True, slots=True)
class GrantValidationResult:
    """Итог проверки grant."""

    ok: bool
    reason: GrantRejectReason | str | None = None
    detail: str = ""


class GrantValidationError(ValueError):
    """Ошибка валидации grant с машинным кодом."""

    def __init__(
        self,
        reason: GrantRejectReason | str,
        detail: str,
    ) -> None:
        code = reason.value if isinstance(reason, GrantRejectReason) else str(reason)
        super().__init__(f"{code}: {detail}")
        self.reason = reason
        self.detail = detail


def _fail(reason: GrantRejectReason | str, detail: str) -> NoReturn:
    raise GrantValidationError(reason, detail)


def normalize_ref(ref: str) -> str:
    """Нормализовать имя ветки к полному refs/heads/… (case-sensitive)."""

    trimmed = ref.strip()
    if trimmed.startswith("refs/heads/"):
        return trimmed
    if trimmed.startswith("refs/"):
        return trimmed
    return f"refs/heads/{trimmed}"


def _parse_rfc3339(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _require_mapping(grant: Mapping[str, Any]) -> None:
    if grant.get("schema") != SCHEMA_ID:
        _fail(GrantRejectReason.MALFORMED, "schema must be MINIMAL_AUTHORITY_GRANT_V0")
    grant_id = grant.get("grant_id")
    if not isinstance(grant_id, str) or not GRANT_ID_RE.match(grant_id):
        _fail(GrantRejectReason.MALFORMED, "grant_id must match mag-<ulid>")
    if grant.get("issuer") != "OWNER":
        _fail(GrantRejectReason.MALFORMED, "issuer must be OWNER")
    if grant.get("exact_token") != "GO":
        _fail(GrantRejectReason.MALFORMED, "exact_token must be GO")
    if grant.get("on_conflict") != "OWNER_ATTENTION":
        _fail(GrantRejectReason.MALFORMED, "on_conflict must be OWNER_ATTENTION")
    if grant.get("repo") != "revoluxe-ai/stroy-system":
        _fail(GrantRejectReason.MALFORMED, "repo must be revoluxe-ai/stroy-system")


def _validate_model_tier(grant: Mapping[str, Any]) -> None:
    tier = grant.get("model_tier")
    if tier == "standard":
        _fail(
            GrantRejectReason.MODEL_TIER_UNDEFINED,
            "model_tier standard is undefined; use inherit only",
        )
    if tier == "fast" and not grant.get("owner_fast_allow"):
        _fail(
            GrantRejectReason.MALFORMED,
            "model_tier fast requires owner_fast_allow: true",
        )
    if tier != "inherit":
        _fail(
            GrantRejectReason.MODEL_TIER_UNDEFINED,
            f"model_tier must be inherit; got {tier!r}",
        )
    if grant.get("owner_fast_allow") is True and tier != "inherit":
        _fail(
            GrantRejectReason.MALFORMED,
            "owner_fast_allow applies only with model_tier inherit",
        )


def _validate_attestation(
    grant: Mapping[str, Any],
    *,
    mint_channel: str | None,
) -> None:
    attestation_ref = grant.get("owner_attestation_ref")
    if not isinstance(attestation_ref, str) or not attestation_ref.strip():
        _fail(
            GrantRejectReason.ATTESTATION_INVALID,
            "owner_attestation_ref is required",
        )
    if not OWNER_ATTESTATION_RE.match(attestation_ref):
        _fail(
            GrantRejectReason.ATTESTATION_INVALID,
            "owner_attestation_ref must be Owner-held (store:/cursor-stores/…)",
        )
    if mint_channel in {"worker", "WORKER", "self_asserted"}:
        _fail(
            GrantRejectReason.WORKER_MINT,
            "workers cannot mint authority grants",
        )


def _validate_actions_disjoint(grant: Mapping[str, Any]) -> None:
    actions = grant.get("actions")
    forbids = grant.get("forbids")
    if not isinstance(actions, list) or not actions:
        _fail(GrantRejectReason.MALFORMED, "actions must be a non-empty list")
    if not isinstance(forbids, list):
        _fail(GrantRejectReason.MALFORMED, "forbids must be a list")
    action_set = set(actions)
    forbid_set = set(forbids)
    unknown_actions = action_set - ALLOWED_ACTIONS
    if unknown_actions:
        _fail(GrantRejectReason.MALFORMED, f"unknown actions: {sorted(unknown_actions)}")
    unknown_forbids = forbid_set - FORBID_TOKENS
    if unknown_forbids:
        _fail(
            GrantRejectReason.MALFORMED,
            f"unknown forbids: {sorted(unknown_forbids)}",
        )
    if action_set & forbid_set:
        _fail(
            GrantRejectReason.ACTION_FORBID_COLLISION,
            "actions and forbids must be disjoint",
        )
    if "merge" in action_set:
        _fail(
            GrantRejectReason.MALFORMED,
            "merge must not appear in actions for V0",
        )
    if "push_ref" in action_set:
        for field in ("base_oid", "tip_oid", "ref"):
            val = grant.get(field)
            if not isinstance(val, str):
                _fail(
                    GrantRejectReason.PUBLICATION_OID_REQUIRED,
                    f"{field} required for push_ref",
                )
            if field in {"base_oid", "tip_oid"} and not OID_RE.match(val):
                _fail(GrantRejectReason.MALFORMED, f"{field} must be git oid")
            if field == "ref" and not REF_RE.match(val):
                _fail(GrantRejectReason.MALFORMED, "ref must be refs/heads/…")
    if "create_pr" in action_set:
        base = grant.get("base_oid")
        ref = grant.get("ref")
        if not isinstance(base, str) or not OID_RE.match(base):
            _fail(
                GrantRejectReason.PUBLICATION_OID_REQUIRED,
                "base_oid required for create_pr",
            )
        if not isinstance(ref, str) or not REF_RE.match(ref):
            _fail(GrantRejectReason.MALFORMED, "ref required for create_pr")

    if "checkpoint_publish" in action_set:
        from checkpoint_publication_v0 import (
            is_protected_ref,
            validate_checkpoint_grant_shape,
        )
        from execution_authority_trust_v0 import validate_checkpoint_grant_ref

        validate_checkpoint_grant_shape(grant)
        ref_val = grant.get("ref")
        if isinstance(ref_val, str):
            validate_checkpoint_grant_ref(ref_val)
            if is_protected_ref(ref_val, grant):
                _fail(GrantRejectReason.PROTECTED_REF, "grant.ref is protected")


def _validate_subject(grant: Mapping[str, Any]) -> None:
    subject = grant.get("subject")
    if not isinstance(subject, dict):
        _fail(GrantRejectReason.MALFORMED, "subject must be an object")
    agent_id = subject.get("agent_id")
    if not isinstance(agent_id, str) or not AGENT_ID_RE.match(agent_id):
        _fail(GrantRejectReason.MALFORMED, "subject.agent_id must be bc-uuid")
    role = subject.get("role")
    if role not in {"executor", "publisher", "merger"}:
        _fail(GrantRejectReason.MALFORMED, "subject.role invalid")


def _validate_expiry(grant: Mapping[str, Any], *, now: datetime | None) -> None:
    issued = grant.get("issued_at")
    expires = grant.get("expires_at")
    if not isinstance(issued, str) or not isinstance(expires, str):
        _fail(GrantRejectReason.MALFORMED, "issued_at and expires_at required")
    if now is None:
        _fail(
            GrantRejectReason.MALFORMED,
            "now is required; expiry is not evaluated when omitted (E1)",
        )
    issued_dt = _parse_rfc3339(issued)
    expires_dt = _parse_rfc3339(expires)
    if expires_dt <= issued_dt:
        _fail(GrantRejectReason.MALFORMED, "expires_at must be after issued_at")
    if expires_dt <= now:
        _fail(GrantRejectReason.EXPIRED, "grant expired")


def check_stable_policy_dominates_grant(
    grant: Mapping[str, Any],
    *,
    surface: InvocationSurface,
) -> None:
    """Стабильный OPSX контракт выше grant; §27 не снимается grant-объектом.

    Supervisor и его subagents остаются под absolute denylist (commit/push/PR)
    даже при валидном grant. Grant может сужать разрешения поздних контрактов,
    но не расширять их против `/opsx-supervisor` §19 и §27.
    """

    actions = grant.get("actions")
    if not isinstance(actions, list):
        return
    action_set = set(actions)
    if surface == InvocationSurface.SUPERVISOR_SUBAGENT and action_set & PUBLICATION_ACTIONS:
        _fail(
            GrantRejectReason.STABLE_POLICY_OVERRIDES_GRANT,
            "supervisor subagents cannot use grants to override §27 publication denylist",
        )

    if "checkpoint_publish" in action_set and surface in {
        InvocationSurface.OPSX_COMMIT,
        InvocationSurface.OPSX_CREATE_PR,
    }:
        _fail(
            "CHECKPOINT_REJECTED_UNAUTHORIZED_SURFACE",
            "checkpoint_publish requires cloud_parent invocation surface",
        )


def validate_minimal_authority_grant_v0(
    grant: Mapping[str, Any],
    *,
    surface: InvocationSurface = InvocationSurface.VALIDATION_ONLY,
    mint_channel: str | None = None,
    now: datetime | None = None,
    gate: "GateDerivedInvocation | None" = None,
) -> GrantValidationResult:
    """Fail-closed проверка MINIMAL_AUTHORITY_GRANT_V0."""

    try:
        from execution_authority_trust_v0 import bind_gate_invocation

        _require_mapping(grant)
        _validate_subject(grant)
        _validate_model_tier(grant)
        actions = grant.get("actions")
        action_set = set(actions) if isinstance(actions, list) else set()
        if "checkpoint_publish" in action_set:
            subject = grant.get("subject")
            agent_id = (
                subject.get("agent_id")
                if isinstance(subject, dict) and isinstance(subject.get("agent_id"), str)
                else ""
            )
            bind_gate_invocation(
                str(grant.get("repo")),
                agent_id,
                gate=gate,
                caller_surface=surface,
                caller_mint_channel=mint_channel,
            )
        _validate_attestation(grant, mint_channel=mint_channel)
        _validate_actions_disjoint(grant)
        _validate_expiry(grant, now=now)
        check_stable_policy_dominates_grant(grant, surface=surface)
    except GrantValidationError as exc:
        return GrantValidationResult(ok=False, reason=exc.reason, detail=exc.detail)
    return GrantValidationResult(ok=True)


def load_grant_document(path: Path) -> Mapping[str, Any]:
    """Загрузить grant из JSON или YAML-подобного JSON файла."""

    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        raise GrantValidationError(
            GrantRejectReason.MALFORMED,
            "yaml loader not enabled; use json for V0 tests",
        )
    data = json.loads(text)
    if not isinstance(data, dict):
        _fail(GrantRejectReason.MALFORMED, "grant document must be a JSON object")
    return data


def validate_grant_file(
    path: Path,
    *,
    surface: InvocationSurface = InvocationSurface.VALIDATION_ONLY,
    mint_channel: str | None = None,
    now: datetime | None = None,
    gate: "GateDerivedInvocation | None" = None,
) -> GrantValidationResult:
    """Проверить grant-файл на диске."""

    grant = load_grant_document(path)
    return validate_minimal_authority_grant_v0(
        grant,
        surface=surface,
        mint_channel=mint_channel,
        now=now,
        gate=gate,
    )
