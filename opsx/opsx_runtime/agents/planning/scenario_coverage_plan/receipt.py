"""Receipt preserve для scenario-coverage plan: skip SDK при неизменных входах."""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from agents.common.planning_artifacts import inspect_plan_artifacts
from agents.common.scenario_files import (
    expected_plan_content_hash,
    plan_has_embedded_rows,
    plan_receipt_machine_path,
)
from check_workflow_receipt import CheckWorkflowReceipt


class PreserveAction(str, Enum):
    """Действие planning-узла до вызова SDK."""

    PRESERVED = "PRESERVED"
    NEEDS_AGENT = "NEEDS_AGENT"
    PAUSE = "PAUSE"


@dataclass(frozen=True)
class PreserveDecision:
    """Итог детерминированной проверки plan+receipt."""

    action: PreserveAction
    code: str = ""
    detail: str = ""
    producer: str = ""


class ScenarioCoveragePlanReceipt:
    """Решает, сохранить готовый план или вызвать ``ScenarioCoveragePlanAgent``."""

    def __init__(self, repo_root: Path, change_id: str) -> None:
        self._repo_root = repo_root.resolve()
        self._change_id = change_id
        self._change = self._repo_root / "openspec" / "changes" / change_id
        self._coverage = self._change / "scenario-coverage"

    def preserve_decision(self) -> PreserveDecision:
        """Сравнить receipt INPUTS с диском; вернуть skip, SDK или PAUSE."""
        planning = inspect_plan_artifacts(
            self._repo_root,
            self._change_id,
            phase="planning",
        )
        if planning.get("status") == "READY":
            return PreserveDecision(
                action=PreserveAction.PRESERVED,
                code=str(planning.get("code") or "PLANNING_INPUTS_READY"),
                detail=str(planning.get("detail") or ""),
            )
        code = str(planning.get("code") or "")
        if code in (
            "PLANNING_INPUTS_MISSING",
            "PLAN_INPUT_MISSING",
            "SPEC_INPUT_MISSING",
        ):
            return PreserveDecision(
                action=PreserveAction.PAUSE,
                code=code,
                detail=str(planning.get("detail") or ""),
                producer=str(planning.get("producer") or "/opsx-continue"),
            )
        if code in ("COVERAGE_PLAN_MISSING", "COVERAGE_PLAN_INVALID", "COVERAGE_RECEIPT_INVALID"):
            return PreserveDecision(
                action=PreserveAction.NEEDS_AGENT,
                code=code,
                detail=str(planning.get("detail") or ""),
                producer=str(planning.get("producer") or "/opsx-spec-scenario-coverage mode=plan"),
            )
        if self._can_preserve_files():
            return PreserveDecision(
                action=PreserveAction.PRESERVED,
                code="COVERAGE_PLAN_PRESERVED",
                detail="plan+receipt current",
            )
        return PreserveDecision(
            action=PreserveAction.NEEDS_AGENT,
            code=code or "COVERAGE_PLAN_STALE",
            detail=str(planning.get("detail") or ""),
            producer=str(planning.get("producer") or "/opsx-spec-scenario-coverage mode=plan"),
        )

    def _can_preserve_files(self) -> bool:
        """Сверить ``plan.md`` и machine receipt на диске без вызова SDK."""
        plan = self._coverage / "plan.md"
        receipt_path = plan_receipt_machine_path(self._coverage)
        if not plan.is_file() or not receipt_path.is_file():
            return False
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(receipt, dict) or receipt.get("SCENARIO_COVERAGE_RECEIPT") is not True:
            return False
        if receipt.get("PLAN_STATUS") not in ("ready", "provisional"):
            return False
        text = plan.read_text(encoding="utf-8", errors="replace")
        if plan_has_embedded_rows(text):
            return False
        if receipt.get("PLAN_CONTENT_HASH") != expected_plan_content_hash(self._coverage):
            return False
        inputs = receipt.get("INPUTS")
        if not isinstance(inputs, dict):
            return False
        if not self._inputs_current(inputs):
            return False
        return True

    def _inputs_current(self, inputs: dict[str, Any]) -> bool:
        for value in inputs.values():
            sources: list[Mapping[str, Any]] = []
            if isinstance(value, list):
                sources.extend(x for x in value if isinstance(x, Mapping))
            elif isinstance(value, Mapping) and (value.get("path") or value.get("source")):
                sources.append(value)
            for entry in sources:
                rel = entry.get("path") or entry.get("source")
                digest = entry.get("content_hash")
                if not isinstance(rel, str) or not isinstance(digest, str):
                    return False
                file_path = (self._change / rel).resolve()
                if not file_path.is_relative_to(self._change.resolve()) or not file_path.is_file():
                    return False
                if not CheckWorkflowReceipt.matches_file(file_path, digest):
                    return False
        return True
