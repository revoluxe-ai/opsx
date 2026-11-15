# -*- coding: utf-8 -*-
"""Registry execution — explicit canonical argv only (no Markdown inference)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

# Approved bounded-subprocess authority (check_id → exact bash -c body).
CANONICAL_BASH_COMMAND: dict[str, str] = {
    "pyright-strict": "bash .cursor/static-check/bin/run.sh -- pyright-strict",
    "mypy": "bash .cursor/static-check/bin/run.sh -- mypy .",
    "opsx-split-apply-impl-ready": (
        "python3 .cursor/pipeline/bounded-verify/gates/run_gate.py "
        "opsx-split-apply-impl-ready"
    ),
    "opsx-apply-green-fresh": (
        "python3 .cursor/pipeline/bounded-verify/gates/run_gate.py "
        "opsx-apply-green-fresh"
    ),
    "opsx-scenario-plan-receipt": (
        "python3 .cursor/pipeline/bounded-verify/gates/run_gate.py "
        "opsx-scenario-plan-receipt"
    ),
    "opsx-pg-provider-receipt": (
        "python3 .cursor/pipeline/bounded-verify/gates/run_gate.py "
        "opsx-pg-provider-receipt"
    ),
}

CANONICAL_POLICY_REVISION: dict[str, str] = {
    check_id: hashlib.sha256(cmd.encode("utf-8")).hexdigest()
    for check_id, cmd in CANONICAL_BASH_COMMAND.items()
}


class RegistryExecutionSupport(str, Enum):
    MACHINE_EXECUTABLE = "MACHINE_EXECUTABLE"
    UNSUPPORTED = "UNSUPPORTED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class RegistryExecutionContract:
    check_id: str
    support: RegistryExecutionSupport
    argv: tuple[str, ...] | None
    reason: str
    task_type: str
    policy_revision: str = ""


def _argv_for_canonical(command: str) -> tuple[str, ...]:
    return ("bash", "-c", command)


def _pack_instructions_match_canonical(instructions_text: str, canonical: str) -> bool:
    normalized = instructions_text.replace("\r\n", "\n")
    return canonical in normalized


def resolve_registry_execution_contract(
    check_id: str,
    registry_path: Path,
    *,
    root: Path = REPO_ROOT,
) -> RegistryExecutionContract:
    """Resolve bounded subprocess contract from explicit canonical mapping."""
    if not registry_path.is_file():
        return RegistryExecutionContract(
            check_id,
            RegistryExecutionSupport.BLOCKED,
            None,
            "registry not found",
            "",
        )
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    checks = data.get("checks") if isinstance(data, dict) else None
    if not isinstance(checks, dict) or check_id not in checks:
        return RegistryExecutionContract(
            check_id,
            RegistryExecutionSupport.BLOCKED,
            None,
            "unknown check_id",
            "",
        )
    entry: Mapping[str, object] = checks[check_id]
    task_type = str(entry.get("task_type", ""))
    pack_rel = str(entry.get("pack", ""))

    if check_id in ("pytest-destructive",):
        return RegistryExecutionContract(
            check_id,
            RegistryExecutionSupport.UNSUPPORTED,
            None,
            "destructive_local requires separate Owner authority (wave D)",
            task_type,
        )
    if check_id not in CANONICAL_BASH_COMMAND:
        return RegistryExecutionContract(
            check_id,
            RegistryExecutionSupport.UNSUPPORTED,
            None,
            f"no canonical bounded argv for {check_id}",
            task_type,
        )
    if task_type != "shell":
        return RegistryExecutionContract(
            check_id,
            RegistryExecutionSupport.UNSUPPORTED,
            None,
            f"task_type={task_type} is not bounded subprocess authority",
            task_type,
        )

    canonical = CANONICAL_BASH_COMMAND[check_id]
    instructions = root / pack_rel / "instructions.md"
    if not instructions.is_file():
        return RegistryExecutionContract(
            check_id,
            RegistryExecutionSupport.BLOCKED,
            None,
            "instructions.md missing",
            task_type,
        )
    if not _pack_instructions_match_canonical(
        instructions.read_text(encoding="utf-8"), canonical
    ):
        return RegistryExecutionContract(
            check_id,
            RegistryExecutionSupport.BLOCKED,
            None,
            "pack instructions do not contain approved canonical command",
            task_type,
            policy_revision=CANONICAL_POLICY_REVISION[check_id],
        )
    return RegistryExecutionContract(
        check_id,
        RegistryExecutionSupport.MACHINE_EXECUTABLE,
        _argv_for_canonical(canonical),
        "explicit canonical argv",
        task_type,
        policy_revision=CANONICAL_POLICY_REVISION[check_id],
    )


def registry_check_support_matrix(
    *,
    root: Path = REPO_ROOT,
    registry_path: Path | None = None,
) -> dict[str, str]:
    """Support matrix for the six registry checks (read-only)."""
    from bounded_verify_plan import DEFAULT_REGISTRY_PATH

    reg = registry_path or DEFAULT_REGISTRY_PATH
    ids = (
        "pyright-strict",
        "mypy",
        "pytest-targeted",
        "pytest-unit",
        "pytest-integration",
        "pytest-destructive",
    )
    return {
        cid: resolve_registry_execution_contract(cid, reg, root=root).support.value
        for cid in ids
    }
