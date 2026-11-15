"""Простой parent graph: список слайсов и цикл одного слайса."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from agents.state import GraphState
from workflows.implementation.graph import build_implementation_graph
from workflows.implementation.routing import (
    ImplementationLaneTerminalError,
    OUTCOME_ENTER_ONE_SLICE,
    OUTCOME_LANE_COMPLETE_FINALIZE,
    OUTCOME_NEXT_SLICE,
    OUTCOME_RETURN_TO_DESIGN,
    route_after_one_slice,
    route_after_slice_list,
)
from workflows.implementation.slice_list import slice_ids_from_design
from workflows.implementation.tests.test_implementation_graph import _stub_hop_agents


def test_slice_ids_from_design_order() -> None:
    design = {
        "slice_order": {"nodes": ["SLICE-002", "SLICE-001"]},
        "slices": [{"slice_id": "SLICE-999"}],
    }
    assert slice_ids_from_design(design) == ["SLICE-002", "SLICE-001"]


def test_route_two_slices_then_finalize() -> None:
    state_after_first: GraphState = {
        "implementation_status": "RUNNING",
        "implementation_slices": [{"slice_id": "A"}, {"slice_id": "B"}],
        "implementation_slice_index": 1,
        "implementation_slice_id": "B",
        "implementation_last_hop": "APPLY_GREEN",
    }
    assert route_after_one_slice(state_after_first) == OUTCOME_NEXT_SLICE
    done: GraphState = {
        "implementation_status": "APPLY_COMPLETE",
        "implementation_lane_complete": True,
        "implementation_slices": [{"slice_id": "A"}, {"slice_id": "B"}],
        "implementation_slice_index": 2,
    }
    assert route_after_one_slice(done) == OUTCOME_LANE_COMPLETE_FINALIZE


def test_green_design_redo_once_then_raises_on_second() -> None:
    first: GraphState = {
        "implementation_force_design_hop": True,
        "implementation_design_redo_count": 1,
        "implementation_status": "RUNNING",
    }
    assert route_after_one_slice(first) == OUTCOME_RETURN_TO_DESIGN
    second: GraphState = {
        "implementation_force_design_hop": True,
        "implementation_design_redo_count": 2,
        "implementation_status": "FAILED",
        "implementation_recovery_code": "DESIGN_REDO_LIMIT",
    }
    with pytest.raises(ImplementationLaneTerminalError, match="DESIGN_REDO_LIMIT"):
        route_after_one_slice(second)


def test_slice_list_routes_to_one_slice() -> None:
    state: GraphState = {
        "implementation_status": "RUNNING",
        "implementation_slices": [{"slice_id": "SLICE-001"}],
    }
    assert route_after_slice_list(state) == OUTCOME_ENTER_ONE_SLICE


@pytest.mark.asyncio
async def test_compiled_graph_has_slice_nodes() -> None:
    graph = build_implementation_graph(_stub_hop_agents(), checkpointer=None)
    nodes = set(graph.get_graph().nodes)
    assert "implementation_slice_list" in nodes
    assert "implementation_one_slice" in nodes
    assert "apply_design_lane" in nodes
    assert "scenario_test_authoring" in nodes
    assert "scenario_trace_materialize" not in nodes
