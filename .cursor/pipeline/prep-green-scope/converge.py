"""Convergence loop: apply delta → invoke canonical FULL → decide (re-export)."""

from __future__ import annotations

from convergence_runner import (
    ConvergenceStatus,
    ConvergenceVerdict,
    Transition,
    decide_transition,
    run_convergence_runner,
)
from disable_plan import DisablePlan, ProbeGate

run_convergence = run_convergence_runner
run_convergence_loop = run_convergence_runner


def probe_allows_go(plan: DisablePlan) -> bool:
    return plan.probe_gate is ProbeGate.AWAITING_GO
