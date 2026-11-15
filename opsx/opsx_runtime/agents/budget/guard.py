"""Pre-hop / task budget enforcement (estimate; unknown ≠ auto-block)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .ledger import TaskBudgetLedger, estimate_prompt_tokens
from .profiles import AgentBudgetProfile


class BudgetExceeded(RuntimeError):
    """RESOURCE_LIMIT: следующий hop/run запрещён."""

    def __init__(self, message: str, *, reason: str = "RESOURCE_LIMIT") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(slots=True)
class BudgetGuard:
    """Проверки бюджета для одного hop в контексте TaskBudgetLedger."""

    profile: AgentBudgetProfile
    ledger: TaskBudgetLedger
    hop_model_turns: int = 0
    hop_tool_calls: int = 0

    def assert_can_start_hop(self, *, prompt: str, hop: str) -> int:
        """Pre-hop: reserve + caps. Возвращает estimate для hop."""
        if self.ledger.deadline_unix and int(time.time()) > self.ledger.deadline_unix:
            raise BudgetExceeded(
                "task deadline exceeded",
                reason="RESOURCE_LIMIT",
            )
        estimate = int(
            estimate_prompt_tokens(prompt) * self.profile.estimate_reserve_ratio,
        )
        projected_task = (
            self.ledger.accumulated_observed_input
            + self.ledger.accumulated_estimated_input
            + estimate
        )
        if projected_task > self.ledger.max_observed_input_per_task:
            raise BudgetExceeded(
                f"task input budget exceeded: {projected_task}>{self.ledger.max_observed_input_per_task}",
            )
        if estimate > self.profile.max_observed_input_per_hop:
            raise BudgetExceeded(
                f"hop input estimate exceeds cap: {estimate}>{self.profile.max_observed_input_per_hop}",
            )
        if self.hop_model_turns >= self.profile.max_model_turns_per_hop:
            raise BudgetExceeded("max model turns per hop exceeded")
        self.hop_model_turns += 1
        _ = hop
        return estimate

    def assert_tool_allowed(self) -> None:
        """Intra-run: cap tool invocations per hop."""
        self.hop_tool_calls += 1
        self.ledger.tool_calls += 1
        if self.hop_tool_calls > self.profile.max_tool_invocations_per_hop:
            raise BudgetExceeded("max tool invocations per hop exceeded")

    def record_intra_run_usage(self, usage: dict[str, int | None]) -> None:
        """Intra-run: accumulate reported usage from stream events."""
        parts = [
            usage.get("input_tokens"),
            usage.get("cache_read_tokens"),
            usage.get("cache_write_tokens"),
        ]
        if all(v is not None for v in parts):
            total = sum(int(v) for v in parts)  # type: ignore[arg-type]
            self.ledger.accumulated_observed_input += total
        out = usage.get("output_tokens")
        if out is not None:
            self.ledger.accumulated_observed_output += int(out)
        if (
            self.ledger.accumulated_observed_input
            > self.ledger.max_observed_input_per_task
        ):
            raise BudgetExceeded("intra-run task input cap exceeded")
