"""Production host harvest must not synthesize authoritative Bug Team evidence."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_PIPELINE = REPO_ROOT / ".cursor/pipeline"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from bug_lifecycle.hermetic_boundary import (
    authorize_hermetic_sandbox,
    clear_hermetic_authority,
    register_hermetic_workspace,
    unregister_hermetic_workspace,
)
from bug_lifecycle.host_harvest import harvest_after_dispatch, write_host_harvest_artifacts


def test_production_harvest_fail_closed_without_external_evidence(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    request = {
        "mission": "M-prod",
        "execution_id": "exec-prod",
        "head": "a" * 40,
    }
    harvest = harvest_after_dispatch(
        root,
        request,
        case_id="CASE-1",
        finding_id="F1",
        plan_id="PLAN",
        work_item_id="WI-1",
        production=True,
        simulate_host_write=False,
    )
    assert harvest["STATUS"] == "FAIL_CLOSED"
    assert harvest["REASON"] == "harvest_not_consumed"


def test_simulate_host_write_only_inside_hermetic_workspace(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    request = {
        "mission": "M-herm",
        "execution_id": "exec-herm",
        "head": "b" * 40,
    }
    blocked = harvest_after_dispatch(
        root,
        request,
        case_id="CASE-2",
        finding_id="F2",
        plan_id="PLAN",
        work_item_id="WI-2",
        production=False,
        simulate_host_write=True,
    )
    assert blocked["STATUS"] == "FAIL_CLOSED"
    assert blocked["REASON"] == "synthetic_host_write_requires_hermetic_workspace"

    authorize_hermetic_sandbox(tmp_path)
    register_hermetic_workspace(root)
    try:
        write_host_harvest_artifacts(
            root,
            mission_id="M-herm",
            execution_id="exec-herm",
            head="b" * 40,
            case_id="CASE-2",
            finding_id="F2",
            plan_id="PLAN",
            work_item_id="WI-2",
        )
        ok = harvest_after_dispatch(
            root,
            request,
            case_id="CASE-2",
            finding_id="F2",
            plan_id="PLAN",
            work_item_id="WI-2",
            production=False,
            simulate_host_write=False,
        )
        assert ok["STATUS"] == "READY_FOR_CLOSURE"
    finally:
        unregister_hermetic_workspace(root)
        clear_hermetic_authority()


def test_production_cannot_use_simulate_even_in_hermetic(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    authorize_hermetic_sandbox(tmp_path)
    register_hermetic_workspace(root)
    request = {
        "mission": "M-prod-herm",
        "execution_id": "exec-ph",
        "head": "c" * 40,
    }
    try:
        harvest = harvest_after_dispatch(
            root,
            request,
            case_id="CASE-3",
            finding_id="F3",
            plan_id="PLAN",
            work_item_id="WI-3",
            production=True,
            simulate_host_write=True,
        )
        assert harvest["STATUS"] == "FAIL_CLOSED"
        assert harvest["REASON"] == "synthetic_host_write_forbidden_in_production"
    finally:
        unregister_hermetic_workspace(root)
        clear_hermetic_authority()
