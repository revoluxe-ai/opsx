# -*- coding: utf-8 -*-
"""Production host dispatch для /opsx-bug-team (Cursor command entry).

Единая наблюдаемая граница между command markdown и durable routing:

``dispatch_opsx_bug_team`` → ``route_bug_team_command`` → ``OrchestratorRuntime``.

Не запускает Task workers (host chat делает это отдельно после dispatch).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .bug_team_command_routing import (
    CommandCaseRoute,
    CommandEntryClass,
    open_orchestrator_for_route,
    route_bug_team_command,
    route_evidence_digest,
)
from .orchestrator import OrchestratorRuntime

HOST_ENTRYPOINT = "/opsx-bug-team"
HOST_DISPATCH_MODULE = "runtime.bug_team_host.dispatch_opsx_bug_team"
CLI_DISPATCH_SCRIPT = ".cursor/pipeline/bug-team/harness/opsx_bug_team_host_dispatch.py"


def resolve_repo_root(start: Optional[Path] = None) -> Path:
    """Git toplevel для host dispatch (fail-closed вне репозитория)."""
    cwd = Path(start or Path.cwd())
    out = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        text=True,
    ).strip()
    return Path(out).resolve()


def _revision(repo_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
    ).strip()


@dataclass(frozen=True)
class BugTeamHostDispatchResult:
    """Результат host dispatch (machine-checkable receipt)."""

    entrypoint: str
    revision: str
    project_name: Optional[str]
    case_id: str
    case_root: Path
    store_root: Optional[Path]
    entry_class: CommandEntryClass
    gate_status: str
    orchestrator: OrchestratorRuntime
    route: CommandCaseRoute
    evidence_digest: str

    def to_receipt(self) -> Dict[str, Any]:
        """JSON-serializable receipt (без orchestrator object)."""
        return {
            "entrypoint": self.entrypoint,
            "revision": self.revision,
            "project_name": self.project_name,
            "case_id": self.case_id,
            "case_root": str(self.case_root),
            "store_root": str(self.store_root) if self.store_root else None,
            "entry_class": self.entry_class.value,
            "gate_status": self.gate_status,
            "evidence_digest": self.evidence_digest,
            "host_dispatch_module": HOST_DISPATCH_MODULE,
        }


def dispatch_opsx_bug_team(
    args: str,
    *,
    repo_root: Optional[Path] = None,
    context_text: str = "",
    execution_mode: str = "CONTROLLED_LIVE_UNCOMMITTED",
    proof_root: Optional[Path] = None,
    acceptance_store_root: Optional[Path] = None,
    synthetic_root: Optional[Path] = None,
) -> BugTeamHostDispatchResult:
    """
    Host dispatch: activation-aware routing + OrchestratorRuntime.

    Args:
        args: Строка аргументов /opsx-bug-team (как $ARGUMENTS).
        repo_root: Git root; по умолчанию ``git rev-parse --show-toplevel`` от cwd.
        acceptance_store_root: Только harness — изолированный fleet root.
    """
    root = Path(repo_root) if repo_root else resolve_repo_root()
    rev = _revision(root)

    route = route_bug_team_command(
        args,
        repo_root=root,
        context_text=context_text,
        execution_mode=execution_mode,
        proof_root=proof_root,
        acceptance_store_root=acceptance_store_root,
        synthetic_root=synthetic_root,
    )
    orch = open_orchestrator_for_route(route)
    digest = route_evidence_digest(route)
    project_name = route.project_id or (
        route.resolved.project.project_name if route.resolved else None
    )
    return BugTeamHostDispatchResult(
        entrypoint=HOST_ENTRYPOINT,
        revision=rev,
        project_name=project_name,
        case_id=route.case_id,
        case_root=route.case_root,
        store_root=route.store_root,
        entry_class=route.entry_class,
        gate_status=route.gate_status,
        orchestrator=orch,
        route=route,
        evidence_digest=digest,
    )


def receipt_digest(receipt: Dict[str, Any]) -> str:
    """Stable digest для acceptance evidence."""
    payload = json.dumps(receipt, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
