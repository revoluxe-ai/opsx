"""Тесты эвристики разбиения Repair Unit."""

from __future__ import annotations

from agents.verify.dispatch_unit.queue_items import build_repair_unit_queue_item
from agents.verify.dispatch_unit.split_heuristics import (
    primary_exception_token,
    try_heuristic_causal_groups,
)
from agents.verify.group_error.schemas import Finding


def test_primary_exception_token_picks_last_named_error():
    obs = (
        "FAILED tests/foo.py::t - AttributeError: '_Tx' object has no attribute 'execute'"
    )
    assert primary_exception_token(obs) == "AttributeError"


def test_heuristic_splits_distinct_pytest_exceptions():
    fids = ["F-0022", "E-0007"]
    findings = [
        Finding(
            finding_id="F-0022",
            source_tool="pytest",
            observation=(
                "FAILED tests/cohort.py::t - AttributeError: no attribute 'execute'"
            ),
            repair_workflow="WF_PYTEST_FAILURE",
        ),
        Finding(
            finding_id="E-0007",
            source_tool="pytest",
            observation=(
                "FAILED tests/other.py::t - InvalidRequestError: transaction already active"
            ),
            repair_workflow="WF_PYTEST_FAILURE",
        ),
    ]
    unit = build_repair_unit_queue_item(
        finding_ids=fids,
        findings=findings,
        anchor_unit_id="F-0022",
        root_cause_summary="hypothesis single cause",
        change_boundaries="tests",
    )
    groups = try_heuristic_causal_groups(unit)
    assert groups is not None
    assert len(groups) == 2
    covered = sorted(fid for g in groups for fid in g.finding_ids)
    assert covered == sorted(fids)
    assert groups[0].repair_route == "test"
    assert groups[1].repair_route == "test"


def test_heuristic_splits_mixed_static_and_pytest():
    fids = ["F-0001", "F-0002"]
    findings = [
        Finding(
            finding_id="F-0001",
            source_tool="pyright",
            observation="src/a.py:1:1 - error: typ",
            repair_workflow="WF_PYRIGHT_TYPE",
        ),
        Finding(
            finding_id="F-0002",
            source_tool="pytest",
            observation="FAILED tests/x.py::y - assert False",
            repair_workflow="WF_PYTEST_FAILURE",
        ),
    ]
    unit = build_repair_unit_queue_item(
        finding_ids=fids,
        findings=findings,
        anchor_unit_id="F-0001",
        root_cause_summary="mixed",
        change_boundaries="x",
    )
    groups = try_heuristic_causal_groups(unit)
    assert groups is not None
    routes = {g.finding_ids[0]: g.repair_route for g in groups}
    assert routes["F-0001"] == "static"
    assert routes["F-0002"] == "test"


def test_heuristic_returns_none_for_shared_signature():
    findings = [
        Finding(
            finding_id="F-0001",
            source_tool="pyright",
            observation="src/a.py:1:1 - error: a",
            repair_workflow="WF_PYRIGHT_TYPE",
        ),
        Finding(
            finding_id="F-0002",
            source_tool="pyright",
            observation="src/a.py:2:1 - error: b",
            repair_workflow="WF_PYRIGHT_TYPE",
        ),
    ]
    unit = build_repair_unit_queue_item(
        finding_ids=["F-0001", "F-0002"],
        findings=findings,
        anchor_unit_id="F-0001",
        root_cause_summary="shared",
        change_boundaries="src",
    )
    assert try_heuristic_causal_groups(unit) is None
