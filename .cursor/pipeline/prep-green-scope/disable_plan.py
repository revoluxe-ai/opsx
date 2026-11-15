"""DisablePlan: baseline normalizer — suppress all non-PASS FULL obligations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from full_check import CheckPacket, FullStaticCheckResult
from pytest_wave_collect import collect_pytest_nodes_for_wave
from static_check_scope import inventory_mypy_modules, inventory_pyright_strict_files

PYTEST_CHECK_IDS = (
    "pytest-targeted",
    "pytest-unit",
    "pytest-integration",
    "pytest-destructive",
)

WAVE_A_STATIC_IDS = ("pyright-strict", "mypy")

_NON_PASS_STATUSES = frozenset({"FAIL", "BLOCKED", "ERROR", "NOT_EXECUTED"})

# Whole-wave pytest collect fallback: unexecutable / unknown-outcome waves only.
_PYTEST_WHOLE_WAVE_FALLBACK_STATUSES = frozenset({"BLOCKED", "NOT_EXECUTED", "ERROR"})

# Completeness: wave A + pytest (destructive only if present in handoff).


class DestructivePolicy(str, Enum):
    """Declared policy for handoff identity only — prep-green does not gate on it."""

    NOT_APPLICABLE = "not_applicable"
    REQUIRED = "required"
    UNRESOLVED = "unresolved"


class ProbeGate(str, Enum):
    AWAITING_GO = "AWAITING_GO"
    BLOCKED_INCOMPLETE_PROBE = "BLOCKED_INCOMPLETE_PROBE"
    BLOCKED_INCOMPLETE_HANDOFF = "BLOCKED_INCOMPLETE_HANDOFF"
    BLOCKED_MISSING_FULL_HANDOFF = "BLOCKED_MISSING_FULL_HANDOFF"
    OWNER_DECISION_REQUIRED = "OWNER_DECISION_REQUIRED"  # legacy artifact only


@dataclass
class DisablePlan:
    """Явные кандидаты отключения из одного полного FULL probe."""

    pyright_files: list[str] = field(default_factory=list)
    mypy_modules: list[str] = field(default_factory=list)
    pytest_nodes: list[str] = field(default_factory=list)
    probe_gate: ProbeGate = ProbeGate.AWAITING_GO
    gate_reasons: list[str] = field(default_factory=list)
    destructive_policy: DestructivePolicy = DestructivePolicy.UNRESOLVED
    source_fingerprint: str = ""

    @property
    def remaining_candidates(self) -> int:
        return (
            len(self.pyright_files)
            + len(self.mypy_modules)
            + len(self.pytest_nodes)
        )


def plan_signature(plan: DisablePlan) -> tuple[str, ...]:
    """Stable causal fingerprint of disable candidates."""

    parts = (
        [f"p:{p}" for p in plan.pyright_files]
        + [f"m:{m}" for m in plan.mypy_modules]
        + [f"t:{n}" for n in plan.pytest_nodes]
    )
    return tuple(sorted(parts))


def resolve_destructive_policy(capabilities: dict[str, Any]) -> DestructivePolicy:
    """Read declared policy when present; never infer applicability for prep-green."""

    quality = capabilities.get("quality") or {}
    sc = quality.get("static_check") or {}
    full = sc.get("full") or {}
    raw = full.get("pytest_destructive")
    if raw is None:
        return DestructivePolicy.UNRESOLVED
    val = str(raw).strip().lower()
    if val in {"not_applicable", "not-applicable", "na"}:
        return DestructivePolicy.NOT_APPLICABLE
    if val in {"required", "mandatory"}:
        return DestructivePolicy.REQUIRED
    return DestructivePolicy.UNRESOLVED


def _path_to_mypy_module(path: str) -> str:
    p = path.replace("\\", "/")
    if p.endswith(".py"):
        p = p[: -len(".py")]
    return p.replace("/", ".").removeprefix("src.")


def _status_yields_candidates(packet: CheckPacket) -> bool:
    if packet.status == "PASS":
        return False
    if packet.status == "SKIPPED":
        return False
    return packet.status in _NON_PASS_STATUSES or packet.status not in {"PASS", "SKIPPED"}


def _collect_pyright(plan: DisablePlan, packet: CheckPacket, *, repo_root: Path | None) -> None:
    if not _status_yields_candidates(packet):
        return
    before = len(plan.pyright_files)
    for prob in packet.static_problems:
        rel = prob.path.split(":")[0]
        if rel and rel not in plan.pyright_files:
            plan.pyright_files.append(rel)
    if repo_root is not None and len(plan.pyright_files) == before:
        for rel in inventory_pyright_strict_files(repo_root):
            if rel not in plan.pyright_files:
                plan.pyright_files.append(rel)
    plan.pyright_files.sort()


def _collect_mypy(plan: DisablePlan, packet: CheckPacket, *, repo_root: Path | None) -> None:
    if not _status_yields_candidates(packet):
        return
    before = len(plan.mypy_modules)
    for prob in packet.static_problems:
        mod = _path_to_mypy_module(prob.path.split(":")[0])
        if mod and mod not in plan.mypy_modules:
            plan.mypy_modules.append(mod)
    if repo_root is not None and len(plan.mypy_modules) == before:
        for mod in inventory_mypy_modules(repo_root):
            if mod not in plan.mypy_modules:
                plan.mypy_modules.append(mod)
    plan.mypy_modules.sort()


def pytest_packet_allows_whole_wave_fallback(packet: CheckPacket) -> bool:
    """Marker inventory only when the wave did not yield individual failure outcomes."""

    if packet.status in _PYTEST_WHOLE_WAVE_FALLBACK_STATUSES:
        return True
    if "suite_not_executed" in packet.notes:
        return True
    if packet.status == "FAIL" and packet.executed_count == 0:
        return True
    return False


def _collect_pytest(
    plan: DisablePlan,
    packet: CheckPacket,
    *,
    check_id: str,
    repo_root: Path | None,
) -> bool:
    """Collect pytest skip candidates; return False when executed FAIL lacks node ids."""

    if not _status_yields_candidates(packet):
        return True
    before = len(plan.pytest_nodes)
    for prob in packet.test_problems:
        if prob.node and prob.node not in plan.pytest_nodes:
            plan.pytest_nodes.append(prob.node)
    added = len(plan.pytest_nodes) > before
    if added:
        plan.pytest_nodes.sort()
        return True
    if packet.status == "FAIL" and not pytest_packet_allows_whole_wave_fallback(packet):
        return False
    if repo_root is not None:
        wave = collect_pytest_nodes_for_wave(repo_root, check_id)
        for node in wave.nodes:
            if node not in plan.pytest_nodes:
                plan.pytest_nodes.append(node)
    plan.pytest_nodes.sort()
    return True


def _probe_completeness_reasons(full: FullStaticCheckResult) -> list[str]:
    """Missing scheduled check packets block STAGE1; absent pytest-destructive is OK."""

    reasons: list[str] = []
    always = (
        "pyright-strict",
        "mypy",
        "pytest-targeted",
        "pytest-unit",
        "pytest-integration",
    )
    for cid in always:
        if cid == "pytest-targeted":
            packet = full.check(cid)
            if packet and packet.status == "SKIPPED":
                continue
        packet = full.check(cid)
        if packet is None:
            reasons.append(f"{cid}: missing from handoff")
    if full.check("pytest-destructive") is None:
        return reasons
    return reasons


def extract_disable_plan(
    full: FullStaticCheckResult,
    *,
    capabilities: dict[str, Any],
    repo_root: Path | None = None,
) -> DisablePlan:
    """FULL → DisablePlan; non-PASS → native suppressions (scheduled checks only)."""

    destructive_policy = resolve_destructive_policy(capabilities)
    plan = DisablePlan(
        source_fingerprint=full.fingerprint,
        destructive_policy=destructive_policy,
    )

    if not full.handoff_complete:
        plan.probe_gate = ProbeGate.BLOCKED_INCOMPLETE_HANDOFF
        plan.gate_reasons = list(full.handoff_incomplete_reasons) or [
            "PREP_GREEN_FULL_HANDOFF incomplete (truncated findings)"
        ]
        return plan

    completeness = _probe_completeness_reasons(full)
    if completeness:
        plan.probe_gate = ProbeGate.BLOCKED_INCOMPLETE_PROBE
        plan.gate_reasons = completeness
        return plan

    pyright = full.check("pyright-strict")
    mypy = full.check("mypy")
    if pyright:
        _collect_pyright(plan, pyright, repo_root=repo_root)
    if mypy:
        _collect_mypy(plan, mypy, repo_root=repo_root)

    for cid in PYTEST_CHECK_IDS:
        packet = full.check(cid)
        if packet is None:
            continue
        if cid == "pytest-targeted" and packet.status == "SKIPPED":
            continue
        if not _collect_pytest(plan, packet, check_id=cid, repo_root=repo_root):
            plan.probe_gate = ProbeGate.BLOCKED_INCOMPLETE_HANDOFF
            plan.gate_reasons = [
                f"{cid}: executed FAIL without failed node ids in handoff"
            ]
            return plan

    plan.probe_gate = ProbeGate.AWAITING_GO
    plan.gate_reasons = []
    return plan
