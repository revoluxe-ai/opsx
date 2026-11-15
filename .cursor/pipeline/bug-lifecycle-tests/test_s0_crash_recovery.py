"""S0 crash after intake checkpoint, before runtime_state."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_PIPELINE = REPO_ROOT / ".cursor/pipeline"
_ADOPTION_CONFTEST = REPO_ROOT / ".cursor/pipeline/engineering-adoption-tests/conftest.py"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))


def _load_adoption():
    spec = importlib.util.spec_from_file_location("engineering_adoption_conftest_s0", _ADOPTION_CONFTEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adoption = _load_adoption()


def test_s0_resume_reuses_checkpoint_no_duplicate_cases(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _adoption.write_minimal_projectization(root)
    _adoption.write_verified_adoption(root)
    from bug_lifecycle.hermetic_boundary import (
        authorize_hermetic_sandbox,
        clear_hermetic_authority,
        register_hermetic_workspace,
        unregister_hermetic_workspace,
    )
    from bug_lifecycle.materialize_bug_queue import MaterializeContext, mode_intake
    from bug_lifecycle.orch_mission_runtime import advance, load_state
    from bug_lifecycle.s0_intake_checkpoint import persist_intake_checkpoint

    authorize_hermetic_sandbox(tmp_path)
    register_hermetic_workspace(root)
    request = {
        "mission": "ORCH-s0-crash",
        "execution_id": "exec-s0",
        "head": "a" * 40,
        "change": "c",
        "native_mission": "YES",
        "findings": [{"location": "app/a.py:1", "finding": "F-crash"}],
    }
    findings = [{"location": "app/a.py:1", "finding": "F-crash"}]
    ctx = MaterializeContext(
        repo_root=root,
        mission_id=request["mission"],
        execution_id=request["execution_id"],
        head=request["head"],
        change_id=request["change"],
        production=False,
    )
    intake = mode_intake(ctx, finding_id="F-crash", location="app/a.py:1", raw_report="x")
    work_items = [
        {"finding_id": "F-crash", "case_id": intake["CASE_ID"], "location": "app/a.py:1"},
    ]
    persist_intake_checkpoint(
        root,
        mission_id=request["mission"],
        request=request,
        findings=findings,
        work_items=work_items,
    )
    assert load_state(root, request["mission"])["stage"] == "S0_INTAKE"
    step = advance(root, request)
    assert step.get("STATUS") == "STAGE_ADVANCED"
    state = load_state(root, request["mission"])
    assert len(state.get("work_items", [])) == 1
    assert state["work_items"][0]["case_id"] == intake["CASE_ID"]
    cases = list((root / "opsx/runs/bug-cases").glob("BUGCASE-*"))
    assert len(cases) == 1
    unregister_hermetic_workspace(root)
    clear_hermetic_authority()
