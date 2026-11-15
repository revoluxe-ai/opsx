"""MVP-1 gate classifier; MVP-0: детерминированный fast-path без LLM."""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.pytest_error_classifier.schemas import PytestErrorClassifierResult, PytestErrorLabel
from agents.verify.pytest_run_health import classify_pytest_observation_fast


class PytestErrorClassifierAgent:
    """Дополняет findings осью BUILD/TEST (после quality_gates)."""

    __test__ = False

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Fast-path only in MVP-0 wiring tests."""
        diag = state.get("quality_gate_diagnostics") or {}
        pytest_lines = diag.get("pytest") if isinstance(diag, dict) else []
        labels: list[PytestErrorLabel] = []
        if isinstance(pytest_lines, list):
            for idx, line in enumerate(pytest_lines, start=1):
                if not isinstance(line, str):
                    continue
                axis = classify_pytest_observation_fast(line)
                if axis is None:
                    continue
                labels.append(
                    PytestErrorLabel(
                        finding_id=f"F-{idx:04d}",
                        axis=axis,
                        confidence="HIGH",
                        rationale="pytest_run_health fast-path",
                    ),
                )
        result = PytestErrorClassifierResult(status="CLASSIFIED", labels=labels)
        return {"pytest_error_classifier_result": result.model_dump(mode="json")}
