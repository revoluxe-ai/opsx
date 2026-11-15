"""Контекст доверия gate для OPSX-2 S4a (VALIDATION_ONLY vs git-backed).

Поля surface, mint_channel и repo_remote MUST поступать от gate/credential,
а не только от worker-asserted аргументов валидатора.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable

from minimal_authority_grant import (
    GrantRejectReason,
    InvocationSurface,
    _fail,
    normalize_ref,
)

CANONICAL_REPO_SLUG = "revoluxe-ai/stroy-system"

_OWNER_MINT_CHANNELS = frozenset({"owner", "issuer", "OWNER", "ISSUER"})

CHECKPOINT_GRANT_REF_PREFIX = "refs/heads/cursor/"

_FULL_GIT_OID_RE = re.compile(r"^[0-9a-f]{40}$")

CHECKPOINT_DENIED_BRANCH_NAMES: frozenset[str] = frozenset({"dev", "mvp"})

CHECKPOINT_DENIED_BRANCH_PREFIXES: tuple[str, ...] = ("deploy/",)

_INVALID_REF_SEGMENT_RE = re.compile(r"\.\.|//|^/|/\.")

AncestorPredicate = Callable[[str, str], bool]


@dataclass(frozen=True, slots=True)
class GateDerivedInvocation:
    """Инварианты invocation, выведенные gate из credential (не worker)."""

    surface: InvocationSurface
    mint_channel: str
    repo_remote: str
    agent_id: str


def canonical_repo_slug(repo_remote: str) -> str:
    """Свести remote URL к slug owner/repo для сравнения с grant.repo."""

    trimmed = repo_remote.strip().rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[:-4]
    if trimmed.startswith("git@github.com:"):
        trimmed = trimmed.removeprefix("git@github.com:")
    if "github.com/" in trimmed:
        trimmed = trimmed.split("github.com/", 1)[1]
    if trimmed != CANONICAL_REPO_SLUG:
        _fail(
            "CHECKPOINT_REJECTED_ALTERNATE_REMOTE",
            f"repo_remote must be {CANONICAL_REPO_SLUG}; got {repo_remote!r}",
        )
    return CANONICAL_REPO_SLUG


def bind_gate_invocation(
    grant_repo: str,
    grant_agent_id: str,
    *,
    gate: GateDerivedInvocation | None,
    caller_surface: InvocationSurface,
    caller_mint_channel: str | None,
) -> None:
    """Сверить gate-derived поля с grant и отклонить caller-only assertion."""

    if gate is None:
        _fail(
            GrantRejectReason.MALFORMED,
            "gate_derived_invocation required for checkpoint_publish validation",
        )
    if gate.mint_channel not in _OWNER_MINT_CHANNELS:
        _fail(
            GrantRejectReason.WORKER_MINT,
            "mint_channel must be Owner-issued (owner/issuer)",
        )
    if caller_mint_channel in {"worker", "WORKER", "self_asserted"}:
        _fail(
            GrantRejectReason.WORKER_MINT,
            "workers cannot mint authority grants",
        )
    if gate.mint_channel in {"worker", "WORKER", "self_asserted"}:
        _fail(
            GrantRejectReason.WORKER_MINT,
            "gate mint_channel cannot be worker-asserted",
        )
    if gate.surface != caller_surface:
        _fail(
            "CHECKPOINT_REJECTED_UNAUTHORIZED_SURFACE",
            "caller surface must match gate-derived surface",
        )
    if canonical_repo_slug(gate.repo_remote) != grant_repo:
        _fail(
            "CHECKPOINT_REJECTED_ALTERNATE_REMOTE",
            "repo_remote does not match grant.repo",
        )
    if gate.agent_id != grant_agent_id:
        _fail(
            "CHECKPOINT_REJECTED_UNAUTHORIZED_AGENT",
            "gate agent_id must match grant subject",
        )


def is_checkpoint_denied_branch_name(branch_name: str) -> bool:
    """Checkpoint deny-list: dev, mvp, deploy/* (Owner policy S4a)."""

    if branch_name in CHECKPOINT_DENIED_BRANCH_NAMES:
        return True
    return any(
        branch_name.startswith(prefix) for prefix in CHECKPOINT_DENIED_BRANCH_PREFIXES
    )


def validate_checkpoint_grant_ref(ref: str) -> str:
    """Allow-list cursor/* с запретом path traversal и checkpoint deny refs."""

    normalized = normalize_ref(ref)
    if not normalized.startswith("refs/heads/"):
        normalized = f"refs/heads/{normalized}"
    if _INVALID_REF_SEGMENT_RE.search(normalized):
        _fail(GrantRejectReason.MALFORMED, "ref contains invalid path segments")
    if ".." in normalized:
        _fail(GrantRejectReason.MALFORMED, "ref must not contain ..")
    branch = normalized.removeprefix("refs/heads/")
    if is_checkpoint_denied_branch_name(branch):
        _fail(
            GrantRejectReason.PROTECTED_REF,
            "checkpoint ref is on Owner deny-list (dev/mvp/deploy/*)",
        )
    if not normalized.startswith(CHECKPOINT_GRANT_REF_PREFIX):
        _fail(
            GrantRejectReason.MALFORMED,
            "checkpoint grant.ref must be under refs/heads/cursor/",
        )
    tail = normalized[len(CHECKPOINT_GRANT_REF_PREFIX) :]
    if not tail or tail.startswith("/"):
        _fail(GrantRejectReason.MALFORMED, "checkpoint ref must name a cursor branch")
    return normalized


def is_full_git_oid(oid: str) -> bool:
    """True только для канонического 40-символьного lowercase hex commit oid."""

    return _FULL_GIT_OID_RE.fullmatch(oid) is not None


def git_merge_base_is_ancestor(ancestor: str, descendant: str) -> bool | None:
    """True/False через git merge-base; None если git недоступен или oid неизвестен."""

    try:
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def is_fast_forward_ancestor(
    remote_oid: str,
    local_tip: str,
    *,
    is_ancestor: AncestorPredicate | None = None,
) -> bool:
    """FF-проверка: реальная ancestry; префикс oid без git не считается FF (S4a / F1)."""

    if not is_full_git_oid(remote_oid) or not is_full_git_oid(local_tip):
        return False
    if remote_oid == local_tip:
        return True
    if is_ancestor is not None:
        return is_ancestor(remote_oid, local_tip)
    git_result = git_merge_base_is_ancestor(remote_oid, local_tip)
    if git_result is not None:
        return git_result
    return False
