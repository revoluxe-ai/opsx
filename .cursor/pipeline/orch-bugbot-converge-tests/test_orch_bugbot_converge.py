"""Fail-closed tests for orch-bugbot-converge."""

from __future__ import annotations

import importlib.util
from pathlib import Path

HELPER = (
    Path(__file__).resolve().parents[1]
    / "orch-bugbot-converge"
    / "orch_bugbot_converge.py"
)
ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("orch_bugbot_converge", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init(root: Path) -> None:
    opsx = root / "opsx"
    opsx.mkdir(parents=True)
    (opsx / "projectization.yaml").write_text(
        "status: COMPLETE\nnormal_commands_enabled: true\n", encoding="utf-8"
    )


def test_dry_run_does_not_claim_converged(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _load().run(root, {"against": "stage", "dry_run": True})
    assert result["STATUS"] == "INVOCATION_READY"
    assert result["BUGBOT_CONVERGED"] == "NO"
    assert result["INVOCATION"]["pipeline_id"] == "bugbot"
    assert result["OWNER_GO_AFTER_INVOCATION_REQUIRED"] == "NO"


def test_converged_requires_fresh_workflow_clean(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    module = _load()
    missing = module.run(root, {"against": "stage", "execution_id": "exec-1"})
    assert missing["BUGBOT_CONVERGED"] == "NO"
    stale = module.run(
        root,
        {
            "against": "stage",
            "head": "a" * 40,
            "execution_id": "exec-1",
            "workflow_receipt": {
                "status": "BUGBOT_CLEAN",
                "against": "stage",
                "head": "b" * 40,
                "execution_id": "exec-1",
                "check_count": 1,
                "workflow_id": "wf-1",
            },
        },
    )
    assert stale["REASON"] == "stale_workflow_evidence"
    ok = module.run(
        root,
        {
            "against": "stage",
            "head": "a" * 40,
            "execution_id": "exec-1",
            "workflow_receipt": {
                "status": "BUGBOT_CLEAN",
                "against": "stage",
                "head": "a" * 40,
                "execution_id": "exec-1",
                "check_count": 1,
                "workflow_id": "wf-1",
            },
        },
    )
    assert ok["STATUS"] == "BUGBOT_CONVERGED"
    assert ok["BUGBOT_CONVERGED"] == "YES"


def test_iteration_limit_stop(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _load().run(
        root,
        {
            "against": "stage",
            "max_cycles": 2,
            "workflow_receipt": {
                "status": "REPAIRING",
                "against": "stage",
                "check_count": 3,
            },
        },
    )
    assert result["STATUS"] == "ITERATION_LIMIT_REACHED"
    assert result["BUGBOT_CONVERGED"] == "NO"


def test_task_transport_forbidden(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _load().run(
        root, {"against": "stage", "check_transport": "TASK_BACKGROUND"}
    )
    assert result["REASON"] == "BUGBOT_SUBAGENTS_FORBIDDEN"


def test_runtime_string_matches_tempo_bugbot() -> None:
    module = _load()
    body = (ROOT / ".cursor" / "commands" / "tempo-bugbot.md").read_text(encoding="utf-8")
    assert "python tools/temporal/runtime.py run bugbot" in body
    assert "python tools/temporal/runtime.py run bugbot" in module.TEMPORAL_RUNTIME
    parser = (
        ROOT / "tools" / "temporal" / "tests" / "test_loop_session_reset.py"
    ).read_text(encoding="utf-8")
    assert 'REPAIR_PROMPT.startswith("/opsx-orch-bug-fix")' in parser
