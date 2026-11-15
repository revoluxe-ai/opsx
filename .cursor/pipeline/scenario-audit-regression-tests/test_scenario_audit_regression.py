"""Регрессия scenario audit: stub contract parity не даёт false-green PASS."""

from __future__ import annotations

import importlib.util
import json

import pytest
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scenario-audit-regression"
    / "scenario_audit_regression.py"
)
FIXTURES = MODULE_PATH.parent / "fixtures"
FALSE_GREEN = FIXTURES / "telegram_false_green" / "audit_manifest.json"
PARITY_OK = FIXTURES / "parity_ok" / "audit_manifest.json"
DRIFT_FIXTURES = (
    FIXTURES / "enum_parse_mode_drift" / "audit_manifest.json",
    FIXTURES / "scalar_text_drift" / "audit_manifest.json",
    FIXTURES / "nested_reply_markup_drift" / "audit_manifest.json",
)


def _load() -> ModuleType:
    """Загрузить оракул по пути файла."""

    spec = importlib.util.spec_from_file_location("scenario_audit_regression", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ORACLE = _load()


def test_false_green_stub_divergence_fails_audit_even_when_pytest_passed() -> None:
    """Stub с неверным полем message не должен получить PASS scenario audit."""

    verdict = ORACLE.evaluate_fixture_manifest(FALSE_GREEN)
    assert verdict.is_pass is False
    assert verdict.status == "FAIL"
    assert verdict.verdict == "STUB_CONTRACT_REGRESSION_FAIL"
    assert verdict.stub_contracts_fail == 1
    assert verdict.scenario_test_pytest_status == "passed"
    row = verdict.rows[0]
    assert row.verdict == "FAIL"
    assert row.request_parity == "FAIL"
    codes = {finding.code for finding in row.findings}
    assert "REQUEST_PARITY_FAIL" in codes
    assert "STUB_BOUNDARY_REQUEST_DRIFT" in codes
    assert "SUCCESS_RETURN_PARITY_FAIL" in codes
    assert row.success_return_parity == "FAIL"


def test_matching_stub_and_boundary_can_pass_audit() -> None:
    """Общий ContractCase: stub и boundary совпадают — audit PASS."""

    verdict = ORACLE.evaluate_fixture_manifest(PARITY_OK)
    assert verdict.is_pass is True
    assert verdict.status == "PASS"
    assert verdict.verdict == "STUB_CONTRACT_REGRESSION_PASS"
    assert verdict.pass_scope == "REGRESSION_FIXTURE_ONLY"
    assert verdict.stub_contracts_fail == 0
    assert verdict.rows[0].findings == ()


@pytest.mark.parametrize("manifest_path", DRIFT_FIXTURES, ids=lambda p: p.parent.name)
def test_value_and_nested_stub_drift_fails_audit(manifest_path: Path) -> None:
    """Enum, scalar и nested JSON drift не должны проходить regression audit."""

    verdict = ORACLE.evaluate_fixture_manifest(manifest_path)
    assert verdict.is_pass is False
    assert verdict.verdict == "STUB_CONTRACT_REGRESSION_FAIL"


def test_cli_audit_exit_code_matches_verdict() -> None:
    """CLI audit возвращает exit 1 на false-green и 0 на parity_ok."""

    fail_run = subprocess.run(
        [sys.executable, str(MODULE_PATH), "audit", "--manifest", str(FALSE_GREEN)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert fail_run.returncode == 1
    fail_payload = json.loads(fail_run.stdout)
    assert fail_payload["STATUS"] == "FAIL"
    assert fail_payload["VERDICT"] == "STUB_CONTRACT_REGRESSION_FAIL"
    assert fail_payload["PASS_SCOPE"] == "REGRESSION_FIXTURE_ONLY"

    pass_run = subprocess.run(
        [sys.executable, str(MODULE_PATH), "audit", "--manifest", str(PARITY_OK)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert pass_run.returncode == 0
    pass_payload = json.loads(pass_run.stdout)
    assert pass_payload["STATUS"] == "PASS"
    assert pass_payload["VERDICT"] == "STUB_CONTRACT_REGRESSION_PASS"
    assert pass_payload["PASS_SCOPE"] == "REGRESSION_FIXTURE_ONLY"


def test_same_key_response_ok_flag_drift_fails_audit(tmp_path: Path) -> None:
    """Одинаковые ключи ответа с разным ok MUST NOT проходить audit."""

    root = tmp_path / "fixture"
    root.mkdir()
    contract_path = root / "shared_contract.json"
    _ = contract_path.write_text(
        json.dumps(
            {
                "request": {
                    "method": "POST",
                    "path_suffix": "/sendMessage",
                    "json_body": {"chat_id": 1, "text": "x"},
                },
                "response": {"status": 200, "json_body": {"ok": True}},
            }
        ),
        encoding="utf-8",
    )
    boundary_path = root / "boundary.json"
    _ = boundary_path.write_text(
        json.dumps(
            {
                "request": {
                    "method": "POST",
                    "path_suffix": "/sendMessage",
                    "json_body": {"chat_id": 1, "text": "x"},
                },
                "response": {"status": 200, "json_body": {"ok": True}},
            }
        ),
        encoding="utf-8",
    )
    stub_path = root / "stub.json"
    _ = stub_path.write_text(
        json.dumps(
            {
                "request": {
                    "method": "POST",
                    "path_suffix": "/sendMessage",
                    "json_body": {"chat_id": 1, "text": "x"},
                },
                "response": {"status": 200, "json_body": {"ok": False}},
            }
        ),
        encoding="utf-8",
    )
    manifest_path = root / "audit_manifest.json"
    _ = manifest_path.write_text(
        json.dumps(
            {
                "shared_contract_source": "shared_contract.json",
                "stub_observed_call": "stub.json",
                "boundary_observed_call": "boundary.json",
                "scenario_test_pytest_status": "passed",
            }
        ),
        encoding="utf-8",
    )
    verdict = ORACLE.evaluate_fixture_manifest(manifest_path)
    assert verdict.is_pass is False
    assert any(
        finding.code == "SUCCESS_RETURN_PARITY_FAIL" for finding in verdict.rows[0].findings
    )


def test_response_json_keys_without_body_is_invalid() -> None:
    """Только json_keys без значений не может пройти как parity proof."""

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "shared_contract.json"
        _ = contract.write_text(
            json.dumps(
                {
                    "request": {
                        "method": "POST",
                        "path_suffix": "/sendMessage",
                        "json_body": {"chat_id": 1, "text": "x"},
                    },
                    "response": {"status": 200, "json_keys": ["ok"]},
                }
            ),
            encoding="utf-8",
        )
        stub = root / "stub.json"
        boundary = root / "boundary.json"
        for path in (stub, boundary):
            _ = path.write_text(
                json.dumps(
                    {
                        "request": {
                            "method": "POST",
                            "path_suffix": "/sendMessage",
                            "json_body": {"chat_id": 1, "text": "x"},
                        },
                        "response": {"status": 200, "json_keys": ["ok"]},
                    }
                ),
                encoding="utf-8",
            )
        manifest = root / "audit_manifest.json"
        _ = manifest.write_text(
            json.dumps(
                {
                    "shared_contract_source": "shared_contract.json",
                    "stub_observed_call": "stub.json",
                    "boundary_observed_call": "boundary.json",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ORACLE.StubContractAuditError) as exc:
            _ = ORACLE.evaluate_fixture_manifest(manifest)
        assert exc.value.code == "RESPONSE_SHAPE_INVALID"
