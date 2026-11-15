"""Маршрутизация design lane: design → review, затем design (макс. 3) или слайсы."""

from __future__ import annotations

from langgraph.graph import END

from agents.implementation.hop_lane_status import merge_split_apply_hop_result
from workflows.implementation.design_pipeline.routing import (
    DESIGN_LANE_EXECUTE_NODE,
    DESIGN_LANE_ROUTE_NODE,
    route_after_design_lane_execute,
)
from workflows.implementation.plan_hop import (
    continue_after_design_review,
    continue_design_with_review,
)
from workflows.implementation.routing_contract import (
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_DESIGN,
    STAGE_APPLY_DESIGN_REVIEW,
)


def test_repairs_required_loops_to_plan_not_end() -> None:
    """После REPAIRS_REQUIRED агент держит RUNNING — снова plan (→ design hop)."""
    state = {
        "implementation_status": RUN_STATUS_RUNNING,
        "implementation_stage": STAGE_APPLY_DESIGN,
        "implementation_recovery_code": "COMPOSED_BEHAVIOR_GAP",
    }
    assert route_after_design_lane_execute(state) == DESIGN_LANE_ROUTE_NODE


def test_no_change_reuse_advances_to_review_not_plan() -> None:
    """Один NO_CHANGE reuse не возвращает lane в plan/design execute."""
    hop_patch = merge_split_apply_hop_result(
        {
            "apply_design_status": "NO_CHANGE",
            "apply_design_answer": {
                "status": "NO_CHANGE",
                "tasks_summary": "canon reused",
                "gap_closure_summary": "skip adapter call",
                "files_changed": [],
                "remaining_gaps": [],
            },
            "implementation_machine_gate_errors": None,
            "implementation_receipt_gate": "SKIP",
        },
        "NO_CHANGE",
    )
    patch = continue_design_with_review({**hop_patch, "implementation_last_hop": STAGE_APPLY_DESIGN})
    assert patch["implementation_stage"] == STAGE_APPLY_DESIGN_REVIEW
    assert route_after_design_lane_execute(patch) == DESIGN_LANE_EXECUTE_NODE
    assert route_after_design_lane_execute(patch) != DESIGN_LANE_ROUTE_NODE
    replan = {
        **patch,
        "implementation_stage": STAGE_APPLY_DESIGN,
        "implementation_last_hop": STAGE_APPLY_DESIGN,
    }
    assert route_after_design_lane_execute(replan) == DESIGN_LANE_EXECUTE_NODE


def test_after_design_routes_straight_to_review() -> None:
    """Законченный design hop не возвращается в plan: следующий узел — review."""
    patch = continue_design_with_review(
        {
            "apply_design_status": "APPLY_PARTIAL",
            "implementation_status": RUN_STATUS_PAUSED,
        },
    )
    assert patch["implementation_stage"] == STAGE_APPLY_DESIGN_REVIEW
    assert patch["implementation_status"] == RUN_STATUS_RUNNING
    assert patch["implementation_last_hop"] == STAGE_APPLY_DESIGN
    assert route_after_design_lane_execute(patch) == DESIGN_LANE_EXECUTE_NODE


def test_review_pass_opens_slices() -> None:
    """APPLY_DESIGN_REVIEW_PASS закрывает design lane и открывает слайсы."""
    patch = continue_after_design_review(
        {"apply_design_review_status": "APPLY_DESIGN_REVIEW_PASS"},
        design_attempt=1,
    )
    assert patch["apply_design_lane_complete"] is True
    assert patch["implementation_stage"] is None
    assert patch["implementation_status"] == "IN_PROGRESS"
    assert route_after_design_lane_execute(patch) == END


def test_review_repairs_return_design_until_third() -> None:
    """Не-pass review возвращает design. Третья попытка ставит PAUSED, не слайсы."""
    second = continue_after_design_review(
        {"apply_design_review_status": "REPAIRS_REQUIRED"},
        design_attempt=2,
    )
    assert second["implementation_stage"] == STAGE_APPLY_DESIGN
    assert second["implementation_design_attempt"] == 3
    assert route_after_design_lane_execute(second) == DESIGN_LANE_EXECUTE_NODE
    capped = continue_after_design_review(
        {"apply_design_review_status": "REPAIRS_REQUIRED", "implementation_machine_gate_errors": [{"code": "X"}]},
        design_attempt=3,
    )
    assert capped["apply_design_lane_complete"] is False
    assert capped["implementation_status"] == RUN_STATUS_PAUSED
    assert capped["implementation_recovery_code"] == "DESIGN_REVIEW_ATTEMPTS_EXHAUSTED"
    assert capped["implementation_machine_gate_errors"] == [{"code": "X"}]


def test_graph_registers_every_router_target() -> None:
    """Каждый узел, который вернёт роутер после hop, есть в карте переходов графа."""
    from workflows.implementation.design_pipeline.graph import (
        DesignLaneAgents,
        build_apply_design_lane_graph,
    )

    graph = build_apply_design_lane_graph(
        DesignLaneAgents(apply_design=object(), apply_design_review=object()),  # type: ignore[arg-type]
    )
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert (DESIGN_LANE_EXECUTE_NODE, DESIGN_LANE_EXECUTE_NODE) in edges
    assert (DESIGN_LANE_EXECUTE_NODE, DESIGN_LANE_ROUTE_NODE) in edges
    assert (DESIGN_LANE_EXECUTE_NODE, END) in edges


def test_paused_raises_instead_of_ending_lane() -> None:
    import pytest

    from workflows.implementation.routing import ImplementationLaneTerminalError

    state = {
        "implementation_status": RUN_STATUS_PAUSED,
        "implementation_recovery_code": "Owner Attention",
    }
    with pytest.raises(ImplementationLaneTerminalError, match="Owner Attention"):
        route_after_design_lane_execute(state)
