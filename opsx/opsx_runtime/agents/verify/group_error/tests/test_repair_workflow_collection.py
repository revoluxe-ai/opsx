"""WF_PYTEST_COLLECTION classification."""

from agents.verify.group_error.repair_workflow import classify_repair_workflow


def test_collection_workflow() -> None:
    assert classify_repair_workflow(
        source_tool="pytest",
        observation="pytest: errors during collection tests/foo.py",
    ) == "WF_PYTEST_COLLECTION"


def test_failure_workflow() -> None:
    assert classify_repair_workflow(
        source_tool="pytest",
        observation="pytest: FAILED tests/foo.py::test_x",
    ) == "WF_PYTEST_FAILURE"
