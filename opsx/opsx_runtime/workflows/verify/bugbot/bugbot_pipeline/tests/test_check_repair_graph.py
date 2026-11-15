"""Тесты PP check-repair graph builder (reuse bug-team, без clone repair nodes)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.state import GraphState
from agents.verify.bugbot.agent import BugBotAgent
from workflows.repair.schemas import (
    Finding,
    FindingOutcome,
    FixEvidence,
    GreenEvidence,
    RepairState,
)
from workflows.stop_errors import OpsxStageStop
from workflows.verify.bugbot.bugbot_pipeline.check_repair_graph import (
    _maybe_raise_green_blocked_stage_stop,
    build_check_repair_graph,
    make_bug_team_repair_hop,
)


@pytest.mark.asyncio
async def test_make_bug_team_repair_hop_calls_build_bug_team_graph_not_nodes_repair():
    fake_team = AsyncMock()
    fake_team.ainvoke = AsyncMock(
        return_value={
            "mission_id": "m1",
            "repository_path": "/repo",
            "workspace": "/repo",
            "output_dir": "/repo/opsx/runs/verify/project-bug-repair/c1",
            "status": "COMPLETE_NO_FINDINGS",
            "finished": [],
            "error": None,
        },
    )
    adapter = MagicMock()
    with patch(
        "workflows.verify.bugbot.bugbot_pipeline.check_repair_graph.build_bug_team_graph",
        return_value=fake_team,
    ) as build_team:
        hop = make_bug_team_repair_hop(adapter)
        patch_result = await hop(
            {
                "repository_path": "/repo",
                "workspace": "/repo",
                "change_id": "c1",
                "review": {"status": "CLEAN", "findings": []},
            },
        )
    build_team.assert_called_once_with(adapter, include_preflight=False, checkpointer=None)
    fake_team.ainvoke.assert_awaited_once()
    assert patch_result["bug_check_repair_status"] == "COMPLETE_NO_FINDINGS"
    assert patch_result["bug_check_repair_summary"] == {
        "status": "COMPLETE_NO_FINDINGS",
        "error": None,
        "mission_id": "m1",
        "current_stage": "materialize",
        "finished_count": 0,
        "findings": [],
        "files_changed": [],
        "commit_status": "PENDING",
        "commit_sha": None,
        "commit_subject": None,
        "hunt_count": 0,
        "hunt_clear": False,
        "rejects_count": 0,
        "output_dir": "/repo/.opsx/runs/verify/project-bug-repair/c1",
    }


def test_build_check_repair_graph_structure_uses_discovery_and_repair_hop():
    discovery = BugBotAgent(MagicMock())
    adapter = MagicMock()
    with (
        patch(
            "workflows.verify.bugbot.bugbot_pipeline.check_repair_graph.make_bug_team_repair_hop",
        ) as mock_hop_factory,
        patch(
            "workflows.verify.bugbot.bugbot_pipeline.check_repair_graph.StateGraph",
        ) as mock_state_graph,
    ):
        mock_builder = MagicMock()
        mock_state_graph.return_value = mock_builder
        mock_builder.compile.return_value = MagicMock()
        mock_hop_factory.return_value = AsyncMock()
        build_check_repair_graph(discovery, adapter)
    mock_builder.add_node.assert_any_call("discovery", discovery.run)
    mock_builder.add_node.assert_any_call("bug_team_repair", mock_hop_factory.return_value)
    mock_builder.add_edge.assert_any_call("discovery", "bug_team_repair")


def test_maybe_raise_green_blocked_stage_stop_includes_notes() -> None:
    """§8.1: evidence содержит green.notes и next_action для Owner UI."""
    final = RepairState.model_validate(
        {
            "mission_id": "m1",
            "repository_path": ".",
            "workspace": ".",
            "output_dir": "/tmp/out",
            "error": "GREEN_STATUS_BLOCKED",
            "finished": [
                FindingOutcome(
                    finding=Finding(id="F1", finding="bug", location="a:1"),
                    fix=FixEvidence(
                        status="FIXED",
                        summary="patched",
                        files_changed=["src/a.py"],
                        execution_route="LOCAL_REPAIR",
                        patch_hash="p1",
                    ),
                    green=GreenEvidence(
                        status="BLOCKED",
                        test_id="tests/unit/t.py::x",
                        notes="run_pytest MCP unavailable",
                    ),
                    terminal_state="BLOCKED",
                ).model_dump(mode="json"),
            ],
        },
    )
    with pytest.raises(OpsxStageStop) as err:
        _maybe_raise_green_blocked_stage_stop(final)
    assert err.value.code == "GREEN_STATUS_BLOCKED"
    assert "run_pytest MCP unavailable" in err.value.evidence
    assert "run_named_bug_repair_ui.py" in err.value.next_action
