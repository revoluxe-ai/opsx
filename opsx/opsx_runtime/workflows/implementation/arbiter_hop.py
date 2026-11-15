"""Parent-level узел ``implementation_arbiter`` и маршрутизация после directive."""

from __future__ import annotations

from functools import partial
from typing import Any

from agents.adapters.repair_adapter import implementation_arbiter_model_selection
from agents.implementation.implementation_arbiter.agent import ImplementationArbiterAgent
from agents.state import GraphState
from workflows.implementation.routing_contract import (
    STAGE_APPLY_DESIGN,
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)

OUTCOME_TARGET_DESIGN = "target_design"
OUTCOME_TARGET_SLICE = "target_slice"

_DESIGN_TARGETS = frozenset({STAGE_APPLY_DESIGN})
_SLICE_TARGETS = frozenset(
    {
        STAGE_APPLY_SCAFFOLD,
        STAGE_APPLY_RED,
        STAGE_APPLY_IMPLEMENT,
        STAGE_APPLY_GREEN,
    },
)


async def implementation_arbiter_node(
    state: GraphState,
    *,
    agent: ImplementationArbiterAgent,
) -> dict[str, Any]:
    """Один вызов arbiter SDK; увеличить ``implementation_arbiter_calls``."""
    calls = int(state.get("implementation_arbiter_calls") or 0) + 1
    hop_state: GraphState = {
        **state,
        "model": implementation_arbiter_model_selection(),
    }
    result = await agent.run(hop_state)
    return {**result, "implementation_arbiter_calls": calls}


def route_after_arbiter(state: GraphState) -> str:
    """После arbiter — design lane или повтор slice lane по ``target_stage``."""
    from workflows.implementation.routing import (
        ImplementationLaneTerminalError,
        _raise_if_failed_else_end_on_paused,
    )

    terminal = _raise_if_failed_else_end_on_paused(state)
    if terminal is not None:
        return terminal
    target = str(state.get("implementation_arbiter_target_stage") or "").strip().upper()
    if target in _DESIGN_TARGETS:
        return OUTCOME_TARGET_DESIGN
    if target in _SLICE_TARGETS:
        slices = state.get("implementation_slices")
        slice_id = str(state.get("implementation_slice_id") or "").strip()
        if not isinstance(slices, list) or not slices or not slice_id:
            raise ImplementationLaneTerminalError(
                "ARBITER_SLICE_CONTEXT_MISSING: arbiter targeted a slice stage "
                f"{target!r} but implementation_slices/slice_id are unset",
            )
        return OUTCOME_TARGET_SLICE
    raise ImplementationLaneTerminalError(
        f"implementation arbiter returned invalid target_stage={target!r}",
    )


def arbiter_node_factory(agent: ImplementationArbiterAgent):
    """Partial для ``add_node`` в parent implementation graph."""
    return partial(implementation_arbiter_node, agent=agent)
