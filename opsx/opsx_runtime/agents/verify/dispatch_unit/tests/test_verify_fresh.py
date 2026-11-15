"""Unit tests: fresh gate verify (без pre-repair snapshot)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.state import GraphState
from agents.verify.dispatch_unit.fresh_focused_gates import FreshGateRun
from agents.verify.dispatch_unit.verify_group import verify_group_findings
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)


def _state_with_finding(tmp_path, *, observation: str, source_tool: str = "pyright") -> GraphState:
    finding = Finding(
        finding_id="F-0001",
        source_tool=source_tool,
        observation=observation,
        repair_workflow="WF_PYRIGHT_TYPE",
    )
    analysis = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1", identity_status="PARTIAL"),
        input_integrity=InputIntegrity(status="COMPLETE", issues=[]),
        ungrouped_finding_ids=["F-0001"],
        findings=[finding],
        triage_summary=TriageSummary(findings_total=1, ungrouped_total=1),
    )
    return {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "analysis_result": analysis.model_dump(mode="json"),
        "quality_gate_diagnostics": {
            "pyright": [observation],
            "pytest": [],
            "mypy": [],
        },
    }


def test_verify_unknown_without_opsx_root(tmp_path):
    """Без ``opsx`` fresh run не выполняется → UNKNOWN, не RESOLVED."""
    state = _state_with_finding(tmp_path, observation="src/a.py:10:1 - error: x")
    statuses = verify_group_findings(state, finding_ids=["F-0001"], files_changed=[])
    assert statuses[0].status == "UNKNOWN"


def test_verify_resolved_only_from_fresh_output(tmp_path):
    """RESOLVED только если observation отсутствует в **свежем** выводе."""
    (tmp_path / "opsx").mkdir(exist_ok=True)
    obs = "src/a.py:10:1 - error: missing type"
    state = _state_with_finding(tmp_path, observation=obs)
    fresh = {
        "pyright": FreshGateRun(tool="pyright", ok=True, issues=[], detail="exit=0"),
    }
    with patch(
        "agents.verify.dispatch_unit.verify_group.collect_fresh_runs",
        return_value=fresh,
    ):
        statuses = verify_group_findings(state, finding_ids=["F-0001"], files_changed=["src/a.py"])
    assert statuses[0].status == "RESOLVED"


def test_verify_unresolved_when_fresh_still_has_issue(tmp_path):
    (tmp_path / "opsx").mkdir(exist_ok=True)
    obs = "src/a.py:10:1 - error: still here"
    state = _state_with_finding(tmp_path, observation=obs)
    fresh = {
        "pyright": FreshGateRun(tool="pyright", ok=True, issues=[obs], detail="exit=1"),
    }
    with patch(
        "agents.verify.dispatch_unit.verify_group.collect_fresh_runs",
        return_value=fresh,
    ):
        statuses = verify_group_findings(state, finding_ids=["F-0001"], files_changed=[])
    assert statuses[0].status == "UNRESOLVED"


def test_stale_snapshot_does_not_mark_resolved(tmp_path):
    """Pre-repair snapshot с observation не даёт RESOLVED без fresh run."""
    obs = "src/a.py:10:1 - error: x"
    state = _state_with_finding(tmp_path, observation=obs)
    statuses = verify_group_findings(state, finding_ids=["F-0001"], files_changed=[])
    assert statuses[0].status == "UNKNOWN"
    assert statuses[0].status != "RESOLVED"
