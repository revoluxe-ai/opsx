"""После repair batch recheck не переиспользует durable PASS cache."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from workflows.verify.gate_repair import runner


def _setup(root: Path) -> None:
    (root / "openspec/changes/demo").mkdir(parents=True)
    (root / "src/stroy").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    for rel in runner._QUALITY_VERIFIER_POLICY_FILES:
        policy = root / rel
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text("policy = 1\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_verify_force_fresh_skips_cache_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup(tmp_path)
    cached = {"quality_gate_verdict": "PASS", "quality_gates_execution_status": "REUSED_PASS"}
    hit = patch.object(runner, "_quality_gates_cache_hit", return_value=cached)
    assemble = patch.object(
        runner,
        "_assemble_quality_gates_patch",
        return_value={"quality_gate_verdict": "PASS", "quality_gates_execution_status": "DETERMINISTIC"},
    )
    monkeypatch.setattr(runner, "resolve_opsx_repository_root", lambda _: tmp_path)
    monkeypatch.setattr(
        runner,
        "build_quality_gate_commands",
        lambda _: {"pytest": ["pytest"], "pyright": ["pyright"], "mypy": ["mypy"]},
    )
    async def _fake_pytest_execute(*_a, **_k):
        return type("R", (), {"gate_check": None, "status": "PASS"})()

    monkeypatch.setattr(runner, "run_pytest_execute", _fake_pytest_execute)
    monkeypatch.setattr(runner, "result_to_gate_check", lambda r: r)
    monkeypatch.setattr(
        runner,
        "_run_pyright_mypy_gates",
        lambda *a, **k: (
            type("G", (), {"exit_code": 0, "duration_ms": 1, "stdout_tail": ""})(),
            type("G", (), {"exit_code": 0, "duration_ms": 1, "stdout_tail": ""})(),
            "",
        ),
    )
    with hit as hit_mock, assemble:
        out = await runner.run_deterministic_quality_gates_async(
            {
                "repository_path": str(tmp_path),
                "change_id": "demo",
                "run_uuid": "u1",
                "verify_force_fresh_quality_gates": True,
            },
        )
    hit_mock.assert_not_called()
    assert out.get("verify_force_fresh_quality_gates") is False
