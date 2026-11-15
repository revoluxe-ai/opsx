"""Детерминированный hop: очередь static issues из ``quality_gate_diagnostics``."""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.static_repair.report import format_static_repair_report
from agents.verify.static_repair.schemas import StaticRepairQueueItem


class StaticRepairAgent:
    """Сформировать repair queue по diagnostics (fix исполняется вне verify subgraph)."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Построить handoff или пропустить при ``quality_gate_verdict=PASS``."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")

        verdict = str(state.get("quality_gate_verdict") or "")
        diagnostics = state.get("quality_gate_diagnostics") or {}
        if not isinstance(diagnostics, dict):
            diagnostics = {}

        if verdict == "PASS":
            report = format_static_repair_report(
                change_id=change_id,
                status="SKIPPED_PASS",
                marker="✅ STATIC_REPAIR_SKIP",
                queue=[],
            )
            return {
                "static_repair_status": "SKIPPED_PASS",
                "static_repair_marker": "✅ STATIC_REPAIR_SKIP",
                "static_repair_report_text": report,
                "static_repair_queue": [],
                "static_repair_issue_count": 0,
            }

        queue: list[StaticRepairQueueItem] = []
        for tool in ("pytest", "pyright", "mypy"):
            issues = diagnostics.get(tool)
            if not isinstance(issues, list):
                continue
            for raw in issues:
                text = str(raw).strip()
                if text:
                    queue.append(StaticRepairQueueItem(tool=tool, issue=text))

        report = format_static_repair_report(
            change_id=change_id,
            status="REPAIR_QUEUE",
            marker="❌ STATIC_REPAIR_QUEUED",
            queue=queue,
        )
        return {
            "static_repair_status": "REPAIR_QUEUE",
            "static_repair_marker": "❌ STATIC_REPAIR_QUEUED",
            "static_repair_report_text": report,
            "static_repair_queue": [item.model_dump(mode="json") for item in queue],
            "static_repair_issue_count": len(queue),
        }
