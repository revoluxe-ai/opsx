"""Pyright-smoke: импорт и wiring workflow DI без запуска LangGraph."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.common.container import CommonAgentsContainer
from agents.verify.verify_hop import VerifyHopContainer
from workflows.implementation.implementation_hop import (
    ImplementationHopWorkflowContainer,
    assemble_implementation_hop_agents,
)
from workflows.main_graph_container import MainGraphWorkflowContainer
from workflows.verify.verify_workflow import VerifyWorkflowContainer


@pytest.mark.unit
def test_workflow_containers_individual_providers_override() -> None:
    """Отдельные Factory/Dependency провайдеры принимают override без bundle()."""
    review_adapter = MagicMock(name="review_adapter")
    apply_adapter = MagicMock(name="apply_adapter")

    common = CommonAgentsContainer()
    _ = common.adapter.override(apply_adapter)

    verify_hop = VerifyHopContainer()
    _ = verify_hop.adapter.override(review_adapter)
    _ = verify_hop.common.override(common)

    impl_lane = ImplementationHopWorkflowContainer()
    _ = impl_lane.adapter.override(apply_adapter)
    apply_design = impl_lane.apply_design_agent()
    hop_agents = assemble_implementation_hop_agents(
        apply_design=apply_design,
        apply_design_review=impl_lane.apply_design_review_agent(),
        apply_scaffold=impl_lane.apply_scaffold_agent(),
        apply_red=impl_lane.apply_red_agent(),
        apply_implement=impl_lane.apply_implement_agent(),
        apply_green=impl_lane.apply_green_agent(),
        implementation_arbiter=impl_lane.implementation_arbiter_agent(),
        adapter=apply_adapter,
    )
    assert hop_agents.apply_design is apply_design

    verify_wf = VerifyWorkflowContainer()
    _ = verify_wf.verify_hop.override(verify_hop)
    _ = verify_wf.repair_adapter.override(apply_adapter)
    for name in (
        "group_error_agent",
        "repair_order_agent",
        "diagnostic_agent",
        "static_triage_agent",
        "typing_repair_agent",
        "test_triage_agent",
        "test_repair_agent",
        "project_bug_hunt_agent",
        "bugbot_agent",
        "security_review_agent",
    ):
        _ = getattr(verify_wf, name).override(MagicMock(name=name))

    parent = MainGraphWorkflowContainer()
    _ = parent.verify_hop.override(verify_hop)
    _ = parent.implementation_hop_agents.override(hop_agents)
    _ = parent.repair_adapter.override(apply_adapter)
    for name in (
        "group_error_agent",
        "repair_order_agent",
        "diagnostic_agent",
        "static_triage_agent",
        "typing_repair_agent",
        "test_triage_agent",
        "test_repair_agent",
        "project_bug_hunt_agent",
        "bugbot_agent",
        "security_review_agent",
    ):
        _ = getattr(parent, name).override(MagicMock(name=name))

    _ = verify_wf.build_verify_graph
    _ = parent.build_main_graph
    assert verify_hop.impl_verify_agent() is not None
