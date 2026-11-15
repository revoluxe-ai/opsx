"""Регрессия: ``FixBugAgent.on_success`` читает ``Finding.id``, как в verify logic_repair."""

from __future__ import annotations

from agents.repair.fix_bug.agent import FixBugAgent
from model_config.agent_roles import AgentRole
from workflows.repair.schemas import FixEvidence, WorkerReceipt
from workflows.verify.dispatch_cycle.repair_state import build_verify_repair_state


def test_on_success_uses_finding_id_not_wrong_attr() -> None:
    """Состояние как у ``build_verify_repair_state`` — без AttributeError на id finding."""
    item = {
        "finding_id": "F-visual-0001",
        "observation": "pytest node failed",
        "path": "tests/unit/foo.py",
        "line": 12,
        "source_tool": "pytest",
    }
    graph_state = {
        "change_id": "visual",
        "repository_path": r"C:\Users\vkita\my_code\opsx-implement",
        "workspace": r"C:\Users\vkita\my_code\opsx-implement",
    }
    repair_state = build_verify_repair_state(graph_state, item)
    agent = FixBugAgent(adapter=object())  # type: ignore[arg-type]
    payload = FixEvidence(
        status="FAILED",
        summary="edit did not fix pytest",
        files_changed=[],
    )
    receipt = WorkerReceipt(
        hop="fix",
        role=AgentRole.IMPLEMENTATION_EXECUTOR,
        state="finished",
    )
    repair_update = agent.on_success(repair_state, payload, receipt)
    assert repair_update.get("status") == "FAILED"
    assert repair_state.active is not None
    assert repair_state.active.id == "F-visual-0001"
