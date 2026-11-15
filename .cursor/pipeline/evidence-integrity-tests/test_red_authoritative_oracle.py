"""R1 authoritative oracle: worker cannot redefine frozen APPLY-DESIGN obligation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
RED_CLI = REPO / "opsx_tools" / "opsx-apply-red.mjs"
FIX = REPO / ".cursor" / "commands" / "_fixtures" / "opsx-apply-red"
def _run_red(args: list[str], repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(RED_CLI), "--repo", str(repo), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def _node_json(script: str) -> dict[str, object]:
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(proc.stdout)


def _durable(red: dict[str, object], repo: Path) -> bool:
    payload = _node_json(
        f"""
        const {{ isDurableRedProven }} = await import("./opsx_tools/opsx-apply-pipeline-identity.mjs");
        const red = {json.dumps(red)};
        console.log(JSON.stringify({{ durable: isDurableRedProven(red, {json.dumps(str(repo))}) }}));
        """
    )
    return payload["durable"] is True


@pytest.fixture()
def mini_repo(tmp_path: Path) -> Path:
    root = tmp_path / "mini"
    root.mkdir(parents=True, exist_ok=True)
    tests = root / "tests" / "unit"
    tests.mkdir(parents=True, exist_ok=True)
    test_src = """// @opsx-red-spec-value: Y
// @opsx-red-behavior-value: X
import assert from "node:assert/strict";
import { test } from "node:test";
function behavior() { return "X"; }
test("lie", () => { assert.equal(behavior(), "Y"); });
"""
    (tests / "oracleMarkerLie.test.mjs").write_text(test_src, encoding="utf-8")
    (root / "authoritative-spec.txt").write_text("REQUIRED_BEHAVIOR_VALUE=X\n", encoding="utf-8")
    design_dir = root / "openspec" / "changes" / "fixture-hydrate" / "apply-design"
    design_dir.mkdir(parents=True, exist_ok=True)
    design = json.loads(
        (REPO / "openspec/changes/fixture-hydrate/apply-design/APPLY-DESIGN.json").read_text(
            encoding="utf-8",
        )
    )
    design["slices"][0]["red_test_targets_or_seams"] = [
        "tests/unit/oracleMarkerLie.test.mjs",
    ]
    design["slices"][0]["red_oracle_bindings"] = [
        {
            "test_path": "tests/unit/oracleMarkerLie.test.mjs",
            "authoritative_expected": "X",
            "obligation_ref": "required_behavior",
        }
    ]
    design_path = design_dir / "APPLY-DESIGN.json"
    design_path.write_text(json.dumps(design, indent=2), encoding="utf-8")
    return root, design_path


def test_oracle_marker_lie_rejected_cli_and_downstream(
    mini_repo: tuple[Path, Path],
) -> None:
    root, design_path = mini_repo
    """Authoritative X; worker marks spec Y; implementation already X."""
    design_doc = json.loads(design_path.read_text(encoding="utf-8"))
    golden = json.loads((FIX / "03-red-proven.RED.json").read_text(encoding="utf-8"))
    test_rel = "tests/unit/oracleMarkerLie.test.mjs"
    cmd = f"node --test {test_rel}"
    h = hashlib.sha256()
    h.update((root / test_rel).read_bytes())
    identity = h.hexdigest()
    digest_proc = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            f"""
import {{ readFileSync }} from 'fs';
import {{ computeSliceBehaviorObligationDigest }} from './opsx_tools/opsx-apply-red-oracle-authority.mjs';
const design = JSON.parse(readFileSync({json.dumps(str(design_path))}, 'utf8'));
const slice = design.slices.find((s) => s.slice_id === 'SLICE-001');
console.log(JSON.stringify(computeSliceBehaviorObligationDigest(design, slice)));
""",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    computed = json.loads(digest_proc.stdout)
    art = {
        **golden,
        "test_paths": [test_rel],
        "test_commands": [cmd],
        "test_content_identity": identity,
        "command_executions": [
            {
                "test_path": test_rel,
                "command": cmd,
                "attempts": [
                    {
                        "attempt": 1,
                        "exit_code": 1,
                        "failure_classification": "INTENDED_BEHAVIOR_MISSING",
                        "relevant_output": "AssertionError: expected 'Y', actual 'X'",
                    }
                ],
                "final_exit_code": 1,
                "final_classification": "INTENDED_BEHAVIOR_MISSING",
                "red_qualified": True,
            }
        ],
        "apply_design_identity": {
            **golden["apply_design_identity"],
            "machine_path": "openspec/changes/fixture-hydrate/apply-design/APPLY-DESIGN.json",
        },
        "behavior_obligation_identity": {
            "slice_id": "SLICE-001",
            "planning_digest": design_doc["planning_identity"]["planning_digest"],
            "obligation_digest": computed["digest"],
        },
    }
    red_path = root / "lie.RED.json"
    red_path.write_text(json.dumps(art, indent=2), encoding="utf-8")
    proc = _run_red([str(red_path)], root)
    body = json.loads(proc.stdout)
    assert proc.returncode != 0
    assert body["status"] == "FAIL"
    codes = {e.get("code") for e in body.get("errors", [])}
    assert "R1_ORACLE" in codes
    assert _durable(art, root) is False


def test_missing_obligation_identity_fails_closed() -> None:
    base = json.loads((FIX / "03-red-proven.RED.json").read_text(encoding="utf-8"))
    del base["behavior_obligation_identity"]
    assert _durable(base, REPO) is False
