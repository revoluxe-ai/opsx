"""Gate: модель получает готовый JSON проверки; runtime пишет receipt и POST-валидирует."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agents.adapters.abc_adapter import AgentRunContractError
from agents.state import GraphState
from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_SCAFFOLD,
    STAGE_APPLY_RED,
)
from workflows.implementation.routing import ImplementationLaneTerminalError
from workflows.implementation.slice_pipeline.stage_hop import run_slice_stage_hop
from workflows.implementation.slice_receipt_dispatch import (
    SliceReceiptValidationAnswer,
    parse_slice_receipt_validation_answer,
)


class _StubAgent:
    def __init__(self, patch: dict[str, Any]) -> None:
        self._patch = patch
        self.seen_validation: dict[str, Any] | None = None

    async def run(self, state: GraphState) -> dict[str, Any]:
        raw = state.get("implementation_slice_receipt_validation")
        if isinstance(raw, dict):
            self.seen_validation = parse_slice_receipt_validation_answer(raw).model_dump(
                mode="json",
            )
        write_rel = self._patch.get("_write_repo_file")
        if isinstance(write_rel, str):
            path = Path(str(state["repository_path"])) / write_rel
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text("def test_x(): pass", encoding="utf-8")
        answer = state.get("answer")
        assert isinstance(answer, dict)
        assert "slice_receipt_validation" in answer
        parse_slice_receipt_validation_answer(answer["slice_receipt_validation"])
        return {k: v for k, v in self._patch.items() if not str(k).startswith("_")}


def _ensure_opsx_root(repo: Path) -> None:
    _ = (repo / "opsx" / "project").mkdir(parents=True, exist_ok=True)


@pytest.mark.asyncio
async def test_skip_path_refreshes_and_posts_valid_json(tmp_path: Path) -> None:
    change = "gate-skip"
    slice_id = "SLICE-001"
    repo = tmp_path
    _ensure_opsx_root(repo)
    rel = "pkg/a.py"
    file_path = repo / rel
    file_path.parent.mkdir(parents=True)
    _ = file_path.write_text("ok", encoding="utf-8")
    scaffold_dir = repo / "openspec" / "changes" / change / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    receipt = {
        "status": "SCAFFOLD_READY",
        "extended_paths": [rel],
        "created_paths": [],
    }
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(json.dumps(receipt), encoding="utf-8")
    state: GraphState = {
        "change_id": change,
        "repository_path": str(repo),
        "implementation_slice_id": slice_id,
    }
    agent = _StubAgent({})
    out = await run_slice_stage_hop(
        state,
        agent=agent,
        stage=STAGE_APPLY_SCAFFOLD,
    )
    assert agent.seen_validation is None
    dispatch = parse_slice_receipt_validation_answer(
        out["implementation_slice_receipt_validation"],
    )
    assert dispatch.valid
    assert dispatch.gate in ("SKIP", "SKIP_MIGRATE")
    from workflows.implementation.slice_receipt import stage_scope_overlay_path

    overlay = stage_scope_overlay_path(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo,
    )
    assert overlay.is_file()
    assert "work_scope_digest" in json.loads(overlay.read_text(encoding="utf-8"))
    assert out["implementation_receipt_gate"] == "SKIP"


@pytest.mark.asyncio
async def test_sdk_path_passes_pre_json_and_post_validates(tmp_path: Path) -> None:
    change = "gate-sdk"
    slice_id = "SLICE-001"
    repo = tmp_path
    _ensure_opsx_root(repo)
    rel = "tests/t.py"
    test_file = repo / rel
    red_dir = repo / "openspec" / "changes" / change / "apply-red" / slice_id
    red_dir.mkdir(parents=True)
    receipt = {
        "status": "RED_PROVEN",
        "test_paths": [rel],
        "fixture_paths": [],
    }
    _ = (red_dir / "RED.json").write_text(json.dumps(receipt), encoding="utf-8")
    agent = _StubAgent(
        {
            "_write_repo_file": rel,
            "apply_red_status": "APPLY_COMPLETE",
            "apply_red_answer": {
                "status": "APPLY_COMPLETE",
                "tasks_summary": "wrote test",
                "gap_closure_summary": "done",
                "files_changed": [rel],
                "remaining_gaps": [],
            },
        },
    )
    state: GraphState = {
        "change_id": change,
        "repository_path": str(repo),
        "implementation_slice_id": slice_id,
    }
    out = await run_slice_stage_hop(state, agent=agent, stage=STAGE_APPLY_RED)
    assert agent.seen_validation is not None
    pre = parse_slice_receipt_validation_answer(agent.seen_validation)
    assert pre.code == "FILE_MISSING"
    assert pre.gate == "PRE_AGENT"
    post = parse_slice_receipt_validation_answer(out["implementation_slice_receipt_validation"])
    assert post.valid
    assert post.gate == "POST_AGENT"
    from workflows.implementation.slice_receipt import load_stage_receipt

    merged = load_stage_receipt(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert merged is not None
    assert merged.get("work_scope_digest")


@pytest.mark.asyncio
async def test_post_validate_fails_when_fingerprints_missing(tmp_path: Path) -> None:
    change = "gate-fail"
    slice_id = "SLICE-001"
    repo = tmp_path
    _ensure_opsx_root(repo)
    red_dir = repo / "openspec" / "changes" / change / "apply-red" / slice_id
    red_dir.mkdir(parents=True)
    _ = (red_dir / "RED.json").write_text(
        json.dumps({"status": "RED_PROVEN", "test_paths": [], "fixture_paths": []}),
        encoding="utf-8",
    )
    agent = _StubAgent({"apply_red_status": "NO_CHANGE"})
    state: GraphState = {
        "change_id": change,
        "repository_path": str(repo),
        "implementation_slice_id": slice_id,
    }
    with pytest.raises(ImplementationLaneTerminalError, match="RECEIPT_POST_VALIDATE"):
        await run_slice_stage_hop(state, agent=agent, stage=STAGE_APPLY_RED)


@pytest.mark.asyncio
async def test_green_proven_migrates_without_digest_or_fingerprints(tmp_path: Path) -> None:
    """Visual-like GREEN: status OK, files on disk, без work_scope_digest → SKIP."""
    change = "gate-green-migrate"
    slice_id = "SLICE-001"
    repo = tmp_path
    _ensure_opsx_root(repo)
    rel_red = "tests/red_case_test.py"
    rel_impl = "src/feature.py"
    (repo / rel_red).parent.mkdir(parents=True, exist_ok=True)
    _ = (repo / rel_red).write_text("def test_red(): pass", encoding="utf-8")
    (repo / rel_impl).parent.mkdir(parents=True, exist_ok=True)
    _ = (repo / rel_impl).write_text("x = 1\n", encoding="utf-8")
    base = repo / "openspec" / "changes" / change
    red_dir = base / "apply-red" / slice_id
    impl_dir = base / "apply-implement" / slice_id
    green_dir = base / "apply-green" / slice_id
    for d in (red_dir, impl_dir, green_dir):
        d.mkdir(parents=True)
    _ = (red_dir / "RED.json").write_text(
        json.dumps(
            {
                "status": "RED_PROVEN",
                "test_paths": [rel_red],
                "fixture_paths": [],
            },
        ),
        encoding="utf-8",
    )
    _ = (impl_dir / "IMPLEMENTATION.json").write_text(
        json.dumps(
            {
                "status": "IMPLEMENTATION_WRITTEN",
                "production_paths_modified": [rel_impl],
                "production_paths_created": [],
            },
        ),
        encoding="utf-8",
    )
    _ = (green_dir / "GREEN.json").write_text(
        json.dumps({"status": "GREEN_PROVEN"}),
        encoding="utf-8",
    )
    state: GraphState = {
        "change_id": change,
        "repository_path": str(repo),
        "implementation_slice_id": slice_id,
    }
    agent = _StubAgent({})
    out = await run_slice_stage_hop(
        state,
        agent=agent,
        stage=STAGE_APPLY_GREEN,
    )
    assert agent.seen_validation is None
    assert out["implementation_receipt_gate"] == "SKIP"
    from workflows.implementation.slice_receipt import load_stage_receipt

    green_payload = load_stage_receipt(change, slice_id, STAGE_APPLY_GREEN, repo_root=repo)
    assert green_payload is not None
    assert green_payload.get("work_scope_digest")


@pytest.mark.asyncio
async def test_tool_scope_violation_raises_contract_error(tmp_path: Path) -> None:
    change = "scope-violation"
    slice_id = "SLICE-005"
    repo = tmp_path
    _ensure_opsx_root(repo)
    state: GraphState = {
        "change_id": change,
        "repository_path": str(repo),
        "implementation_slice_id": slice_id,
    }

    class _ScopeViolationAgent:
        async def run(self, hop_state: GraphState) -> dict[str, Any]:
            raise AgentRunContractError(
                "TOOL_SCOPE_VIOLATION",
                status="cancelled",
                run_id="run-42d6f885",
                agent_id="agent-76a9e808",
                attempt=1,
                message="tool scope violation is an agent contract error",
            )

    with pytest.raises(AgentRunContractError, match="TOOL_SCOPE_VIOLATION"):
        _ = await run_slice_stage_hop(
            state,
            agent=_ScopeViolationAgent(),
            stage=STAGE_APPLY_SCAFFOLD,
        )


def test_slice_receipt_validation_answer_rejects_extra_keys() -> None:
    with pytest.raises(Exception):
        SliceReceiptValidationAnswer.model_validate(
            {
                "valid": False,
                "code": "X",
                "work_paths": [],
                "stage": STAGE_APPLY_RED,
                "slice_id": "S1",
                "gate": "PRE_AGENT",
                "extra": "nope",
            },
        )
