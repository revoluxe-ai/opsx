"""Регрессия operative gate RED evidence integrity (R1–R5), реконструкция."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
RED_CLI = REPO / "opsx_tools" / "opsx-apply-red.mjs"
FIX = REPO / ".cursor" / "commands" / "_fixtures" / "opsx-apply-red"
YALTA_RED = (
    REPO / "openspec/changes/invest-yalta-telegram-funnel/apply-red/SLICE-001/RED.json"
)
VISUAL_RED = REPO / "openspec/changes/visual/apply-red/SLICE-001/RED.json"


def _run_red(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(RED_CLI), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def _json(stdout: str) -> dict[str, object]:
    return json.loads(stdout)


def _frozen_test_identity(paths: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(paths):
        digest.update((REPO / rel).read_bytes())
    return digest.hexdigest()


def test_attack_a_false_oracle_fails_closed(tmp_path: Path) -> None:
    """R1: spec===behavior, но assertion ожидает Y — не RED_PROVEN."""
    base = json.loads((FIX / "03-red-proven.RED.json").read_text(encoding="utf-8"))
    adv_test = "tests/unit/redAdversarialFalseOracle.test.mjs"
    cmd = f"node --test {adv_test}"
    base["test_paths"] = [adv_test]
    base["test_commands"] = [cmd]
    base["command_executions"] = [
        {
            "test_path": adv_test,
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
    ]
    base["test_content_identity"] = _frozen_test_identity([adv_test])
    path = tmp_path / "A-false-oracle.RED.json"
    path.write_text(json.dumps(base, indent=2), encoding="utf-8")
    proc = _run_red([str(path)])
    body = _json(proc.stdout)
    assert proc.returncode != 0
    assert body["status"] == "FAIL"


def test_attack_b_fabricated_execution_fails_closed(tmp_path: Path) -> None:
    """R2: выдуманный test path / hash не проходит gate."""
    base = json.loads((FIX / "03-red-proven.RED.json").read_text(encoding="utf-8"))
    base["test_paths"] = ["tests/unit/does-not-exist.test.mjs"]
    base["test_commands"] = ["node --test tests/unit/does-not-exist.test.mjs"]
    base["test_content_identity"] = "f" * 64
    base["command_executions"][0]["test_path"] = "tests/unit/does-not-exist.test.mjs"
    base["command_executions"][0]["command"] = "node --test tests/unit/does-not-exist.test.mjs"
    path = tmp_path / "B-fabricated.RED.json"
    path.write_text(json.dumps(base, indent=2), encoding="utf-8")
    proc = _run_red([str(path)])
    body = _json(proc.stdout)
    assert proc.returncode != 0
    assert body["status"] == "FAIL"


def test_attack_d_test_thrown_sentinel_fails_closed(tmp_path: Path) -> None:
    """R4: sentinel, брошенный в тесте без scaffold import, не RED."""
    base = json.loads((FIX / "03-red-proven.RED.json").read_text(encoding="utf-8"))
    adv = "tests/unit/redAdversarialFakeSentinel.test.mjs"
    base["test_paths"] = [adv]
    base["test_content_identity"] = _frozen_test_identity([adv])
    base["test_commands"] = ["node --test tests/unit/redAdversarialFakeSentinel.test.mjs"]
    base["command_executions"] = [
        {
            "test_path": "tests/unit/redAdversarialFakeSentinel.test.mjs",
            "command": "node --test tests/unit/redAdversarialFakeSentinel.test.mjs",
            "attempts": [
                {
                    "attempt": 1,
                    "exit_code": 1,
                    "failure_classification": "INTENDED_BEHAVIOR_MISSING",
                    "relevant_output": "OPSX_SCAFFOLD_NOT_IMPLEMENTED",
                }
            ],
            "final_exit_code": 1,
            "final_classification": "INTENDED_BEHAVIOR_MISSING",
            "red_qualified": True,
        }
    ]
    path = tmp_path / "D-fake-sentinel.RED.json"
    path.write_text(json.dumps(base, indent=2), encoding="utf-8")
    proc = _run_red([str(path)])
    assert proc.returncode != 0


def test_golden_03_red_proven_operative_pass() -> None:
    proc = _run_red([str(FIX / "03-red-proven.RED.json")])
    body = _json(proc.stdout)
    assert proc.returncode == 0
    assert body["status"] == "PASS"
    assert body["artifact_status"] == "RED_PROVEN"


def test_yalta_historical_read_pass() -> None:
    proc = _run_red(["--historical-read", str(YALTA_RED)])
    body = _json(proc.stdout)
    assert proc.returncode == 0
    assert body.get("historical_read") is True


def test_negative_19_incomplete_fails() -> None:
    proc = _run_red([str(FIX / "19-incomplete-old-v1.RED.json")])
    assert proc.returncode != 0


def _node_json(script: str) -> dict[str, object]:
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _durable_red_proven(red: dict[str, object]) -> bool:
    payload = _node_json(
        f"""
        await import("./opsx_tools/opsx-apply-red-transition-gate.mjs");
        const {{ isDurableRedProven }} = await import("./opsx_tools/opsx-apply-pipeline-identity.mjs");
        const red = {json.dumps(red)};
        console.log(JSON.stringify({{ durable: isDurableRedProven(red) }}));
        """
    )
    return payload["durable"] is True


def test_status_only_red_stub_rejected_by_consumer() -> None:
    assert _durable_red_proven({"status": "RED_PROVEN"}) is False


def test_attack_a_downstream_not_durable(tmp_path: Path) -> None:
    base = json.loads((FIX / "03-red-proven.RED.json").read_text(encoding="utf-8"))
    adv_test = "tests/unit/redAdversarialFalseOracle.test.mjs"
    cmd = f"node --test {adv_test}"
    base["test_paths"] = [adv_test]
    base["test_commands"] = [cmd]
    base["command_executions"] = [
        {
            "test_path": adv_test,
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
    ]
    base["test_content_identity"] = _frozen_test_identity([adv_test])
    assert _durable_red_proven(base) is False


def test_attack_b_downstream_not_durable(tmp_path: Path) -> None:
    base = json.loads((FIX / "03-red-proven.RED.json").read_text(encoding="utf-8"))
    base["test_paths"] = ["tests/unit/does-not-exist.test.mjs"]
    base["test_commands"] = ["node --test tests/unit/does-not-exist.test.mjs"]
    base["test_content_identity"] = "f" * 64
    base["command_executions"][0]["test_path"] = "tests/unit/does-not-exist.test.mjs"
    base["command_executions"][0]["command"] = "node --test tests/unit/does-not-exist.test.mjs"
    assert _durable_red_proven(base) is False


def test_attack_d_downstream_not_durable() -> None:
    base = json.loads((FIX / "03-red-proven.RED.json").read_text(encoding="utf-8"))
    adv = "tests/unit/redAdversarialFakeSentinel.test.mjs"
    base["test_paths"] = [adv]
    base["test_content_identity"] = _frozen_test_identity([adv])
    base["test_commands"] = ["node --test tests/unit/redAdversarialFakeSentinel.test.mjs"]
    base["command_executions"] = [
        {
            "test_path": adv,
            "command": "node --test tests/unit/redAdversarialFakeSentinel.test.mjs",
            "attempts": [
                {
                    "attempt": 1,
                    "exit_code": 1,
                    "failure_classification": "INTENDED_BEHAVIOR_MISSING",
                    "relevant_output": "OPSX_SCAFFOLD_NOT_IMPLEMENTED",
                }
            ],
            "final_exit_code": 1,
            "final_classification": "INTENDED_BEHAVIOR_MISSING",
            "red_qualified": True,
        }
    ]
    assert _durable_red_proven(base) is False


def test_golden_03_downstream_durable() -> None:
    base = json.loads((FIX / "03-red-proven.RED.json").read_text(encoding="utf-8"))
    assert _durable_red_proven(base) is True


def test_forged_operative_stamp_rejected() -> None:
    base = json.loads((FIX / "03-red-proven.RED.json").read_text(encoding="utf-8"))
    base["operative_red_transition"] = {
        "verifier_id": "opsx-apply-red-transition-gate",
        "permitted_transition": "RED_PROVEN",
        "decision_digest": "0" * 64,
    }
    assert _durable_red_proven(base) is False


def test_visual_slice001_red_operative_pass() -> None:
    """Регрессия: visual SLICE-001 RED operative (pytest launcher + python scaffold)."""
    probe = subprocess.run(
        [
            "../stroy-system/.venv/Scripts/python.exe",
            "-m",
            "pytest",
            "-q",
            "tests/unit/channels/telegram/invest_yalta/test_photo_delivery.py::test_registry_intro_cover_sendphoto_not_unknown_asset",
            "tests/unit/channels/telegram/invest_yalta/test_photo_delivery.py::test_cover_for_ux_state_maps_start_and_confirmed",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        proc = _run_red(["--historical-read", str(VISUAL_RED)])
        body = _json(proc.stdout)
        assert proc.returncode == 0
        assert body["status"] == "PASS"
        assert body.get("historical_read") is True
        return
    proc = _run_red([str(VISUAL_RED)])
    body = _json(proc.stdout)
    assert proc.returncode == 0, proc.stdout
    assert body["status"] == "PASS"
    assert body["artifact_status"] == "RED_PROVEN"


def test_cli_pass_emits_transition_decision() -> None:
    proc = _run_red([str(FIX / "03-red-proven.RED.json")])
    body = _json(proc.stdout)
    assert proc.returncode == 0
    decision = body.get("transition_decision")
    assert isinstance(decision, dict)
    assert decision.get("permitted_transition") == "RED_PROVEN"
    assert isinstance(decision.get("decision_digest"), str)
    assert len(decision["decision_digest"]) == 64


def test_coverage_reconcile_lie_fails_closed(tmp_path: Path) -> None:
    """R3: ложный POST_GREEN_PASS отклоняется в mode=coverage-reconcile."""
    lie = {
        "schema": "opsx.apply_coverage_reconcile",
        "status": "COVERAGE_RECONCILED",
        "historical_red_fabricated": False,
        "original_red_mutated": False,
        "discovered_after": "GREEN_PROVEN",
        "test_paths": ["tests/unit/fixtureHydrateCoordinatorRed.test.mjs"],
        "executions": [
            {
                "test_path": "tests/unit/fixtureHydrateCoordinatorRed.test.mjs",
                "command": (
                    "node --test --test-name-pattern ^hydrate coordinator coalesces "
                    "in-flight hydrates$ tests/unit/fixtureHydrateCoordinatorRed.test.mjs"
                ),
                "exit_code": 0,
                "classification": "POST_GREEN_PASS",
            }
        ],
    }
    path = tmp_path / "reconcile.json"
    path.write_text(json.dumps(lie, indent=2), encoding="utf-8")
    proc = _run_red(["--mode", "coverage-reconcile", str(path)])
    assert proc.returncode != 0
