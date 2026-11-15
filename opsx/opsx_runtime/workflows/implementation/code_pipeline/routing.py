"""Conditional edges для subgraph кода: scaffold → red → implement → green.

Стадии design (``APPLY_DESIGN`` / ``APPLY_DESIGN_REVIEW``) выполняются только в
``design_pipeline``; code lane — остальная executable-часть split-APPLY.
"""

from __future__ import annotations

from langgraph.graph import END

from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)

# Code lane = mutating hops после design; совпадает с SDK-частью main loop (не design).
CODE_LANE_STAGES = frozenset(
    {
        STAGE_APPLY_SCAFFOLD,
        STAGE_APPLY_RED,
        STAGE_APPLY_IMPLEMENT,
        STAGE_APPLY_GREEN,
    },
)
