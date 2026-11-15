"""Verify entry: legacy checkpoint marks cannot bypass independent receipts.

impl → scenario → activation → deterministic gates/repair → project bug → bugbot
→ security. Every hop validates its own current evidence before deciding to skip.
"""

from __future__ import annotations

import logging
from typing import Any


from agents.state import GraphState
from workflows.verify.cycle_checks import CheckName, cycle_checks

logger = logging.getLogger(__name__)


async def verify_cycle_gate_node(state: GraphState) -> dict[str, Any]:
    """Only invalidate old checkpoint marks; every check validates its own receipt.

    A previous FULL_VERIFY_CYCLE green bit is not authority to bypass
    independently stale impl, scenario, quality or review evidence.
    """
    patch = cycle_checks(state).invalidate()
    if state.get("verify_cycle_skipped") is True:
        patch = {**patch, "verify_cycle_skipped": False}
    logger.info(
        "verify_cycle_gate continue=impl_verify independent_receipts=yes "
        "legacy_full_cycle_skip=disabled",
    )
    return patch


def route_after_verify_cycle_gate(state: GraphState) -> str:
    """Always enter checks: each check's own durable receipt decides reuse."""
    _ = state
    return "impl_verify"


async def verify_finalize_green_cycle_node(state: GraphState) -> dict[str, Any]:
    """Legacy full-cycle audit marker; never authorizes skipping independent receipts."""
    return cycle_checks(state).establish(CheckName.FULL_VERIFY_CYCLE)
