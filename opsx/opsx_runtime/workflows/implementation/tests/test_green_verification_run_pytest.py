"""GREEN replay frozen poetry pytest использует тот же runner, что run_pytest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.implementation.stage_receipt_writer import (
    _pytest_node_id_from_frozen_command,
    _run_green_verification_executions,
    write_green_receipt_from_hop,
)


def _ensure_opsx_root(repo: Path) -> None:
    _ = (repo / "opsx" / "project").mkdir(parents=True, exist_ok=True)


def test_single_poetry_pytest_node_is_extracted() -> None:
    command = (
        "poetry run pytest -q "
        "tests/scenarios/example/test_slice.py::test_scenario_red"
    )
    assert _pytest_node_id_from_frozen_command(command) == (
        "tests/scenarios/example/test_slice.py::test_scenario_red"
    )
    assert _pytest_node_id_from_frozen_command("node --test tests/unit/a.test.js") is None
    assert (
        _pytest_node_id_from_frozen_command(
            "poetry run pytest -q tests/a.py tests/b.py",
        )
        is None
    )


def test_green_replay_uses_run_pytest_interpreter(monkeypatch, tmp_path: Path) -> None:
    """Ненулевой exit run_pytest остаётся RED evidence и несёт хвост."""
    captured: list[str] = []

    def fake_run(repo_root: Path, node_id: str, *, timeout_seconds: float = 0) -> dict[str, Any]:
        del repo_root, timeout_seconds
        captured.append(node_id)
        return {
            "ok": False,
            "exit_code": 1,
            "tail": "AssertionError: authoritative_expected",
            "node_id": node_id,
        }

    monkeypatch.setattr(
        "agents.agent_tools.deterministic.pytest_tool.run_repair_hop_pytest_sync",
        fake_run,
    )
    _ensure_opsx_root(tmp_path)
    command = "poetry run pytest -q tests/scenarios/example/test_slice.py::test_scenario_red"
    executions = _run_green_verification_executions(tmp_path, [command])
    assert captured == ["tests/scenarios/example/test_slice.py::test_scenario_red"]
    assert executions[0]["exit_code"] == 1
    assert executions[0]["execution_mechanism"] == "python_pytest"
    assert "AssertionError" in executions[0]["relevant_output"]


def test_write_green_refusal_keeps_replay_tail(monkeypatch, tmp_path: Path) -> None:
    _ensure_opsx_root(tmp_path)
    command = "poetry run pytest -q tests/scenarios/example/test_slice.py::test_scenario_red"
    monkeypatch.setattr(
        "workflows.implementation.stage_receipt_writer.load_stage_receipt",
        lambda *_args, **_kwargs: {
            "status": "RED_PROVEN",
            "chat_history_required": False,
            "test_commands": [command],
            "test_content_identity": "abc",
            "command_executions": [{"final_exit_code": 1, "attempts": []}],
        },
    )
    monkeypatch.setattr(
        "workflows.implementation.stage_receipt_writer.classify_green_predecessor_pairing",
        lambda **_kwargs: {"ok": True, "branch": "NORMAL"},
    )
    monkeypatch.setattr(
        "workflows.implementation.stage_receipt_writer._run_green_verification_executions",
        lambda *_args, **_kwargs: [
            {
                "phase": "green_verify",
                "iteration": 0,
                "command": command,
                "exit_code": 1,
                "relevant_output": "FAILED test_scenario_red",
            },
        ],
    )
    result = write_green_receipt_from_hop(
        "any-change",
        "slice-a",
        repo_root=tmp_path,
        answer={
            "status": "APPLY_COMPLETE",
            "tasks_summary": "claimed pass",
            "gap_closure_summary": "claimed pass",
            "files_changed": [],
        },
    )
    assert result.ok is False
    assert result.code == "RED_EVIDENCE_NOT_PASS"
    assert result.detail is not None
    assert "FAILED test_scenario_red" in result.detail
