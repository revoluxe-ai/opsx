"""Контрактная валидация checkpoint_publish (OPSX-2 S1.5 / S4a).

Описывает state machine публикации checkpoint без live git hook.
FF-проверка: `git merge-base --is-ancestor` при доступном git, иначе fail-closed
(префикс oid не считается ancestry). `GateDerivedInvocation` обязателен для
checkpoint transitions — surface/mint не только caller-asserted.

VALIDATION_ONLY: этот модуль не вызывает `git ls-remote`; remote oid в контексте
должен поступать от gate (git-backed path в S4b). `LIVE_AUTHORITY: NO`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from execution_authority_trust_v0 import (
    GateDerivedInvocation,
    bind_gate_invocation,
    canonical_repo_slug,
    is_checkpoint_denied_branch_name,
    is_fast_forward_ancestor,
    is_full_git_oid,
    validate_checkpoint_grant_ref,
)
from minimal_authority_grant import (
    GrantRejectReason,
    GrantValidationError,
    InvocationSurface,
    OID_RE,
    REF_RE,
    _fail,
    normalize_ref,
)

CHECKPOINT_MODES = frozenset({"auto_commit_and_push"})
CHECKPOINT_IDEMPOTENCY = frozenset({"git_ff_oid"})
FORBIDDEN_SUCCESS_WORDS = frozenset({"GREEN", "PASS"})

DEFAULT_PROTECTED_REF_SUFFIXES: frozenset[str] = frozenset(
    {
        "main",
        "master",
        "stage",
        "prod",
        "production",
    }
)

CHECKPOINT_INCOMPATIBLE_ACTIONS = frozenset(
    {"local_commit", "push_ref", "create_pr", "merge", "update_pr_body"}
)

RECEIPT_ATTESTATION_RE = re.compile(
    r"(checkpoint[-_]?receipt|CHECKPOINT_RECEIPT|receipt\.json)",
    re.IGNORECASE,
)


class CheckpointPhase(str, Enum):
    """Фазы checkpoint publication (контракт, не runtime hook)."""

    DENIED_NO_GRANT = "DENIED_NO_GRANT"
    GRANT_REJECTED = "GRANT_REJECTED"
    CHECKPOINT_ELIGIBLE = "CHECKPOINT_ELIGIBLE"
    CHECKPOINT_LOCAL = "CHECKPOINT_LOCAL"
    CHECKPOINT_PUSHING = "CHECKPOINT_PUSHING"
    CHECKPOINT_PUBLISHED = "CHECKPOINT_PUBLISHED"
    CHECKPOINT_IDEMPOTENT_NOOP = "CHECKPOINT_IDEMPOTENT_NOOP"
    CHECKPOINT_DIVERGED = "CHECKPOINT_DIVERGED"
    DENIED_PROTECTED_REF = "DENIED_PROTECTED_REF"
    OWNER_ATTENTION = "OWNER_ATTENTION"
    BLOCKED_POLICY_VIOLATION = "BLOCKED_POLICY_VIOLATION"
    DENIED_UNAUTHORIZED_SURFACE = "DENIED_UNAUTHORIZED_SURFACE"


class CheckpointRejectReason(str, Enum):
    """Коды отказа попытки checkpoint publish."""

    PROTECTED_REF = "CHECKPOINT_REJECTED_PROTECTED_REF"
    ALTERNATE_TARGET = "CHECKPOINT_REJECTED_ALTERNATE_TARGET"
    FORCE_PUSH = "CHECKPOINT_REJECTED_FORCE_PUSH"
    NON_FF = "CHECKPOINT_REJECTED_NON_FF"
    REF_DELETE = "CHECKPOINT_REJECTED_REF_DELETE"
    CROSS_EXECUTION = "CHECKPOINT_REJECTED_CROSS_EXECUTION"
    STALE_REVISION = "CHECKPOINT_REJECTED_STALE_GRANT_REVISION"
    UNAUTHORIZED_SURFACE = "CHECKPOINT_REJECTED_UNAUTHORIZED_SURFACE"
    UNAUTHORIZED_AGENT = "CHECKPOINT_REJECTED_UNAUTHORIZED_AGENT"
    TIP_OID_PREBOUND = "CHECKPOINT_REJECTED_TIP_OID_PREBOUND"
    REMOTE_UNCERTAIN = "CHECKPOINT_REJECTED_REMOTE_UNCERTAIN"
    FABRICATED_SUCCESS = "CHECKPOINT_REJECTED_FABRICATED_SUCCESS"
    ACTION_MIX = "CHECKPOINT_REJECTED_ACTION_MIX"
    CHECKPOINT_CONFIG = "CHECKPOINT_REJECTED_CHECKPOINT_CONFIG"


@dataclass(frozen=True, slots=True)
class CheckpointPublishContext:
    """Контекст одной попытки checkpoint (после grant validation)."""

    surface: InvocationSurface
    agent_id: str
    execution_id: str
    grant_revision: int
    grant_ref: str
    remote_ref_target: str
    head_ref: str
    force_push: bool = False
    delete_ref: bool = False
    local_tip_oid: str | None = None
    remote_oid_before: str | None = None
    remote_oid_after: str | None = None
    remote_push_acknowledged: bool = False
    worktree_dirty: bool = False
    worktree_clean: bool = False
    local_equals_remote: bool = False
    parent_oid: str | None = None
    tree_oid: str | None = None
    interrupted_mid_push: bool = False
    proposed_success_word: str | None = None
    repo_remote: str | None = None
    gate: GateDerivedInvocation | None = None


@dataclass(frozen=True, slots=True)
class CheckpointTransitionResult:
    """Итог перехода state machine."""

    ok: bool
    phase: CheckpointPhase
    reason: CheckpointRejectReason | GrantRejectReason | None = None
    detail: str = ""


def protected_ref_set(
    grant: Mapping[str, Any],
) -> frozenset[str]:
    """Полный набор защищённых ref: defaults + Owner-configured prohibited."""

    extra = grant.get("owner_prohibited_refs")
    refs: set[str] = {
        f"refs/heads/{name}" for name in DEFAULT_PROTECTED_REF_SUFFIXES
    }
    if isinstance(extra, list):
        for item in extra:
            if isinstance(item, str):
                normalized = normalize_ref(item)
                if not normalized.startswith("refs/heads/"):
                    normalized = f"refs/heads/{normalized}"
                refs.add(normalized)
    return frozenset(refs)


def is_protected_ref(ref: str, grant: Mapping[str, Any]) -> bool:
    """True если ref в protected set (полное совпадение после нормализации)."""

    normalized = normalize_ref(ref)
    if not normalized.startswith("refs/heads/"):
        normalized = f"refs/heads/{normalized}"
    if normalized in protected_ref_set(grant):
        return True
    branch = normalized.removeprefix("refs/heads/")
    return is_checkpoint_denied_branch_name(branch)


def validate_checkpoint_grant_shape(grant: Mapping[str, Any]) -> None:
    """Дополнительные правила grant при наличии checkpoint_publish."""

    actions = grant.get("actions")
    if not isinstance(actions, list):
        return
    action_set = set(actions)
    if "checkpoint_publish" not in action_set:
        return

    if action_set & CHECKPOINT_INCOMPATIBLE_ACTIONS:
        _fail(
            GrantRejectReason.CHECKPOINT_ACTION_MIX,
            "checkpoint_publish must not mix with local_commit/push_ref/create_pr/merge",
        )

    if grant.get("tip_oid") is not None:
        _fail(
            GrantRejectReason.MALFORMED,
            "tip_oid must not be pre-bound on checkpoint_publish grant",
        )

    base = grant.get("base_oid")
    ref = grant.get("ref")
    if not isinstance(base, str) or not OID_RE.match(base):
        _fail(
            GrantRejectReason.PUBLICATION_OID_REQUIRED,
            "base_oid required for checkpoint_publish",
        )
    if not isinstance(ref, str) or not REF_RE.match(ref):
        _fail(GrantRejectReason.MALFORMED, "ref required for checkpoint_publish")

    validate_checkpoint_grant_ref(ref)

    if is_protected_ref(ref, grant):
        _fail(GrantRejectReason.PROTECTED_REF, "grant.ref is a protected ref")

    execution_id = grant.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id.strip():
        _fail(GrantRejectReason.MALFORMED, "execution_id required for checkpoint_publish")

    revision = grant.get("grant_revision")
    if not isinstance(revision, int) or revision < 1:
        _fail(GrantRejectReason.MALFORMED, "grant_revision must be int >= 1")

    checkpoint = grant.get("checkpoint")
    if not isinstance(checkpoint, dict):
        _fail(GrantRejectReason.CHECKPOINT_CONFIG, "checkpoint object required")
    mode = checkpoint.get("mode")
    if mode not in CHECKPOINT_MODES:
        _fail(
            GrantRejectReason.CHECKPOINT_CONFIG,
            f"checkpoint.mode must be auto_commit_and_push; got {mode!r}",
        )
    if checkpoint.get("extra_go") is not False:
        _fail(
            GrantRejectReason.CHECKPOINT_CONFIG,
            "checkpoint.extra_go must be false",
        )
    idem = checkpoint.get("idempotency")
    if idem not in CHECKPOINT_IDEMPOTENCY:
        _fail(
            GrantRejectReason.CHECKPOINT_CONFIG,
            f"checkpoint.idempotency must be git_ff_oid; got {idem!r}",
        )

    attestation = grant.get("owner_attestation_ref")
    if isinstance(attestation, str) and RECEIPT_ATTESTATION_RE.search(attestation):
        _fail(
            GrantRejectReason.ATTESTATION_INVALID,
            "checkpoint receipt cannot be owner_attestation_ref",
        )


def authorize_checkpoint_surface(
    grant: Mapping[str, Any],
    *,
    surface: InvocationSurface,
    agent_id: str,
) -> None:
    """Поверхность и субъект для checkpoint_publish."""

    if surface == InvocationSurface.SUPERVISOR_SUBAGENT:
        _fail(
            GrantRejectReason.STABLE_POLICY_OVERRIDES_GRANT,
            "supervisor subagents cannot checkpoint_publish",
        )
    if surface not in {
        InvocationSurface.CLOUD_PARENT,
        InvocationSurface.VALIDATION_ONLY,
    }:
        _fail(
            CheckpointRejectReason.UNAUTHORIZED_SURFACE,
            f"surface {surface.value} cannot invoke checkpoint_publish",
        )

    subject = grant.get("subject")
    if not isinstance(subject, dict):
        _fail(GrantRejectReason.MALFORMED, "subject required")
    grant_agent = subject.get("agent_id")
    if surface == InvocationSurface.CLOUD_PARENT and grant_agent != agent_id:
        _fail(
            CheckpointRejectReason.UNAUTHORIZED_AGENT,
            "subject.agent_id must match Cloud parent",
        )


def bind_checkpoint_execution(
    grant: Mapping[str, Any],
    *,
    execution_id: str,
    grant_revision: int,
) -> None:
    """Отклонить stale или cross-execution grant."""

    grant_exec = grant.get("execution_id")
    if grant_exec != execution_id:
        _fail(
            CheckpointRejectReason.CROSS_EXECUTION,
            "execution_id does not match grant",
        )
    grant_rev = grant.get("grant_revision")
    if grant_rev != grant_revision:
        _fail(
            CheckpointRejectReason.STALE_REVISION,
            "grant_revision stale or ahead of execution",
        )


def transition_checkpoint_publication(
    grant: Mapping[str, Any],
    ctx: CheckpointPublishContext,
    *,
    is_ancestor: Callable[[str, str], bool] | None = None,
) -> CheckpointTransitionResult:
    """Детерминированный переход state machine для одной попытки."""

    if ctx.proposed_success_word in FORBIDDEN_SUCCESS_WORDS:
        return CheckpointTransitionResult(
            ok=False,
            phase=CheckpointPhase.BLOCKED_POLICY_VIOLATION,
            reason=CheckpointRejectReason.FABRICATED_SUCCESS,
            detail="checkpoint must not emit GREEN/PASS",
        )

    try:
        validate_checkpoint_grant_shape(grant)
        subject = grant.get("subject")
        grant_agent = (
            subject.get("agent_id")
            if isinstance(subject, dict) and isinstance(subject.get("agent_id"), str)
            else ""
        )
        bind_gate_invocation(
            str(grant.get("repo")),
            grant_agent,
            gate=ctx.gate,
            caller_surface=ctx.surface,
            caller_mint_channel=None,
        )
        if ctx.repo_remote is not None:
            if canonical_repo_slug(ctx.repo_remote) != str(grant.get("repo")):
                _fail(
                    CheckpointRejectReason.ALTERNATE_TARGET,
                    "repo_remote does not match grant.repo",
                )
        authorize_checkpoint_surface(
            grant,
            surface=ctx.surface,
            agent_id=ctx.agent_id,
        )
        bind_checkpoint_execution(
            grant,
            execution_id=ctx.execution_id,
            grant_revision=ctx.grant_revision,
        )
    except GrantValidationError as exc:
        return CheckpointTransitionResult(
            ok=False,
            phase=CheckpointPhase.GRANT_REJECTED,
            reason=exc.reason,
            detail=exc.detail,
        )

    grant_ref = normalize_ref(str(grant.get("ref")))
    remote_target = normalize_ref(ctx.remote_ref_target)
    head = normalize_ref(ctx.head_ref)

    if is_protected_ref(grant_ref, grant) or is_protected_ref(remote_target, grant):
        return CheckpointTransitionResult(
            ok=False,
            phase=CheckpointPhase.DENIED_PROTECTED_REF,
            reason=CheckpointRejectReason.PROTECTED_REF,
            detail="protected ref cannot be checkpoint target",
        )

    if remote_target != grant_ref:
        return CheckpointTransitionResult(
            ok=False,
            phase=CheckpointPhase.BLOCKED_POLICY_VIOLATION,
            reason=CheckpointRejectReason.ALTERNATE_TARGET,
            detail="push must target grant.ref only",
        )

    if head in protected_ref_set(grant):
        return CheckpointTransitionResult(
            ok=False,
            phase=CheckpointPhase.DENIED_PROTECTED_REF,
            reason=CheckpointRejectReason.PROTECTED_REF,
            detail="HEAD must not be on protected ref during checkpoint",
        )

    if ctx.force_push or ctx.delete_ref:
        return CheckpointTransitionResult(
            ok=False,
            phase=CheckpointPhase.CHECKPOINT_DIVERGED,
            reason=CheckpointRejectReason.FORCE_PUSH
            if ctx.force_push
            else CheckpointRejectReason.REF_DELETE,
            detail="force push and ref deletion forbidden",
        )

    if ctx.interrupted_mid_push and not ctx.remote_push_acknowledged:
        return CheckpointTransitionResult(
            ok=False,
            phase=CheckpointPhase.OWNER_ATTENTION,
            reason=CheckpointRejectReason.REMOTE_UNCERTAIN,
            detail="reconcile remote oid before retry; no fabricated success",
        )

    if (
        ctx.worktree_clean
        and ctx.local_equals_remote
        and ctx.local_tip_oid
        and ctx.remote_oid_before == ctx.local_tip_oid
        and is_full_git_oid(ctx.local_tip_oid)
        and is_full_git_oid(ctx.remote_oid_before)
    ):
        return CheckpointTransitionResult(
            ok=True,
            phase=CheckpointPhase.CHECKPOINT_IDEMPOTENT_NOOP,
            detail="already published",
        )

    if ctx.local_tip_oid and ctx.remote_oid_before:
        if not is_fast_forward_ancestor(
            ctx.remote_oid_before,
            ctx.local_tip_oid,
            is_ancestor=is_ancestor,
        ):
            return CheckpointTransitionResult(
                ok=False,
                phase=CheckpointPhase.CHECKPOINT_DIVERGED,
                reason=CheckpointRejectReason.NON_FF,
                detail="remote is not ancestor of local tip",
            )

    if ctx.remote_push_acknowledged and ctx.local_tip_oid and ctx.remote_oid_after:
        if ctx.remote_oid_after != ctx.local_tip_oid:
            return CheckpointTransitionResult(
                ok=False,
                phase=CheckpointPhase.OWNER_ATTENTION,
                reason=CheckpointRejectReason.REMOTE_UNCERTAIN,
                detail="remote oid must match receipt tip_oid",
            )
        return CheckpointTransitionResult(
            ok=True,
            phase=CheckpointPhase.CHECKPOINT_PUBLISHED,
            detail="remote confirmed",
        )

    if ctx.local_tip_oid and not ctx.remote_push_acknowledged:
        return CheckpointTransitionResult(
            ok=True,
            phase=CheckpointPhase.CHECKPOINT_PUSHING,
            detail="fast-forward push required",
        )

    if ctx.worktree_dirty and not ctx.local_tip_oid:
        return CheckpointTransitionResult(
            ok=True,
            phase=CheckpointPhase.CHECKPOINT_LOCAL,
            detail="commit required before push",
        )

    return CheckpointTransitionResult(
        ok=True,
        phase=CheckpointPhase.CHECKPOINT_ELIGIBLE,
        detail="eligible for checkpoint",
    )


def outcome_words(result: CheckpointTransitionResult) -> Sequence[str]:
    """Слова статуса для contract audit — checkpoint never GREEN/PASS."""

    return (result.phase.value,)
