"""Привязка BudgetGuard к hop (repair output_dir или verify workspace)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .guard import BudgetExceeded, BudgetGuard
from .ledger import (
    TaskBudgetLedger,
    load_task_ledger,
    reconcile_usage,
    save_task_ledger,
)
from .profiles import AgentBudgetProfile, profile_id_for_role, resolve_profile_for_role


def output_dir_from_state(state: Mapping[str, Any]) -> Path | None:
    """Каталог durable ledger: repair ``output_dir`` или явный ``budget_output_dir``."""
    for key in ("output_dir", "budget_output_dir"):
        raw = state.get(key)
        if raw is not None and str(raw).strip():
            return Path(str(raw))
    return None


def task_id_from_state(state: Mapping[str, Any], *, hop: str) -> str:
    """Идентификатор задачи для ledger (repair mission или verify run)."""
    for key in ("repair_task_id", "mission_id", "change_id", "run_uuid"):
        raw = state.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return f"hop:{hop}"


def begin_hop_budget(
    state: Mapping[str, Any],
    *,
    role: str | None,
    hop: str,
    prompt: str,
) -> tuple[BudgetGuard | None, int, Path | None]:
    """Pre-hop: ledger + reserve estimate. Без output_dir — no-op."""
    out = output_dir_from_state(state)
    if out is None:
        return None, 0, None
    profile = resolve_profile_for_role(role)
    task_id = task_id_from_state(state, hop=hop)
    ledger = load_task_ledger(
        out,
        task_id=task_id,
        profile=profile,
        profile_id=profile_id_for_role(role),
    )
    guard = BudgetGuard(profile=profile, ledger=ledger)
    estimate = guard.assert_can_start_hop(prompt=prompt, hop=hop)
    save_task_ledger(out, guard.ledger)
    return guard, estimate, out


def finish_hop_budget(
    guard: BudgetGuard | None,
    output_dir: Path | None,
    *,
    hop: str,
    usage: dict[str, Any] | None,
    estimate: int,
) -> None:
    """Post-hop reconcile и persist."""
    if guard is None or output_dir is None:
        return
    reconcile_usage(guard.ledger, hop=hop, usage=usage, estimate=estimate)
    save_task_ledger(output_dir, guard.ledger)


def bind_intra_run_guard(
    adapter_state: dict[str, Any],
    guard: BudgetGuard | None,
) -> None:
    """Прокинуть guard в adapter state для stream intra-run reconcile."""
    if guard is not None:
        adapter_state["intra_run_budget_guard"] = guard


async def guarded_adapter_run(
    adapter: Any,
    state: Mapping[str, Any],
    *,
    role: str | None,
    hop: str,
    prompt: str,
    run_kwargs: dict[str, Any],
) -> Any:
    """Обёртка ``adapter.run`` с pre/post hop budget (impl/verify direct calls)."""
    guard, estimate, out = begin_hop_budget(state, role=role, hop=hop, prompt=prompt)
    merge_state = dict(state)
    bind_intra_run_guard(merge_state, guard)
    try:
        result = await adapter.run(merge_state, prompt=prompt, **run_kwargs)
    except BudgetExceeded:
        raise
    finish_hop_budget(
        guard,
        out,
        hop=hop,
        usage=getattr(result, "usage", None),
        estimate=estimate,
    )
    return result
