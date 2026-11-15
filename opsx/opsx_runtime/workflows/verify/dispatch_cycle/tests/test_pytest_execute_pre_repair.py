"""Тесты маршрута после scoped pytest_execute."""

from __future__ import annotations

import tempfile
from pathlib import Path

from workflows.verify.dispatch_cycle.pytest_execute_pre_repair import (
    _module_paths_for_item,
    pytest_execute_pre_repair_route,
)


def test_route_finalize_only_on_pause_skip():
    assert (
        pytest_execute_pre_repair_route({"verify_skip_test_repair": True})
        == "test_finding_finalize"
    )


def test_route_to_repair_on_pass_or_fail():
    assert pytest_execute_pre_repair_route({"verify_skip_test_repair": False}) == "test_repair"


def test_module_paths_empty_for_collection_harness_finding():
    """WF_PYTEST_COLLECTION без test node → FULL_SUITE, не ValueError."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "opsx").mkdir()
        item = {
            "finding_id": "F-0001",
            "repair_workflow": "WF_PYTEST_COLLECTION",
            "observation": (
                "pytest: bug_lifecycle.hermetic_boundary.HermeticBoundaryError: "
                "production repo cannot be registered as hermetic bug-lifecycle workspace"
            ),
        }
        state = {"repository_path": str(root)}
        assert _module_paths_for_item(state, item) == []
