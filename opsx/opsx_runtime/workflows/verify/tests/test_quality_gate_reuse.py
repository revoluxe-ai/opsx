"""Reuse of expensive deterministic gates only under complete environment evidence."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from workflows.verify.gate_repair import runner
from workflows.verify.gate_repair.schemas import GateCheckResult
from workflows.verify.pytest_execute.schemas import CollectProof, PytestExecuteResult


def _setup(root: Path) -> None:
    (root / "openspec/changes/demo").mkdir(parents=True)
    (root / "src/stroy").mkdir(parents=True)
    (root / "src/stroy/example.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/test_example.py").write_text("assert True\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    # Complete verifier policy exists in the hermetic runtime, independently
    # of the product tree being checked.
    for rel in runner._QUALITY_VERIFIER_POLICY_FILES:
        policy = root / rel
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text("policy = 1\\n", encoding="utf-8")


def _patch_runner(monkeypatch: pytest.MonkeyPatch, root: Path, calls: list) -> None:
    monkeypatch.setattr(runner, "resolve_opsx_repository_root", lambda _: root)
    monkeypatch.setattr(
        runner, "build_quality_gate_commands",
        lambda _: {
            "pytest": ["pytest", "-q", "tests"],
            "pyright": ["pyright", "src/stroy"],
            "mypy": ["mypy", "src/stroy"],
        },
    )
    def _execute(check_id, command, *, cwd, timeout_seconds):
        calls.append(check_id)
        return (
            GateCheckResult(
                check_id=check_id, command=command, exit_code=0,
                duration_ms=100,
                stdout_tail="Success: no issues found in 1 source file",
            ), "Success: no issues found in 1 source file",
        )
    monkeypatch.setattr(runner, "_run_gate_subprocess", _execute)
    class _Diagnostics:
        total_count = 0
        input_integrity_status = "COMPLETE"
        def as_handoff_dict(self):
            return {"total_count": 0, "input_integrity_status": "COMPLETE"}
    monkeypatch.setattr(runner, "extract_gate_diagnostics", lambda *a, **k: _Diagnostics())

    async def _fake_pytest_execute(*_a, **_k):
        calls.append("pytest")
        gate = GateCheckResult(
            check_id="pytest",
            command=["pytest", "-q", "tests"],
            exit_code=0,
            duration_ms=100,
            stdout_tail="1 passed",
        )
        return PytestExecuteResult(
            status="PASS",
            scope_key="FULL_SUITE",
            collect_proof=CollectProof(
                scope_key="FULL_SUITE",
                collect_ok=True,
                collect_exit_code=0,
                collect_tail="1 test collected",
            ),
            gate_check=gate,
            run_log="1 passed",
        )

    monkeypatch.setattr(runner, "run_pytest_execute", _fake_pytest_execute)


def _state(root: Path, run: str, env: str | None) -> dict:
    return {
        "repository_path": str(root),
        "change_id": "demo",
        "run_uuid": run,
        "quality_gate_environment_id": env,
    }


def test_same_environment_and_sources_reuses_all_three_gates(tmp_path, monkeypatch):
    _setup(tmp_path)
    calls: list[str] = []
    _patch_runner(monkeypatch, tmp_path, calls)
    first = runner.run_deterministic_quality_gates_sync(_state(tmp_path, "exec-1", "postgres-fixture-v1"))
    assert first["quality_gate_verdict"] == "PASS"
    assert first["quality_gate_receipt"]["saved"] is True
    assert calls == ["pytest", "pyright", "mypy"]

    second = runner.run_deterministic_quality_gates_sync(_state(tmp_path, "exec-2", "postgres-fixture-v1"))
    assert calls == ["pytest", "pyright", "mypy"]  # Второй прогон — без subprocess.
    assert second["quality_gate_verdict"] == "PASS"
    assert second["quality_gates_execution_status"] == "REUSED_PASS"
    assert second["quality_gate_receipt"]["origin_execution_id"] == "exec-1"
    assert second["quality_gate_receipt"]["current_execution_id"] == "exec-2"
    assert "verdict" not in second  # Must not overwrite impl_verify verdict.

    # A changed source or fixture/environment requires real checks.
    (tmp_path / "src/stroy/example.py").write_text("x = 2\n", encoding="utf-8")
    runner.run_deterministic_quality_gates_sync(_state(tmp_path, "exec-3", "postgres-fixture-v1"))
    assert len(calls) == 6
    runner.run_deterministic_quality_gates_sync(_state(tmp_path, "exec-4", "postgres-fixture-v2"))
    assert len(calls) == 9


def test_auto_local_runtime_enables_reuse_without_explicit_env(tmp_path, monkeypatch):
    """Без ``quality_gate_environment_id`` используется local-runtime attestation."""
    _setup(tmp_path)
    calls: list[str] = []
    _patch_runner(monkeypatch, tmp_path, calls)
    first = runner.run_deterministic_quality_gates_sync(_state(tmp_path, "exec-1", None))
    assert first["quality_gate_receipt"]["saved"] is True
    second = runner.run_deterministic_quality_gates_sync(_state(tmp_path, "exec-2", None))
    assert second["quality_gates_execution_status"] == "REUSED_PASS"
    assert len(calls) == 3


def test_missing_run_uuid_never_reuses(tmp_path, monkeypatch):
    _setup(tmp_path)
    calls: list[str] = []
    _patch_runner(monkeypatch, tmp_path, calls)
    state = {
        "repository_path": str(tmp_path),
        "change_id": "demo",
        "quality_gate_environment_id": "env",
    }
    first = runner.run_deterministic_quality_gates_sync(state)
    second = runner.run_deterministic_quality_gates_sync({**state, "run_uuid": "exec-2"})
    assert first["quality_gate_receipt"]["saved"] is False
    assert second["quality_gates_execution_status"] == "DETERMINISTIC"
    assert len(calls) >= 3


def test_partial_diagnostics_do_not_publish_reusable_pass(tmp_path, monkeypatch):
    _setup(tmp_path)
    calls: list[str] = []
    _patch_runner(monkeypatch, tmp_path, calls)
    class _Partial:
        total_count = 0
        input_integrity_status = "PARTIAL"
        def as_handoff_dict(self):
            return {"total_count": 0, "input_integrity_status": "PARTIAL"}
    monkeypatch.setattr(runner, "extract_gate_diagnostics", lambda *a, **k: _Partial())
    result = runner.run_deterministic_quality_gates_sync(_state(tmp_path, "exec-1", "env"))
    assert result["quality_gate_receipt"]["saved"] is False
    assert len(calls) == 3


def test_opsx_only_changes_reuse_quality_pass_but_policy_change_invalidates(tmp_path, monkeypatch):
    _setup(tmp_path)
    calls = []
    _patch_runner(monkeypatch, tmp_path, calls)
    state = _state(tmp_path, "exec-1", "test-environment-attested")
    first = runner.run_deterministic_quality_gates_sync(state)
    assert first["quality_gate_receipt"]["saved"] is True
    assert len(calls) == 3

    # OPSX orchestration and OPSX tests cannot invalidate product quality.
    for rel in (
        "opsx/opsx_runtime/workflows/checkpoint_resume.py",
        "tests/opsx/test_harness.py",
    ):
        change = tmp_path / rel
        change.parent.mkdir(parents=True, exist_ok=True)
        change.write_text("runtime change\\n", encoding="utf-8")
    reused = runner.run_deterministic_quality_gates_sync(
        _state(tmp_path, "exec-2", "test-environment-attested")
    )
    assert reused["quality_gates_execution_status"] == "REUSED_PASS"
    assert len(calls) == 3

    policy = tmp_path / runner._QUALITY_VERIFIER_POLICY_FILES[1]
    policy.write_text("updated result classification\\n", encoding="utf-8")
    fresh = runner.run_deterministic_quality_gates_sync(
        _state(tmp_path, "exec-3", "test-environment-attested")
    )
    assert fresh["quality_gates_execution_status"] == "DETERMINISTIC"
    assert len(calls) == 6


def test_quality_pytest_command_excludes_opsx_harness(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(runner, "_resolve_poetry", lambda *_: ["poetry"])
    command = runner.build_quality_gate_commands(tmp_path)["pytest"]
    assert "--ignore=" + str(tmp_path / "tests/opsx") in command
    assert "not requires_postgres" in command
