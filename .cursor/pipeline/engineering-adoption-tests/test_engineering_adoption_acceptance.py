"""Acceptance tests for OPSX project engineering adoption (15 scenarios)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import write_minimal_projectization, write_verified_adoption
from tools.openspec.opsx_continue_skill_contract import evaluate_planning_write_preflight
from tools.openspec.opsx_engineering_adoption_gate import (
    BOOTSTRAP_COMMAND_ALLOWLIST,
    engineering_adoption_allows_mutations,
    validate_quarantine_record,
    validate_verified_adoption,
)

from engineering_adoption import (  # type: ignore[import-not-found]
    TransitionResult,
    _run_bounded,
    transition,
    verify_active_gates,
)


def _mini_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "migrations").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    write_minimal_projectization(root)
    opsx = root / "opsx"
    (opsx / "project").mkdir(parents=True, exist_ok=True)
    caps = {
        "product": {"language": "python", "package_manager": "poetry"},
        "quality": {"test": {"status": "resolved", "command": "pytest -q"}},
    }
    _ = (opsx / "project" / "capabilities.yaml").write_text(
        yaml.safe_dump(caps, sort_keys=False),
        encoding="utf-8",
    )
    return root


def test_01_unadopted_blocks_mutation_commands(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    result = engineering_adoption_allows_mutations(root, command_id="opsx-continue")
    assert not result.ok
    assert result.reason == "OPSX_ADOPTION_REQUIRED"


def test_02_bootstrap_paths_remain_available(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    for cmd in BOOTSTRAP_COMMAND_ALLOWLIST:
        allowed = engineering_adoption_allows_mutations(root, command_id=cmd)
        assert allowed.ok, cmd


def test_03_forged_adopted_marker_cannot_bypass(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    write_verified_adoption(root, forged_hash=True)
    result = validate_verified_adoption(root)
    assert not result.ok


def test_04_valid_adoption_enables_commands(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    write_verified_adoption(root)
    result = engineering_adoption_allows_mutations(root, command_id="opsx-continue")
    assert result.ok


def test_05_new_standard_errors_fail_gate(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    write_verified_adoption(root)
    baseline_path = root / "opsx/engineering-adoption/pyright-baseline.json"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps({"diagnostic_signatures": ["aaa"]}),
        encoding="utf-8",
    )
    policy = {"pyright_policy": {}}
    new_error = {"file": "src/app.py", "severity": "error", "message": "new error", "rule": "reportGeneralTypeIssues"}
    with mock.patch("engineering_adoption._run_bounded") as bounded:
        bounded.side_effect = [
            {"returncode": 0, "timed_out": False},  # compileall
            {"returncode": 0, "timed_out": False},  # pytest
            {"returncode": 0, "timed_out": False},  # pyright --version
            {"returncode": 1, "timed_out": False, "stdout_tail": json.dumps({"generalDiagnostics": [new_error]})},
            {"returncode": 0, "timed_out": False},  # full pytest
        ]
        verify = verify_active_gates(root, policy, None)
    assert verify["active_quality_gates"]["pyright_no_new_standard_errors"] is False


def test_06_legacy_baseline_not_misrepresented_as_fixed(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    write_verified_adoption(root)
    baseline = {
        "diagnostic_signatures": ["legacy-sig-1", "legacy-sig-2"],
        "diagnostic_count": 2,
    }
    path = root / "opsx/engineering-adoption/pyright-baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    policy = {"pyright_policy": {}}
    with mock.patch("engineering_adoption._run_bounded") as bounded:
        bounded.side_effect = [
            {"returncode": 0, "timed_out": False},
            {"returncode": 0, "timed_out": False},
            {"returncode": 0, "timed_out": False},
            {
                "returncode": 0,
                "timed_out": False,
                "stdout_tail": json.dumps({"generalDiagnostics": []}),
            },
            {"returncode": 0, "timed_out": False},
        ]
        verify = verify_active_gates(root, policy, None)
    assert verify["active_quality_gates"]["pyright_no_new_standard_errors"] is True
    assert verify["active_quality_gates"].get("pyright_new_signatures") == []


def test_07_modified_files_strict_policy(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    write_verified_adoption(root)
    diag = {
        "file": str(root / "src/app.py"),
        "severity": "error",
        "message": "bad",
        "rule": "reportGeneralTypeIssues",
        "range": {"start": {"line": 1}},
    }
    policy = {"pyright_policy": {}}
    with mock.patch("engineering_adoption._git_changed_python_files", return_value=["src/app.py"]):
        with mock.patch("engineering_adoption._run_bounded") as bounded:
            bounded.side_effect = [
                {"returncode": 0, "timed_out": False},
                {"returncode": 0, "timed_out": False},
                {"returncode": 0, "timed_out": False},
                {
                    "returncode": 1,
                    "timed_out": False,
                    "stdout_tail": json.dumps({"generalDiagnostics": [diag]}),
                },
                {"returncode": 0, "timed_out": False},
            ]
            verify = verify_active_gates(root, policy, None)
    assert verify["active_quality_gates"]["pyright_strict_modified_clean"] is False


def test_08_hanging_tests_terminate_bounded(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)

    def slow_run(*_args, **_kwargs):
        time.sleep(0.05)
        return {"returncode": 0, "timed_out": False, "stdout_tail": "", "stderr_tail": ""}

    with mock.patch("engineering_adoption.subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 30)):
        result = _run_bounded([sys.executable, "-c", "import time; time.sleep(9)"], cwd=root, timeout_s=0.01)
    assert result["timed_out"] is True


def test_09_authorized_quarantine_excluded_from_active_suite(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    write_verified_adoption(root, with_quarantine=True)
    result = validate_verified_adoption(root)
    assert result.ok
    quarantine = json.loads(
        (root / "opsx/engineering-adoption/quarantined-tests.json").read_text(encoding="utf-8")
    )
    assert quarantine["records"][0]["test_node_id"].startswith("tests/")


def test_10_unauthorized_quarantine_fails_closed(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    write_verified_adoption(root, with_quarantine=True)
    lock_path = root / "opsx/engineering-adoption.yaml"
    lock = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    lock["status"] = "ADOPTED_WITH_QUARANTINE"
    lock_path.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")
    bad = {
        "schema": "opsx.engineering_adoption.quarantine",
        "records": [
            {
                "test_node_id": "tests/x.py::t",
                "reason": "x",
                "owner_authorization_ref": "owner-message:1",
                "evidence": "e",
                "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                "reactivation_condition": "fix",
                "risk": "medium",
            }
        ],
    }
    (root / "opsx/engineering-adoption/quarantined-tests.json").write_text(
        json.dumps(bad),
        encoding="utf-8",
    )
    result = validate_verified_adoption(root)
    assert not result.ok
    assert result.reason in {"QUARANTINE_EXPIRED", "QUARANTINE_RECORD_INCOMPLETE"}


def test_11_quarantined_tests_cannot_count_as_pass() -> None:
    record = {
        "test_node_id": "t",
        "reason": "r",
        "owner_authorization_ref": "owner-message:1",
        "evidence": "e",
        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "reactivation_condition": "x",
        "risk": "high",
        "counts_as_pass": True,
    }
    assert validate_quarantine_record(record, now=datetime.now(UTC)) == "QUARANTINE_COUNTS_AS_PASS_FORBIDDEN"


def test_12_config_change_invalidates_stale_evidence(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    write_verified_adoption(root)
    caps = root / "opsx/project/capabilities.yaml"
    caps.write_text(caps.read_text(encoding="utf-8") + "# touch\n", encoding="utf-8")
    result = validate_verified_adoption(root)
    assert not result.ok
    assert result.reason == "OPSX_ADOPTION_STALE"


def test_13_uncommitted_product_changes_survive_onboarding(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    product = root / "src/app.py"
    original = product.read_text(encoding="utf-8")
    product.write_text(original + "# dirty\n", encoding="utf-8")
    tr = transition(root, "DISCOVER")
    assert tr.ok, tr.detail
    with mock.patch(
        "engineering_adoption.assess_project",
        return_value={"passing_checks": [], "failing_checks": [], "hanging_checks": []},
    ):
        tr = transition(root, "ASSESS")
    assert tr.ok, tr.detail
    tr = transition(root, "POLICY_PROPOSED")
    assert tr.ok, tr.detail
    assert product.read_text(encoding="utf-8").endswith("# dirty\n")


def test_14_repeated_adoption_idempotent_when_unchanged(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    write_verified_adoption(root)
    before = (root / "opsx/engineering-adoption.yaml").read_text(encoding="utf-8")
    tr = transition(root, "STATUS")
    assert tr.ok
    after = (root / "opsx/engineering-adoption.yaml").read_text(encoding="utf-8")
    assert before == after


def test_15_failure_never_produces_false_adopted(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    tr = transition(root, "VERIFY")
    assert not tr.ok
    lock = root / "opsx/engineering-adoption.yaml"
    assert not lock.is_file()


def test_16_invalid_transition_skips_adopted_lock(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    tr = transition(root, "CONFIGURE")
    assert not tr.ok
    assert tr.status == "FAIL_CLOSED"
    assert not (root / "opsx/engineering-adoption.yaml").is_file()


def test_continue_preflight_blocks_without_adoption(tmp_path: Path) -> None:
    """Integration: shared execution authority blocks /opsx-continue writes."""

    root = _mini_repo(tmp_path)
    skill_src = REPO_ROOT / ".cursor/skills/openspec-continue-change/SKILL.md"
    import shutil

    from tests.unit.tools.test_openspec_guard import _write_minimal_openspec_tree, _write_minimal_policy
    from tests.unit.tools.test_openspec_cli_provider import _copy_provider

    _write_minimal_policy(root)
    _write_minimal_openspec_tree(root)
    shutil.copytree(REPO_ROOT / "tools/openspec", root / "tools/openspec", dirs_exist_ok=True)
    skill_dst = root / "opsx/core/.cursor/skills/openspec-continue-change"
    skill_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(skill_src, skill_dst / "SKILL.md")
    shutil.copy2(
        REPO_ROOT / ".cursor/commands/opsx-continue.md",
        root / ".cursor/commands/opsx-continue.md",
    )
    _copy_provider(root)
    pre = evaluate_planning_write_preflight(root)
    assert not pre.ok
    assert pre.reason == "OPSX_ADOPTION_REQUIRED"


def test_16_pyright_truncated_json_is_fail_closed(tmp_path: Path) -> None:
    from engineering_adoption import _checked_pyright_diagnostics, _parse_pyright_diagnostics

    assert _parse_pyright_diagnostics('{"generalDiagnostics": [') is None
    assert _checked_pyright_diagnostics({"returncode": 1, "timed_out": False, "stdout_tail": ""}) is None
    root = _mini_repo(tmp_path)
    with mock.patch("engineering_adoption._run_bounded") as bounded:
        bounded.side_effect = [
            {"returncode": 0, "timed_out": False},
            {"returncode": 0, "timed_out": False},
            {"returncode": 0, "timed_out": False},
            {"returncode": 1, "timed_out": False, "stdout_full": '{"generalDiagnostics": [', "stdout_tail": ""},
            {"returncode": 0, "timed_out": False},
        ]
        outcome = verify_active_gates(root, {"pyright_policy": {}}, None)
    assert outcome["active_quality_gates"]["pyright_output_untrusted"] is True
    assert outcome["active_quality_gates"]["pyright_no_new_standard_errors"] is False
    assert outcome["verify_ok"] is False


def test_17_pyright_full_json_not_truncated(tmp_path: Path) -> None:
    from engineering_adoption import _checked_pyright_diagnostics
    payload = json.dumps({"generalDiagnostics": [{"file": f"src/file_{i}.py", "severity": "error"} for i in range(300)]})
    assert len(payload) > 4000
    run = {"returncode": 1, "timed_out": False, "stdout_tail": payload[-4000:], "stdout_full": payload}
    assert len(_checked_pyright_diagnostics(run) or []) == 300
    assert _checked_pyright_diagnostics({**run, "stdout_full": payload[-4000:]}) is None


def test_18_existing_strict_gate_cannot_be_bypassed(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    gate = root / "tools/pyright/run_local_gate.sh"
    gate.parent.mkdir(parents=True)
    gate.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    with mock.patch("engineering_adoption._run_bounded") as bounded:
        bounded.side_effect = [
            {"returncode": 0, "timed_out": False},
            {"returncode": 0, "timed_out": False},
            {"returncode": 0, "timed_out": False},
            {"returncode": 0, "timed_out": False, "stdout_tail": '{"generalDiagnostics": []}'},
            {"returncode": 1, "timed_out": False},
            {"returncode": 0, "timed_out": False},
        ]
        outcome = verify_active_gates(root, {"pyright_policy": {}}, None)
    assert outcome["active_quality_gates"]["strict_on_touch"] is False
    assert outcome["verify_ok"] is False
