"""GREEN pairing must load the RED receipt identity hook.

A stale top-level test_content_identity is not authority when
operative_red_transition names an accepted on-disk digest and the design
binding supplies the oracle. A digest that matches no on-disk variant
stays rejected.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from workflows.implementation.stage_receipt_writer import classify_green_predecessor_pairing

_CORE = Path(__file__).resolve().parents[5]


def _legacy_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stamp_digest(repo: Path, red: dict[str, object]) -> str:
    payload = json.dumps(red)
    script = f"""
const fs = await import('node:fs');
const gate = await import('./opsx_tools/opsx-apply-red-transition-gate.mjs');
const red = {payload};
const digest = gate.computeRedOperativeStampDigest(red, {{ ok: true }}, {json.dumps(str(repo))});
console.log(JSON.stringify({{ digest }}));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_CORE,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout.strip().splitlines()[-1])
    return str(body["digest"])


def _red_and_implement(
    tmp_path: Path,
    *,
    top: str,
    operative: str,
) -> tuple[dict[str, object], dict[str, object]]:
    change_id = "pairing-hook"
    slice_id = "SLICE-001"
    test_rel = "tests/frozen/test_hold.py"
    design_rel = f"openspec/changes/{change_id}/apply-design/APPLY-DESIGN.json"
    _ = (tmp_path / "opsx").mkdir()
    test_path = tmp_path / test_rel
    test_path.parent.mkdir(parents=True)
    _ = test_path.write_text("def test_hold():\n    assert False\n", encoding="utf-8")
    design_path = tmp_path / design_rel
    design_path.parent.mkdir(parents=True)
    _ = design_path.write_text(
        json.dumps(
            {
                "status": "APPLY_DESIGN_READY",
                "slices": [
                    {
                        "slice_id": slice_id,
                        "red_oracle_bindings": [
                            {
                                "test_path": test_rel,
                                "authoritative_expected": "0",
                            },
                        ],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    command = f"poetry run pytest -q {test_rel}::test_hold"
    red: dict[str, object] = {
        "schema": "opsx.apply_red",
        "schema_version": 2,
        "evidence_contract": "per_command_v1",
        "change_id": change_id,
        "slice_id": slice_id,
        "status": "RED_PROVEN",
        "tests_frozen": True,
        "test_paths": [test_rel],
        "fixture_paths": [],
        "test_commands": [command],
        "command_executions": [
            {
                "command": command,
                "final_exit_code": 1,
                "final_classification": "INTENDED_BEHAVIOR_MISSING",
                "red_qualified": True,
                "pass_qualified": False,
                "attempts": [{"attempt": 1, "exit_code": 1}],
            },
        ],
        "test_content_identity": top,
        "behavior_obligation_identity": {
            "slice_id": slice_id,
            "planning_digest": "a" * 64,
            "obligation_digest": "b" * 64,
        },
        "apply_design_identity": {"machine_path": design_rel},
        "next_stage": "APPLY_IMPLEMENT",
    }
    red["operative_red_transition"] = {
        "verifier_id": "opsx-apply-red-transition-gate",
        "permitted_transition": "RED_PROVEN",
        "test_content_identity": operative,
        "decision_digest": _stamp_digest(tmp_path, red),
    }
    implement: dict[str, object] = {
        "status": "IMPLEMENTATION_WRITTEN",
        "slice_id": slice_id,
        "next_stage": "APPLY_GREEN",
        "tests_modified": False,
        "tests_executed": False,
        "frozen_test_content_identity": operative,
        "production_paths_created": ["src/pkg/widget.py"],
        "production_paths_modified": [],
        "red_identity": {
            "status": "RED_PROVEN",
            "slice_id": slice_id,
            "tests_frozen": True,
            "test_content_identity": operative,
        },
    }
    return red, implement


@pytest.mark.unit
def test_green_pairing_accepts_operative_digest_when_top_level_is_stale(
    tmp_path: Path,
) -> None:
    """Stale RED top-level digest does not erase an accepted operative freeze."""
    test_rel = tmp_path / "tests" / "frozen" / "test_hold.py"
    red, implement = _red_and_implement(
        tmp_path,
        top="ab" * 32,
        operative="pending",
    )
    operative = _legacy_digest(test_rel)
    assert operative != "ab" * 32
    red["operative_red_transition"]["test_content_identity"] = operative  # type: ignore[index]
    red["operative_red_transition"]["decision_digest"] = _stamp_digest(tmp_path, red)  # type: ignore[index]
    implement["frozen_test_content_identity"] = operative
    implement["red_identity"]["test_content_identity"] = operative  # type: ignore[index]
    pairing = classify_green_predecessor_pairing(
        repo_root=tmp_path,
        red=red,
        implement=implement,
    )
    assert pairing.get("ok") is True, pairing
    assert pairing.get("branch") == "NORMAL"


@pytest.mark.unit
def test_green_pairing_rejects_operative_digest_that_matches_no_bytes(
    tmp_path: Path,
) -> None:
    """A transition digest that is not an on-disk variant stays unauthorized."""
    red, implement = _red_and_implement(
        tmp_path,
        top="ab" * 32,
        operative="cd" * 32,
    )
    pairing = classify_green_predecessor_pairing(
        repo_root=tmp_path,
        red=red,
        implement=implement,
    )
    assert pairing.get("ok") is False
    assert pairing.get("code") == "IMPLEMENTATION_AUTHORITY_MISSING"
