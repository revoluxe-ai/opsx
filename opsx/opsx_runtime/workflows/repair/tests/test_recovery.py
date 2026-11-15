from __future__ import annotations

import asyncio
import json
from pathlib import Path

from workflows.repair.graph import build_repair_graph
from workflows.repair.primitive import _deterministic, _merge
from workflows.repair.schemas import Finding, RepairState

from .fakes import FakeAdapter, response


def _state(tmp_path, **updates):
    data = {
        "mission_id": "recover-test",
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "isolated_workspace": True,
        "output_dir": str(tmp_path / "run"),
        "owner_go": True,
    }
    data.update(updates)
    return RepairState(**data)


def test_legacy_worker_roles_and_missing_route_migrate(tmp_path):
    raw = {
        "mission_id": "dogfood-old",
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "isolated_workspace": True,
        "output_dir": str(tmp_path / "run"),
        "classification": {
            "status": "PASS",
            "kind": "IMPLEMENTATION_BUG",
            "reason": "legacy",
            "evidence": "a.py:1",
        },
        "runs": [
            {
                "hop": "001-classify-old",
                "role": "classify",
                "state": "finished",
                "budget_stop_reason": None,
            },
            {
                "hop": "002-red-old",
                "role": "red",
                "state": "FAILED",
                "error": "old tool name",
            },
        ],
    }
    state = RepairState.model_validate(raw)
    assert state.classification is not None
    assert state.classification.chosen_route == "ROUTE_A"
    assert [run.role for run in state.runs] == [
        "CLASSIFIER",
        "RED_TEST_WORKER",
    ]


def test_recovered_graph_can_restart_at_red_without_classifier(tmp_path):
    adapter = FakeAdapter([
        response("RED_TEST_WORKER", {
            "status": "RED_CONFIRMED",
            "test_id": "tests/t.py::x",
            "test_path": "tests/t.py",
            "command": "pytest tests/t.py::x",
            "expected_failure": "bad",
            "actual_failure": "bad",
            "failure_match": True,
        }),
        response("IMPLEMENTATION_EXECUTOR", {
            "status": "FIXED",
            "summary": "fixed",
            "files_changed": ["src/a.py"],
            "execution_route": "LOCAL_REPAIR",
            "patch_hash": "ph",
        }),
        response("GREEN_VERIFIER", {
            "status": "GREEN_CONFIRMED",
            "test_id": "tests/t.py::x",
            "command": "pytest tests/t.py::x",
            "targeted_regressions": ["tests/t.py::x"],
        }),
        response("INVARIANT_CAPTURE_WORKER", {
            "invariant_id": "INV-1",
            "plain_language_invariant": "reply delivered",
            "technical_invariant": "consumed reply is delivered",
            "scope": "webhook",
            "proof_from_root_finding": "red green",
            "hunt_scope": ["src"],
        }),
    ])
    state = _state(
        tmp_path,
        active=Finding(id="F1", finding="bug", location="a.py:1"),
        classification={
            "status": "PASS",
            "kind": "IMPLEMENTATION_BUG",
            "reason": "already classified",
            "evidence": "a.py:1",
            "chosen_route": "ROUTE_A",
        },
    )
    graph = build_repair_graph(
        adapter,
        start_at="red",
        include_preflight=False,
    )
    result = RepairState.model_validate(
        asyncio.run(graph.ainvoke(state.model_dump(mode="json")))
    )
    assert result.status == "FIXED"
    assert len(adapter.calls) == 4
    assert "CLASSIFIER" not in adapter.calls[0]["prompt"]


def test_materializer_and_claim_are_individual_deterministic_primitives(tmp_path):
    state = _state(
        tmp_path,
        source_review={
            "status": "FINDINGS",
            "findings": [{
                "id": "F1",
                "severity": "high",
                "location": "a.py:1",
                "finding": "bug",
            }],
        },
    )
    materialized = _merge(state, _deterministic("MATERIALIZER", state))
    assert [item.id for item in materialized.queue] == ["F1"]
    claimed = _merge(
        materialized,
        _deterministic("CLAIM_WORKER", materialized),
    )
    assert claimed.active is not None
    assert claimed.active.id == "F1"
    assert claimed.queue == []
