"""TaskBudgetLedger — durable JSON рядом с repair/verify output_dir."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .profiles import AgentBudgetProfile


class LedgerEntry(BaseModel):
    """Одна запись расхода (reported или estimated)."""

    model_config = ConfigDict(extra="forbid")

    phase: Literal["pre_hop", "intra_run", "post_hop"] = "post_hop"
    hop: str = ""
    source: Literal["reported", "estimated", "replaced"] = "estimated"
    input_tokens: int = 0
    output_tokens: int = 0


class TaskBudgetLedger(BaseModel):
    """Накопленный расход задачи; persist в output_dir."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = "default"
    profile_id: str = "standard_impl"
    max_observed_input_per_task: int = 25_000_000
    max_observed_output_per_task: int = 1_000_000
    accumulated_observed_input: int = 0
    accumulated_estimated_input: int = 0
    accumulated_observed_output: int = 0
    accumulated_estimated_output: int = 0
    model_turns: int = 0
    tool_calls: int = 0
    deadline_unix: int | None = None
    entries: list[LedgerEntry] = Field(default_factory=list)


def ledger_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / "task_budget_ledger.json"


def _ledger_deadline_unix(*, now: int, profile: AgentBudgetProfile) -> int:
    """Wall-clock deadline для task ledger (hop pre-check в ``BudgetGuard``)."""
    wall = profile.deadline_wall_clock_s
    seconds = int(wall) if wall is not None and wall > 0 else 2700
    return now + seconds


def _refresh_ledger_deadline_if_expired(
    ledger: TaskBudgetLedger,
    *,
    now: int,
    profile: AgentBudgetProfile,
) -> TaskBudgetLedger:
    """Продлить истёкший deadline при reuse durable ledger между прогонами verify/repair."""
    if ledger.deadline_unix is not None and ledger.deadline_unix > now:
        return ledger
    return ledger.model_copy(
        update={"deadline_unix": _ledger_deadline_unix(now=now, profile=profile)},
    )


def load_task_ledger(
    output_dir: str | Path,
    *,
    task_id: str,
    profile: AgentBudgetProfile,
    profile_id: str = "standard_impl",
) -> TaskBudgetLedger:
    path = ledger_path(output_dir)
    now = int(time.time())
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ledger = TaskBudgetLedger.model_validate(data)
            if ledger.task_id == task_id:
                return _refresh_ledger_deadline_if_expired(
                    ledger, now=now, profile=profile,
                )
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    deadline = _ledger_deadline_unix(now=now, profile=profile)
    return TaskBudgetLedger(
        task_id=task_id,
        profile_id=profile_id,
        max_observed_input_per_task=profile.max_observed_input_per_task,
        max_observed_output_per_task=profile.max_observed_output_per_task,
        deadline_unix=deadline,
    )


def save_task_ledger(output_dir: str | Path, ledger: TaskBudgetLedger) -> None:
    path = ledger_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ledger.model_dump_json(indent=2) + "\n", encoding="utf-8")


def estimate_prompt_tokens(prompt: str) -> int:
    """Консервативная оценка без tokenizer (≈4 chars/token)."""
    length = len(prompt.encode("utf-8"))
    return max(1, int(length / 3))


def reconcile_usage(
    ledger: TaskBudgetLedger,
    *,
    hop: str,
    usage: dict[str, Any] | None,
    estimate: int,
) -> TaskBudgetLedger:
    """Post-hop: replace estimate with reported when available."""
    observed_in: int | None = None
    observed_out: int | None = None
    if usage:
        parts_in = [
            usage.get("input_tokens"),
            usage.get("cache_read_tokens"),
            usage.get("cache_write_tokens"),
        ]
        if all(v is not None for v in parts_in):
            observed_in = sum(int(v) for v in parts_in)  # type: ignore[arg-type]
        out = usage.get("output_tokens")
        if out is not None:
            observed_out = int(out)

    if observed_in is not None:
        ledger.accumulated_observed_input += observed_in
        ledger.entries.append(
            LedgerEntry(
                hop=hop,
                source="reported",
                input_tokens=observed_in,
                output_tokens=observed_out or 0,
            ),
        )
    else:
        ledger.accumulated_estimated_input += estimate
        ledger.entries.append(
            LedgerEntry(hop=hop, source="estimated", input_tokens=estimate),
        )
    if observed_out is not None:
        ledger.accumulated_observed_output += observed_out
    ledger.model_turns += 1
    return ledger
