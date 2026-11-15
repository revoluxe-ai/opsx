"""Regression: workflow.py must import so worker can schedule hunt after bootstrap."""

from __future__ import annotations


def test_project_bug_hunt_workflow_importable() -> None:
    from workflow import ProjectBugHuntWorkflow

    assert ProjectBugHuntWorkflow.__name__ == "ProjectBugHuntWorkflow"
