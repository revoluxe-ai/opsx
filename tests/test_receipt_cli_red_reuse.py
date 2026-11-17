"""RED receipt materialize must not re-run a completed frozen suite on hop record."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

_HOOK = (
    Path(__file__).resolve().parents[1]
    / "opsx"
    / "opsx_runtime"
    / "workflows"
    / "implementation"
    / "receipt_cli_identity_hook.mjs"
)
_CLI = Path(__file__).resolve().parents[1] / "opsx_tools" / "opsx-record-slice-receipt-decision-cli.mjs"


def _init_git_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )


@pytest.mark.unit
def test_red_hop_materialize_reuses_prior_command_executions(tmp_path: Path) -> None:
    """``writeRedSuccess`` must skip live shape when executions already match commands."""
    assert _HOOK.is_file()
    assert _CLI.is_file()
    _init_git_repo(tmp_path)
    change_id = "example_change"
    slice_id = "slice-a"
    test_rel = "tests/unit/test_red_reuse_sample.py"
    module = tmp_path / test_rel
    module.parent.mkdir(parents=True)
    _ = module.write_text(
        "def test_scenario_red():\n"
        "    raise RuntimeError('OPSX_SCAFFOLD_NOT_IMPLEMENTED')\n",
        encoding="utf-8",
    )
    design = {
        "schema": "opsx.apply_design",
        "planning_identity": {"planning_digest": "a" * 64, "inputs": []},
        "repository_identity": {"inspected_digest": "b" * 64},
        "protected_context_identity": {"content_digest": "c" * 64},
        "slices": [
            {
                "slice_id": slice_id,
                "red_oracle_bindings": [
                    {
                        "test_path": test_rel,
                        "authoritative_expected": "NOT-MET",
                        "observed_intended_failures": [{"command": "test_scenario_red"}],
                    }
                ],
            }
        ],
    }
    design_path = (
        tmp_path / "openspec" / "changes" / change_id / "apply-design" / "APPLY-DESIGN.json"
    )
    design_path.parent.mkdir(parents=True)
    _ = design_path.write_text(json.dumps(design), encoding="utf-8")
    red_dir = tmp_path / "openspec" / "changes" / change_id / "apply-red" / slice_id
    red_dir.mkdir(parents=True)
    cmd = f"poetry run pytest -q {test_rel}::test_scenario_red"
    red_body = {
        "schema": "opsx.apply_red",
        "schema_version": 1,
        "change_id": change_id,
        "slice_id": slice_id,
        "status": "RED_PROVEN",
        "test_paths": [test_rel],
        "test_commands": [cmd],
        "command_executions": [
            {
                "command": cmd,
                "final_exit_code": 1,
                "final_classification": "INTENDED_BEHAVIOR_MISSING",
                "red_qualified": True,
                "pass_qualified": False,
                "attempts": [
                    {
                        "attempt": 1,
                        "exit_code": 1,
                        "failure_classification": "INTENDED_BEHAVIOR_MISSING",
                        "relevant_output": "OPSX_SCAFFOLD_NOT_IMPLEMENTED",
                    }
                ],
            }
        ],
        "tests_frozen": True,
        "test_content_identity": "d" * 64,
        "behavior_obligation_identity": {
            "slice_id": slice_id,
            "obligation_digest": "e" * 64,
        },
    }
    red_path = red_dir / "RED.json"
    _ = red_path.write_text(json.dumps(red_body, indent=2), encoding="utf-8")
    scaffold_dir = tmp_path / "openspec" / "changes" / change_id / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(
        json.dumps({"schema": "opsx.apply_scaffold", "status": "SCAFFOLD_READY"}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    payload = json.dumps(
        {
            "repo": str(tmp_path),
            "change_id": change_id,
            "decision": {
                "stage": "APPLY_RED",
                "slice_id": slice_id,
                "hop_status": "APPLY_COMPLETE",
            },
        }
    )
    start = time.monotonic()
    run = subprocess.run(
        ["node", "--import", _HOOK.as_uri(), str(_CLI)],
        input=payload,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    elapsed = time.monotonic() - start
    line = (run.stdout or "").strip().splitlines()
    body = json.loads(line[-1] if line else "{}")
    combined = (run.stdout or "") + (run.stderr or "")
    assert elapsed < 10.0, "reused executions must not spawn a live pytest suite"
    assert "reused" in combined or "test session starts" not in combined.lower()
    assert body.get("code") != "RED_SHAPE_FAILED", combined