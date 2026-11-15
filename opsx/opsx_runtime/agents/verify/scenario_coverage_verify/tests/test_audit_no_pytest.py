"""Регрессия: hop scenario coverage audit не поднимает pytest subprocess."""
from __future__ import annotations

from pathlib import Path

_ROW_CYCLE = (
    Path(__file__).resolve().parents[1] / "row_cycle.py"
)
_AGENT = Path(__file__).resolve().parents[1] / "agent.py"
_ROW_SINGLE = Path(__file__).resolve().parents[1] / "row_single.py"

# Канонический mapped node для defer-tier сценариев (совпадает с repair defer-тестами).
DEFER_NODE = "tests/scenarios/visual/test_defer_contract.py::test_hermetic_seam"

_FORBIDDEN_INVOCATIONS = (
    "run_focused_checks",
    "run_pytest_execute",
    "run_pytest_execute_sync",
)


def test_scenario_coverage_verify_modules_do_not_invoke_pytest_execute() -> None:
    """Runtime walk/audit не вызывает focused pytest — только read-only LLM recheck."""
    for path in (_ROW_CYCLE, _AGENT, _ROW_SINGLE):
        text = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN_INVOCATIONS:
            assert token not in text, f"{path.name} must not reference {token}"


def test_defer_node_id_matches_workflow_contract() -> None:
    """Якорь node id для defer-tier регрессий (без subprocess в audit path)."""
    assert DEFER_NODE.startswith("tests/scenarios/visual/")
    assert "::test_" in DEFER_NODE
