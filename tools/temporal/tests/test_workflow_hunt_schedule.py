"""Project-bug-hunt workflow schedules hunt STC/HB from session_lifecycle_contract."""

from __future__ import annotations

from pathlib import Path


def test_project_bug_hunt_workflow_uses_hunt_contract_stc_hb() -> None:
    workflow_py = Path(__file__).resolve().parents[1] / "workflow.py"
    text = workflow_py.read_text(encoding="utf-8")
    hunt_block = text.split("class ProjectBugHuntWorkflow", 1)[1].split("class VerificationExecutionWorkflow", 1)[0]
    assert "hunt_start_to_close_timeout()" in hunt_block
    assert "hunt_heartbeat_timeout()" in hunt_block
    assert "timedelta(hours=3)" not in hunt_block
