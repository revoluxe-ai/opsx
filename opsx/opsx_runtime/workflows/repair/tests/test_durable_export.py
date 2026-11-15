"""Тесты durable_export — mission-local JSON не теряются."""

from __future__ import annotations

import json
from pathlib import Path

from workflows.repair.durable_export import persist_durable_exports
from workflows.repair.schemas import (
    FanInPayload,
    Finding,
    FindingOutcome,
    HuntPayload,
    InvariantPayload,
    RepairState,
)


def test_persist_durable_exports_writes_index_and_invariant(tmp_path: Path):
    run_dir = tmp_path / "mission"
    run_dir.mkdir()
    state = RepairState(
        mission_id="m-smoke",
        repository_path=str(tmp_path),
        workspace=str(tmp_path),
        output_dir=str(run_dir),
        status="CLEAR",
        current_stage="hunt",
        invariants=[
            InvariantPayload(
                invariant_id="INV-1",
                plain_language_invariant="rule",
                technical_invariant="tech",
                scope="scope",
                entry_conditions=["a"],
                required_behavior=["b"],
                forbidden_behavior=["c"],
                known_writers=["w"],
                known_readers=["r"],
                proof_from_root_finding="proof",
                hunt_scope=["src"],
            )
        ],
        hunts=[
            HuntPayload(
                hunt_result="CLEAR",
                scope_checked=["src"],
                notes="ok",
            )
        ],
        finished=[
            FindingOutcome(
                finding=Finding(id="F1", finding="bug", location="a:1"),
                terminal_state="FIXED",
                invariant_id="INV-1",
            )
        ],
        seen=["F1"],
    )
    index = persist_durable_exports(state)
    assert (run_dir / "durable" / "INDEX.json").is_file()
    assert index["mission_id"] == "m-smoke"
    assert (run_dir / "family" / "INVARIANT.json").is_file()
    assert (run_dir / "durable" / "learning" / "LEARNING_DECISION.json").is_file()
    finished = json.loads(
        (run_dir / "durable" / "findings" / "FINISHED.json").read_text(encoding="utf-8")
    )
    assert finished["seen"] == ["F1"]


def test_fanin_and_learning_mirrors_results(tmp_path: Path):
    run_dir = tmp_path / "mission2"
    run_dir.mkdir()
    state = RepairState(
        mission_id="m-fanin",
        repository_path=str(tmp_path),
        workspace=str(tmp_path),
        output_dir=str(run_dir),
        status="FANIN_READY",
        current_stage="fanin",
        owner_final="CLOSE_EPISODE_NO_CLASS_LESSON",
        invariants=[
            InvariantPayload(
                invariant_id="INV-X",
                plain_language_invariant="rule",
                technical_invariant="tech",
                scope="scope",
                entry_conditions=["a"],
                required_behavior=["b"],
                forbidden_behavior=["c"],
                known_writers=["w"],
                known_readers=["r"],
                proof_from_root_finding="proof",
                hunt_scope=["src"],
            )
        ],
        fanin=FanInPayload(
            status="FANIN_READY",
            root_bug="bug",
            root_cause_pattern="pattern",
            error_class="class",
            regression_test_pattern="test",
        ),
    )
    persist_durable_exports(state)
    assert (run_dir / "results" / "FANIN.json").is_file()
    assert (run_dir / "results" / "LEARNING_DECISION.json").is_file()
    learning = json.loads(
        (run_dir / "durable" / "learning" / "LEARNING_DECISION.json").read_text(
            encoding="utf-8"
        )
    )
    assert learning["LEARNING_VERDICT"] == "NO_REUSABLE_LESSON"
    assert learning["SOURCE_FANIN_PATH"] is not None
