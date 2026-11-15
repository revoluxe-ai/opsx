"""Детектор застоя pytest node id между раундами ``quality_gates`` в gate-repair."""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.dispatch_unit.repro_cohort import pytest_node_id
from workflows.stop_errors import VerifyStageStop

STATE_BASELINE_KEY = "verify_gate_repair_pytest_node_baseline"
STOP_CODE = "GATE_REPAIR_NO_PROGRESS"


def _pytest_observations(diagnostics: dict[str, Any] | None) -> list[str]:
    if not isinstance(diagnostics, dict):
        return []
    raw = diagnostics.get("pytest")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item.strip()]


def failing_pytest_node_ids_from_diagnostics(
    diagnostics: dict[str, Any] | None,
) -> frozenset[str]:
    """Собрать нормализованные pytest node id из ``quality_gate_diagnostics.pytest``."""
    nodes: set[str] = set()
    for observation in _pytest_observations(diagnostics):
        node = pytest_node_id(observation)
        if node:
            nodes.add(node)
    return frozenset(nodes)


def prior_pytest_node_baseline(state: GraphState) -> frozenset[str]:
    """Baseline предыдущего раунда quality gates из GraphState."""
    raw = state.get(STATE_BASELINE_KEY)
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    return frozenset(str(item) for item in raw if item)


def baseline_state_patch(current: frozenset[str]) -> dict[str, Any]:
    """Patch для записи baseline после успешного прохода проверки застоя."""
    if not current:
        return {STATE_BASELINE_KEY: None}
    return {STATE_BASELINE_KEY: tuple(sorted(current))}


def enforce_gate_repair_pytest_progress(
    *,
    prior: frozenset[str],
    current: frozenset[str],
) -> None:
    """Остановить workflow, если два подряд раунда gates вернули тот же непустой набор node id.

    Args:
        prior: baseline с предыдущего раунда (пустой — первый учёт).
        current: node id текущего раунда.

    Raises:
        VerifyStageStop: ``GATE_REPAIR_NO_PROGRESS``, если ``current == prior`` и набор непустой.
    """
    if not current:
        return
    if not prior:
        return
    if current != prior:
        return
    msg = (
        "gate-repair quality_gates: pytest node set unchanged between rounds; "
        "another repair round would not reduce failures"
    )
    evidence = "\n".join(sorted(current))
    raise VerifyStageStop(
        msg,
        code=STOP_CODE,
        stage="gate_repair",
        evidence=evidence,
        owner="gate_repair",
        next_action=(
            "inspect last repair hop, diagnostics, and failing tests; "
            "do not rerun the same gate-repair loop"
        ),
    )


def apply_pytest_round_progress(
    state: GraphState,
    diagnostics: dict[str, Any] | None,
) -> dict[str, Any]:
    """Проверить застой и вернуть patch baseline для merge в state после ``quality_gates``."""
    prior = prior_pytest_node_baseline(state)
    current = failing_pytest_node_ids_from_diagnostics(diagnostics)
    enforce_gate_repair_pytest_progress(prior=prior, current=current)
    return baseline_state_patch(current)
