"""Markdown handoff для static repair queue."""

from __future__ import annotations

from agents.verify.static_repair.schemas import StaticRepairQueueItem


def format_static_repair_report(
    *,
    change_id: str,
    status: str,
    marker: str,
    queue: list[StaticRepairQueueItem],
) -> str:
    """Отчёт: статус + нумерованная очередь issues."""
    lines = [
        f"# Static repair — {change_id}",
        "",
        marker,
        "",
        f"Status: `{status}`",
        f"Issues queued: {len(queue)}",
        "",
    ]
    if not queue:
        lines.append("_No repair items (gates PASS)._")
        lines.append("")
        return "\n".join(lines)
    lines.append("## Repair queue (handoff)")
    for index, item in enumerate(queue, start=1):
        lines.append(f"{index}. **[{item.tool}]** `{item.issue}`")
    lines.append("")
    return "\n".join(lines)
