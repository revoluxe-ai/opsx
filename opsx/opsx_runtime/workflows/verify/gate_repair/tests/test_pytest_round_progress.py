"""Регрессия: застой pytest node id между раундами ``quality_gates``."""

from __future__ import annotations

import pytest

from workflows.stop_errors import VerifyStageStop
from workflows.verify.gate_repair.pytest_round_progress import (
    STOP_CODE,
    apply_pytest_round_progress,
    enforce_gate_repair_pytest_progress,
    failing_pytest_node_ids_from_diagnostics,
)


_NODE_A = "tests/unit/a.py::test_a"
_NODE_B = "tests/unit/b.py::test_b"


def _diag(*nodes: str) -> dict[str, object]:
    return {
        "pytest": [f"pytest: {_NODE_A if n == 'a' else _NODE_B} - AssertionError: x" for n in nodes],
        "pyright": [],
        "mypy": [],
        "total_count": len(nodes),
    }


def test_failing_pytest_node_ids_from_diagnostics() -> None:
    ids = failing_pytest_node_ids_from_diagnostics(_diag("a", "b"))
    assert ids == frozenset({_NODE_A, _NODE_B})


def test_same_set_twice_raises() -> None:
    """Второй раунд: prior уже записан, current совпадает — стоп."""
    nodes = frozenset({_NODE_A, _NODE_B})
    enforce_gate_repair_pytest_progress(prior=frozenset(), current=nodes)
    with pytest.raises(VerifyStageStop) as err:
        enforce_gate_repair_pytest_progress(prior=nodes, current=nodes)
    assert err.value.code == STOP_CODE
    assert _NODE_A in err.value.evidence


def test_smaller_set_does_not_raise() -> None:
    prior = frozenset({_NODE_A, _NODE_B})
    current = frozenset({_NODE_A})
    enforce_gate_repair_pytest_progress(prior=prior, current=current)


def test_empty_set_does_not_raise() -> None:
    prior = frozenset({_NODE_A})
    enforce_gate_repair_pytest_progress(prior=prior, current=frozenset())
    enforce_gate_repair_pytest_progress(prior=frozenset(), current=frozenset())


def test_different_replacement_sets_new_baseline_via_apply() -> None:
    prior_nodes = frozenset({_NODE_A})
    state = {"verify_gate_repair_pytest_node_baseline": tuple(sorted(prior_nodes))}
    patch = apply_pytest_round_progress(state, _diag("b"))
    assert patch["verify_gate_repair_pytest_node_baseline"] == (_NODE_B,)


def test_first_non_empty_records_baseline() -> None:
    state: dict[str, object] = {}
    patch = apply_pytest_round_progress(state, _diag("a", "b"))
    assert patch["verify_gate_repair_pytest_node_baseline"] == (_NODE_A, _NODE_B)


def test_apply_raises_on_unchanged_second_round() -> None:
    baseline = (_NODE_A, _NODE_B)
    state = {"verify_gate_repair_pytest_node_baseline": baseline}
    with pytest.raises(VerifyStageStop) as err:
        apply_pytest_round_progress(state, _diag("a", "b"))
    assert err.value.code == STOP_CODE
