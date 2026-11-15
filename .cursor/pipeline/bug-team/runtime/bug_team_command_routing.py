# -*- coding: utf-8 -*-
"""Маршрутизация /opsx-bug-team к durable project store (V0 integration).

Связывает parse invocation → case root → OrchestratorRuntime без repo-local fallback.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import activation
from .bug_team_invocation import parse_bug_team_invocation
from .case_store_resolver import (
    CaseRootMode,
    DurableStoreUnresolved,
    ResolvedCaseRoot,
    create_new_durable_project_case,
    generate_case_uuid,
    resolve_durable_project_case,
    resolve_durable_store_root,
    resolve_project_identity,
    resolve_synthetic_test_case,
)
from .causal_history.config import load_settings
from .causal_history.store import CausalEventStore
from .orchestrator import OrchestratorRuntime


class CommandEntryClass(str, Enum):
    """Классификация command entrypoint для audit / acceptance."""

    DURABLE_REAL_CASE = "DURABLE_REAL_CASE"
    SYNTHETIC_TEST_CASE = "SYNTHETIC_TEST_CASE"
    EXPLICIT_LEGACY_CASE = "EXPLICIT_LEGACY_CASE"
    READ_ONLY_HISTORY_ACCESS = "READ_ONLY_HISTORY_ACCESS"
    BLOCKED = "BLOCKED"


class CommandRoutingErrorCode(str, Enum):
    """Коды блокировки command routing."""

    BLOCKED_NOT_ACTIVE = "BLOCKED_NOT_ACTIVE"
    BLOCKED_AMBIGUOUS = "BLOCKED_AMBIGUOUS"
    BLOCKED_INTAKE_REQUIRED = "BLOCKED_INTAKE_REQUIRED"
    CASE_NOT_FOUND = "CASE_NOT_FOUND"
    LEGACY_REDIRECT_FORBIDDEN = "LEGACY_REDIRECT_FORBIDDEN"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"


class CommandRoutingBlocked(RuntimeError):
    """Fail-closed command routing."""

    def __init__(self, code: CommandRoutingErrorCode, detail: str, recovery: str) -> None:
        self.code = code
        self.detail = detail
        self.recovery = recovery
        super().__init__(f"{code.value}: {detail}")


@dataclass(frozen=True)
class CommandCaseRoute:
    """Результат маршрутизации команды."""

    entry_class: CommandEntryClass
    case_id: str
    case_root: Path
    project_id: Optional[str]
    store_root: Optional[Path]
    invocation: Dict[str, Any]
    resolved: Optional[ResolvedCaseRoot]
    gate_status: str
    revision_binding: str


def allocate_bug_case_id() -> str:
    """Новый CASE_ID (UUIDv4) для durable real case."""
    return generate_case_uuid()


def _git_toplevel(repo_root: Path) -> Path:
    import subprocess

    out = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo_root,
        text=True,
    ).strip()
    return Path(os.path.realpath(out))


def legacy_repo_local_case_root(repo_root: Path, case_id: str) -> Path:
    """Явный legacy путь (не durable, не auto-migrate)."""
    return _git_toplevel(repo_root) / "opsx" / "runs" / "bug-cases" / case_id


def _durable_case_exists(resolved: ResolvedCaseRoot) -> bool:
    """True when durable case directory is present (including freshly created UUID dir)."""
    return resolved.case_root.is_dir()


def _check_activation_gate(
    *,
    execution_mode: str,
    proof_root: Optional[Path],
    acceptance_store_root: Optional[Path],
) -> str:
    if acceptance_store_root is not None:
        return "DURABLE_ACCEPTANCE_ALLOWED"
    status, _details = activation.check_invocation_gate(
        execution_mode=execution_mode,
        proof_root=proof_root,
    )
    if status in (
        "ACTIVE_ALLOWED",
        "CONTROLLED_UNCOMMITTED_PROOF_ALLOWED",
        "PROOF_MODE_ALLOWED",
    ):
        return status
    raise CommandRoutingBlocked(
        CommandRoutingErrorCode.BLOCKED_NOT_ACTIVE,
        status,
        "Owner activation required for durable real-case routing",
    )


def route_bug_team_command(
    args: str,
    *,
    repo_root: Path,
    context_text: str = "",
    execution_mode: str = "NORMAL",
    proof_root: Optional[Path] = None,
    acceptance_store_root: Optional[Path] = None,
    synthetic_root: Optional[Path] = None,
) -> CommandCaseRoute:
    """
    Production routing: invocation → durable / legacy / synthetic case root.

    Args:
        repo_root: Git root проекта.
        acceptance_store_root: Только harness — изолированный fleet root (обходит activation).
        synthetic_root: Для REVIEW_PROOF_ONLY harness synthetic cases.
    """
    parsed = parse_bug_team_invocation(args, context_text)
    mode = parsed.get("MODE", "")
    if mode == "BLOCKED_AMBIGUOUS_INVOCATION":
        raise CommandRoutingBlocked(
            CommandRoutingErrorCode.BLOCKED_AMBIGUOUS,
            str(parsed),
            "Use either case= or free-form intake, not both",
        )
    if mode == "BLOCKED_INTAKE_REQUIRED":
        raise CommandRoutingBlocked(
            CommandRoutingErrorCode.BLOCKED_INTAKE_REQUIRED,
            "empty invocation",
            "Provide bug description or case=",
        )

    gate_status = _check_activation_gate(
        execution_mode=execution_mode,
        proof_root=proof_root,
        acceptance_store_root=acceptance_store_root,
    )

    env_prev = os.environ.get("OPSX_BUG_CASE_STORE_ROOT")
    if acceptance_store_root is not None:
        os.environ["OPSX_BUG_CASE_STORE_ROOT"] = str(acceptance_store_root)
    try:
        return _route_with_store(parsed, repo_root, gate_status, synthetic_root, execution_mode)
    finally:
        if acceptance_store_root is not None:
            if env_prev is None:
                os.environ.pop("OPSX_BUG_CASE_STORE_ROOT", None)
            else:
                os.environ["OPSX_BUG_CASE_STORE_ROOT"] = env_prev


def _route_with_store(
    parsed: Dict[str, Any],
    repo_root: Path,
    gate_status: str,
    synthetic_root: Optional[Path],
    execution_mode: str,
) -> CommandCaseRoute:
    import subprocess

    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
    ).strip()
    mode = parsed["MODE"]

    if execution_mode == "REVIEW_PROOF_ONLY" and synthetic_root is not None:
        case_id = parsed.get("CASE_ID") or allocate_bug_case_id()
        resolved = resolve_synthetic_test_case(case_id, synthetic_root, repo_root=repo_root)
        return CommandCaseRoute(
            entry_class=CommandEntryClass.SYNTHETIC_TEST_CASE,
            case_id=case_id,
            case_root=resolved.case_root,
            project_id=resolved.project.project_id,
            store_root=resolved.store_root,
            invocation=parsed,
            resolved=resolved,
            gate_status=gate_status,
            revision_binding=revision,
        )

    intake_modes = {"FREE_FORM", "EXPLICIT_SIGNAL", "CURRENT_CONTEXT"}
    if mode in intake_modes:
        resolved = create_new_durable_project_case(repo_root=repo_root)
        case_id = resolved.case_id
        return CommandCaseRoute(
            entry_class=CommandEntryClass.DURABLE_REAL_CASE,
            case_id=case_id,
            case_root=resolved.case_root,
            project_id=resolved.project.project_id,
            store_root=resolved.store_root,
            invocation=parsed,
            resolved=resolved,
            gate_status=gate_status,
            revision_binding=revision,
        )

    if mode == "CASE_RESUME":
        case_id = parsed["CASE_ID"]
        resolved_peek = resolve_durable_project_case(
            case_id, repo_root=repo_root, create_if_missing=False
        )
        if _durable_case_exists(resolved_peek):
            return CommandCaseRoute(
                entry_class=CommandEntryClass.DURABLE_REAL_CASE,
                case_id=case_id,
                case_root=resolved_peek.case_root,
                project_id=resolved_peek.project.project_id,
                store_root=resolved_peek.store_root,
                invocation=parsed,
                resolved=resolved_peek,
                gate_status=gate_status,
                revision_binding=revision,
            )
        legacy = legacy_repo_local_case_root(repo_root, case_id)
        if legacy.is_dir():
            return CommandCaseRoute(
                entry_class=CommandEntryClass.EXPLICIT_LEGACY_CASE,
                case_id=case_id,
                case_root=legacy,
                project_id=None,
                store_root=None,
                invocation=parsed,
                resolved=None,
                gate_status=gate_status,
                revision_binding=revision,
            )
        raise CommandRoutingBlocked(
            CommandRoutingErrorCode.CASE_NOT_FOUND,
            case_id,
            "Case not in durable store or authorized legacy path",
        )

    raise CommandRoutingBlocked(
        CommandRoutingErrorCode.BLOCKED_INTAKE_REQUIRED,
        mode,
        "Unsupported invocation mode",
    )


def open_orchestrator_for_route(route: CommandCaseRoute) -> OrchestratorRuntime:
    """OrchestratorRuntime для маршрута (без silent legacy→durable redirect)."""
    if route.entry_class == CommandEntryClass.DURABLE_REAL_CASE:
        assert route.resolved is not None
        return OrchestratorRuntime(
            case_root=route.resolved.case_root,
            case_root_mode=CaseRootMode.DURABLE_PROJECT_CASE,
            resolved_case=route.resolved,
        )
    if route.entry_class == CommandEntryClass.SYNTHETIC_TEST_CASE:
        assert route.resolved is not None
        return OrchestratorRuntime(
            case_root=route.resolved.case_root,
            execution_mode="REVIEW_PROOF_ONLY",
            proof_root=route.resolved.case_root,
            case_root_mode=CaseRootMode.SYNTHETIC_TEST_CASE,
            resolved_case=route.resolved,
        )
    if route.entry_class == CommandEntryClass.EXPLICIT_LEGACY_CASE:
        return OrchestratorRuntime(
            case_root=route.case_root,
            execution_mode="NORMAL",
        )
    raise CommandRoutingBlocked(
        CommandRoutingErrorCode.LEGACY_REDIRECT_FORBIDDEN,
        route.entry_class.value,
        "Cannot open orchestrator for read-only route",
    )


def read_durable_case_history(
    case_id: str,
    *,
    repo_root: Path,
    acceptance_store_root: Optional[Path] = None,
) -> Tuple[CommandCaseRoute, list]:
    """
    READ_ONLY_HISTORY_ACCESS: replay causal events без publish/mutate.
    """
    env_prev = os.environ.get("OPSX_BUG_CASE_STORE_ROOT")
    if acceptance_store_root is not None:
        os.environ["OPSX_BUG_CASE_STORE_ROOT"] = str(acceptance_store_root)
    try:
        resolved = resolve_durable_project_case(
            case_id, repo_root=repo_root, create_if_missing=False
        )
        if not _durable_case_exists(resolved):
            raise CommandRoutingBlocked(
                CommandRoutingErrorCode.CASE_NOT_FOUND,
                case_id,
                "No durable case history",
            )
        events = CausalEventStore(resolved.case_root, case_id).replay_deterministic()
        route = CommandCaseRoute(
            entry_class=CommandEntryClass.READ_ONLY_HISTORY_ACCESS,
            case_id=case_id,
            case_root=resolved.case_root,
            project_id=resolved.project.project_id,
            store_root=resolved.store_root,
            invocation={"MODE": "READ_ONLY", "CASE_ID": case_id},
            resolved=resolved,
            gate_status="READ_ONLY",
            revision_binding="",
        )
        return route, events
    except DurableStoreUnresolved as exc:
        raise CommandRoutingBlocked(
            CommandRoutingErrorCode.IDENTITY_UNRESOLVED,
            exc.detail,
            exc.recovery,
        ) from exc
    finally:
        if acceptance_store_root is not None:
            if env_prev is None:
                os.environ.pop("OPSX_BUG_CASE_STORE_ROOT", None)
            else:
                os.environ["OPSX_BUG_CASE_STORE_ROOT"] = env_prev


def assert_no_repo_local_fallback_for_real(route: CommandCaseRoute) -> None:
    """CMD-05: real durable routes must not resolve under worktree bug-cases."""
    if route.entry_class != CommandEntryClass.DURABLE_REAL_CASE:
        return
    from .case_store_resolver import assert_not_repo_local_fallback

    assert_not_repo_local_fallback(route.case_root, route.resolved.project.repo_root)


def live_v3_append_disabled(route: CommandCaseRoute) -> bool:
    """CMD-10: default settings keep live V3 off."""
    settings = load_settings(route.case_root)
    return not settings.live_v3_append_enabled


def route_evidence_digest(route: CommandCaseRoute) -> str:
    """Stable digest for acceptance receipts."""
    payload = f"{route.case_id}|{route.entry_class.value}|{route.case_root}|{route.project_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_store_without_fallback(
    repo_root: Path,
    *,
    acceptance_store_root: Optional[Path],
) -> Path:
    """CMD-05 helper: missing env uses ~/opsx default, never repo-local."""
    env_prev = os.environ.get("OPSX_BUG_CASE_STORE_ROOT")
    if acceptance_store_root is not None:
        os.environ["OPSX_BUG_CASE_STORE_ROOT"] = str(acceptance_store_root)
    try:
        project = resolve_project_identity(repo_root)
        if acceptance_store_root is None and not os.environ.get("OPSX_BUG_CASE_STORE_ROOT"):
            return resolve_durable_store_root(project)
        return resolve_durable_store_root(project)
    except DurableStoreUnresolved:
        raise
    finally:
        if acceptance_store_root is not None:
            if env_prev is None:
                os.environ.pop("OPSX_BUG_CASE_STORE_ROOT", None)
            else:
                os.environ["OPSX_BUG_CASE_STORE_ROOT"] = env_prev
