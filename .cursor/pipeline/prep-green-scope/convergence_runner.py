"""Convergence transition + sequential runner (apply → run FULL → decide)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from apply_disable import ApplyDeltaResult, apply_disable_delta, prepare_delta
from disable_plan import (
    DestructivePolicy,
    DisablePlan,
    ProbeGate,
    extract_disable_plan,
    plan_signature,
    resolve_destructive_policy,
)
from full_check import FullStaticCheckResult

DEFAULT_SAFETY_MAX_ITERATIONS = 12


class ConvergenceStatus(str, Enum):
    PASS = "PASS"
    PAUSE = "PAUSE"
    FAIL = "FAIL"


class Transition(str, Enum):
    PASS = "PASS"
    PAUSE = "PAUSE"
    APPLY_DELTA = "APPLY_DELTA"
    RUN_FULL = "RUN_FULL"


@dataclass
class ConvergenceState:
    applies: int = 0
    last_signature: tuple[str, ...] | None = None
    last_fingerprint: str = ""


@dataclass
class ConvergenceVerdict:
    status: ConvergenceStatus
    verdict: str
    iterations: int
    last_full_status: str
    remaining_disable_candidates: int
    blocked_required_checks: int
    reasons: list[str]
    first_full_fingerprint: str
    last_full_fingerprint: str
    convergence_rerun_consumed: bool
    full_runs_after_apply: int = 0


def _count_blocked_checks(
    full: FullStaticCheckResult,
    *,
    destructive_policy: DestructivePolicy,
) -> int:
    """Baseline normalizer: infra/runtime non-PASS is not a terminal blocker."""

    _ = (full, destructive_policy)
    return 0


def decide_transition(
    full: FullStaticCheckResult,
    state: ConvergenceState,
    plan: DisablePlan,
    *,
    destructive_policy: DestructivePolicy,
    is_probe: bool,
) -> tuple[Transition, list[str]]:
    """Deterministic next step from current FULL + plan."""

    blocked = _count_blocked_checks(full, destructive_policy=destructive_policy)
    reasons: list[str] = []

    if is_probe and plan.probe_gate is not ProbeGate.AWAITING_GO:
        reasons.extend(plan.gate_reasons)
        if plan.probe_gate is ProbeGate.BLOCKED_INCOMPLETE_HANDOFF:
            return Transition.PAUSE, reasons
        return Transition.PAUSE, reasons

    if (
        full.status == "PASS"
        and plan.remaining_candidates == 0
        and blocked == 0
        and plan.probe_gate is ProbeGate.AWAITING_GO
    ):
        return Transition.PASS, []

    if plan.remaining_candidates == 0 and full.status != "PASS":
        reasons.append("FULL non-PASS with zero disable candidates")
        return Transition.PAUSE, reasons

    sig = plan_signature(plan)
    if state.last_signature is not None and sig == state.last_signature:
        reasons.append("same disable candidate set without progress")
        return Transition.PAUSE, reasons

    return Transition.APPLY_DELTA, []


def run_convergence_runner(
    root: Any,
    *,
    initial_handoff: dict[str, Any],
    run_full_after_apply: Callable[[], dict[str, Any]],
    capabilities: dict[str, Any],
    safety_max_iterations: int = DEFAULT_SAFETY_MAX_ITERATIONS,
) -> ConvergenceVerdict:
    """GO loop: decide → apply → invoke canonical FULL handoff → repeat."""

    destructive_policy = resolve_destructive_policy(capabilities)
    full = FullStaticCheckResult.from_static_check_handoff(initial_handoff)
    first_fp = full.fingerprint
    state = ConvergenceState(last_fingerprint=full.fingerprint)
    full_runs = 0

    for _ in range(safety_max_iterations):
        plan = extract_disable_plan(
            full, capabilities=capabilities, repo_root=Path(root)
        )
        is_probe = state.applies == 0
        transition, treasons = decide_transition(
            full,
            state,
            plan,
            destructive_policy=destructive_policy,
            is_probe=is_probe,
        )

        if transition is Transition.PASS:
            return ConvergenceVerdict(
                status=ConvergenceStatus.PASS,
                verdict="PREP_GREEN_SCOPE_READY",
                iterations=state.applies,
                last_full_status="PASS",
                remaining_disable_candidates=0,
                blocked_required_checks=0,
                reasons=[],
                first_full_fingerprint=first_fp,
                last_full_fingerprint=full.fingerprint,
                convergence_rerun_consumed=full_runs > 0,
                full_runs_after_apply=full_runs,
            )

        if transition is Transition.PAUSE:
            blocked = _count_blocked_checks(full, destructive_policy=destructive_policy)
            verdict = (
                "PREP_GREEN_SCOPE_PROBE_BLOCKED"
                if is_probe and plan.probe_gate is not ProbeGate.AWAITING_GO
                else "PREP_GREEN_SCOPE_NON_CONVERGED"
            )
            return ConvergenceVerdict(
                status=ConvergenceStatus.PAUSE,
                verdict=verdict,
                iterations=state.applies,
                last_full_status=full.status,
                remaining_disable_candidates=plan.remaining_candidates,
                blocked_required_checks=blocked,
                reasons=treasons,
                first_full_fingerprint=first_fp,
                last_full_fingerprint=full.fingerprint,
                convergence_rerun_consumed=full_runs > 0,
                full_runs_after_apply=full_runs,
            )

        prep = prepare_delta(root, plan)
        if not prep.ok:
            return ConvergenceVerdict(
                status=ConvergenceStatus.PAUSE,
                verdict="PAUSE_UNSUPPORTED_DISABLE_SHAPE",
                iterations=state.applies,
                last_full_status=full.status,
                remaining_disable_candidates=plan.remaining_candidates,
                blocked_required_checks=_count_blocked_checks(
                    full, destructive_policy=destructive_policy
                ),
                reasons=[f"unsupported pytest: {prep.unsupported_pytest_nodes}"],
                first_full_fingerprint=first_fp,
                last_full_fingerprint=full.fingerprint,
                convergence_rerun_consumed=full_runs > 0,
                full_runs_after_apply=full_runs,
            )

        apply_result: ApplyDeltaResult = apply_disable_delta(root, plan)
        if apply_result.recovery_contract != "none" and (
            plan.pytest_nodes and not apply_result.applied.pytest_nodes
        ):
            return ConvergenceVerdict(
                status=ConvergenceStatus.PAUSE,
                verdict="PAUSE_UNSUPPORTED_DISABLE_SHAPE",
                iterations=state.applies,
                last_full_status=full.status,
                remaining_disable_candidates=plan.remaining_candidates,
                blocked_required_checks=_count_blocked_checks(
                    full, destructive_policy=destructive_policy
                ),
                reasons=apply_result.not_applied,
                first_full_fingerprint=first_fp,
                last_full_fingerprint=full.fingerprint,
                convergence_rerun_consumed=full_runs > 0,
                full_runs_after_apply=full_runs,
            )

        state.applies += 1
        state.last_signature = plan_signature(plan)
        prev_fp = full.fingerprint

        handoff = run_full_after_apply()
        full_runs += 1
        full = FullStaticCheckResult.from_static_check_handoff(handoff)
        if full.fingerprint == prev_fp:
            return ConvergenceVerdict(
                status=ConvergenceStatus.FAIL,
                verdict="PREP_GREEN_SCOPE_NO_RERUN_EVIDENCE",
                iterations=state.applies,
                last_full_status=full.status,
                remaining_disable_candidates=0,
                blocked_required_checks=_count_blocked_checks(
                    full, destructive_policy=destructive_policy
                ),
                reasons=["FULL fingerprint unchanged after apply"],
                first_full_fingerprint=first_fp,
                last_full_fingerprint=full.fingerprint,
                convergence_rerun_consumed=True,
                full_runs_after_apply=full_runs,
            )
        state.last_fingerprint = full.fingerprint

    return ConvergenceVerdict(
        status=ConvergenceStatus.PAUSE,
        verdict="PREP_GREEN_SCOPE_SAFETY_CAP",
        iterations=state.applies,
        last_full_status=full.status,
        remaining_disable_candidates=extract_disable_plan(
            full, capabilities=capabilities, repo_root=Path(root)
        ).remaining_candidates,
        blocked_required_checks=_count_blocked_checks(
            full, destructive_policy=destructive_policy
        ),
        reasons=[f"safety_max_iterations={safety_max_iterations} exhausted"],
        first_full_fingerprint=first_fp,
        last_full_fingerprint=full.fingerprint,
        convergence_rerun_consumed=full_runs > 0,
        full_runs_after_apply=full_runs,
    )


run_convergence_loop = run_convergence_runner
