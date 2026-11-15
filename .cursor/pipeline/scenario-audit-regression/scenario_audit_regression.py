"""Машинная проверка stub contract parity для scenario audit.

Регрессия ловит false-green: scenario seam зелёный при stub, который не
совпадает с общим ContractCase и с boundary integration. Оракул не пишет в
продуктовые хранилища и не запускает pytest — только сравнивает зафиксированные
формы вызова.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


CONTRACT_ID = "OPSX_SCENARIO_AUDIT_REGRESSION_V0"
ORACLE_REPO_PATH = (
    ".cursor/pipeline/scenario-audit-regression/scenario_audit_regression.py"
)


class StubContractAuditError(ValueError):
    """Некорректный manifest или отсутствующий файл фикстуры."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class ParityFinding:
    """Одно нарушение parity между сторонами или контрактом."""

    field: str
    code: str
    detail: str


@dataclass(frozen=True)
class StubContractRowVerdict:
    """Вердикт по одной строке STUB_CONTRACT."""

    scenario_id: str
    stub_symbol: str
    boundary_symbol: str
    shared_contract_source: str
    request_parity: str
    success_return_parity: str
    verdict: str
    findings: tuple[ParityFinding, ...]


@dataclass(frozen=True)
class ScenarioAuditVerdict:
    """Итог machine-check для одного regression fixture."""

    status: str
    verdict: str
    contract_id: str
    stub_contracts_total: int
    stub_contracts_pass: int
    stub_contracts_fail: int
    rows: tuple[StubContractRowVerdict, ...]
    scenario_test_pytest_status: str | None

    pass_scope: str = "REGRESSION_FIXTURE_ONLY"

    @property
    def is_pass(self) -> bool:
        return (
            self.status == "PASS"
            and self.verdict == "STUB_CONTRACT_REGRESSION_PASS"
            and self.pass_scope == "REGRESSION_FIXTURE_ONLY"
        )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise StubContractAuditError("FIXTURE_MISSING", str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StubContractAuditError("FIXTURE_INVALID_JSON", f"{path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StubContractAuditError("FIXTURE_NOT_OBJECT", str(path))
    return payload


def _canonical_json_value(value: Any) -> Any:
    """Рекурсивно канонизировать JSON-значение для сравнения parity."""

    if isinstance(value, dict):
        return {str(key): _canonical_json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value
    if value is None:
        return None
    return str(value)


def _normalize_call_shape(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Привести форму HTTP-вызова к канону для сравнения (значения и вложенность)."""

    method = str(raw.get("method", "")).upper()
    path_suffix = str(raw.get("path_suffix", ""))
    json_body = raw.get("json_body")
    if json_body is None:
        json_body = {}
    if not isinstance(json_body, dict):
        raise StubContractAuditError("CALL_SHAPE_INVALID", "json_body must be an object")
    return {
        "method": method,
        "path_suffix": path_suffix,
        "json_body": _canonical_json_value(json_body),
    }


def _normalize_response_shape(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Сравнить HTTP-ответ: status и семантическое тело (json_body обязателен)."""

    status = int(raw.get("status", 0))
    json_body = raw.get("json_body")
    if json_body is None:
        raise StubContractAuditError(
            "RESPONSE_SHAPE_INVALID",
            "response.json_body is required (json_keys alone is not parity proof)",
        )
    if not isinstance(json_body, dict):
        raise StubContractAuditError("RESPONSE_SHAPE_INVALID", "json_body must be an object")
    return {"status": status, "json_body": _canonical_json_value(json_body)}


def _contract_canonical(contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    request = contract.get("request")
    response = contract.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise StubContractAuditError("CONTRACT_INCOMPLETE", "request and response required")
    return _normalize_call_shape(request), _normalize_response_shape(response)


def _compare_to_contract(
    label: str,
    observed: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> tuple[str, tuple[ParityFinding, ...]]:
    expected_request, expected_response = _contract_canonical(contract)
    try:
        actual_request = _normalize_call_shape(observed.get("request", {}))
        actual_response = _normalize_response_shape(observed.get("response", {}))
    except StubContractAuditError as exc:
        return "FAIL", (ParityFinding(label, exc.code, exc.detail),)

    findings: list[ParityFinding] = []
    if actual_request != expected_request:
        findings.append(
            ParityFinding(
                f"{label}.request",
                "REQUEST_PARITY_FAIL",
                f"expected {expected_request!r}, got {actual_request!r}",
            )
        )
    if actual_response != expected_response:
        findings.append(
            ParityFinding(
                f"{label}.response",
                "SUCCESS_RETURN_PARITY_FAIL",
                f"expected {expected_response!r}, got {actual_response!r}",
            )
        )
    if findings:
        return "FAIL", tuple(findings)
    return "PASS", ()


def _compare_sides(
    stub: Mapping[str, Any],
    boundary: Mapping[str, Any],
) -> tuple[str, tuple[ParityFinding, ...]]:
    try:
        stub_request = _normalize_call_shape(stub.get("request", {}))
        stub_response = _normalize_response_shape(stub.get("response", {}))
        boundary_request = _normalize_call_shape(boundary.get("request", {}))
        boundary_response = _normalize_response_shape(boundary.get("response", {}))
    except StubContractAuditError as exc:
        return "FAIL", (ParityFinding("stub_vs_boundary", exc.code, exc.detail),)

    findings: list[ParityFinding] = []
    if stub_request != boundary_request:
        findings.append(
            ParityFinding(
                "executable.request",
                "STUB_BOUNDARY_REQUEST_DRIFT",
                f"stub {stub_request!r} != boundary {boundary_request!r}",
            )
        )
    if stub_response != boundary_response:
        findings.append(
            ParityFinding(
                "executable.response",
                "STUB_BOUNDARY_RESPONSE_DRIFT",
                f"stub {stub_response!r} != boundary {boundary_response!r}",
            )
        )
    if findings:
        return "FAIL", tuple(findings)
    return "PASS", ()


def evaluate_fixture_manifest(manifest_path: Path) -> ScenarioAuditVerdict:
    """Проверить один regression fixture: stub MUST совпасть с contract и boundary."""

    manifest = _load_json(manifest_path)
    fixture_root = manifest_path.parent
    contract_rel = str(manifest.get("shared_contract_source", "")).strip()
    stub_rel = str(manifest.get("stub_observed_call", "")).strip()
    boundary_rel = str(manifest.get("boundary_observed_call", "")).strip()
    if not contract_rel or not stub_rel or not boundary_rel:
        raise StubContractAuditError(
            "MANIFEST_INCOMPLETE",
            "shared_contract_source, stub_observed_call, boundary_observed_call required",
        )

    contract = _load_json(fixture_root / contract_rel)
    stub_side = _load_json(fixture_root / stub_rel)
    boundary_side = _load_json(fixture_root / boundary_rel)

    scenario_id = str(manifest.get("scenario_id", "unknown"))
    stub_symbol = str(manifest.get("stub_symbol", "unknown"))
    boundary_symbol = str(manifest.get("boundary_symbol", "unknown"))
    pytest_status = manifest.get("scenario_test_pytest_status")
    pytest_status_str = str(pytest_status) if pytest_status is not None else None

    findings: list[ParityFinding] = []
    stub_req, stub_req_findings = _compare_to_contract("stub", stub_side, contract)
    findings.extend(stub_req_findings)
    boundary_req, boundary_findings = _compare_to_contract(
        "boundary", boundary_side, contract
    )
    findings.extend(boundary_findings)
    _, executable_findings = _compare_sides(stub_side, boundary_side)
    findings.extend(executable_findings)

    request_codes = {
        "REQUEST_PARITY_FAIL",
        "STUB_BOUNDARY_REQUEST_DRIFT",
    }
    response_codes = {
        "SUCCESS_RETURN_PARITY_FAIL",
        "STUB_BOUNDARY_RESPONSE_DRIFT",
    }
    request_parity = (
        "PASS" if not any(f.code in request_codes for f in findings) else "FAIL"
    )
    success_return_parity = (
        "PASS" if not any(f.code in response_codes for f in findings) else "FAIL"
    )

    row_pass = not findings and stub_req == "PASS" and boundary_req == "PASS"
    row_verdict = "PASS" if row_pass else "FAIL"
    row = StubContractRowVerdict(
        scenario_id=scenario_id,
        stub_symbol=stub_symbol,
        boundary_symbol=boundary_symbol,
        shared_contract_source=contract_rel,
        request_parity=request_parity,
        success_return_parity=success_return_parity,
        verdict=row_verdict,
        findings=tuple(findings),
    )

    if row_pass:
        return ScenarioAuditVerdict(
            status="PASS",
            verdict="STUB_CONTRACT_REGRESSION_PASS",
            contract_id=CONTRACT_ID,
            stub_contracts_total=1,
            stub_contracts_pass=1,
            stub_contracts_fail=0,
            rows=(row,),
            scenario_test_pytest_status=pytest_status_str,
            pass_scope="REGRESSION_FIXTURE_ONLY",
        )

    return ScenarioAuditVerdict(
        status="FAIL",
        verdict="STUB_CONTRACT_REGRESSION_FAIL",
        contract_id=CONTRACT_ID,
        stub_contracts_total=1,
        stub_contracts_pass=0,
        stub_contracts_fail=1,
        rows=(row,),
        scenario_test_pytest_status=pytest_status_str,
        pass_scope="REGRESSION_FIXTURE_ONLY",
    )


def verdict_to_dict(verdict: ScenarioAuditVerdict) -> dict[str, Any]:
    """Сериализовать вердикт для CLI и тестов."""

    return {
        "STATUS": verdict.status,
        "VERDICT": verdict.verdict,
        "PASS_SCOPE": verdict.pass_scope,
        "COMPARED_FIELDS": [
            "request.method",
            "request.path_suffix",
            "request.json_body (deep)",
            "response.status",
            "response.json_body (deep)",
        ],
        "NOT_COMPARED_BY_ORACLE": [
            "FAILURE_EMPTY_PARITY",
            "ASYNC_EXCEPTION_PARITY",
            "full change scenario audit",
        ],
        "CONTRACT_ID": verdict.contract_id,
        "STUB_CONTRACTS_TOTAL": verdict.stub_contracts_total,
        "STUB_CONTRACTS_PASS": verdict.stub_contracts_pass,
        "STUB_CONTRACTS_FAIL": verdict.stub_contracts_fail,
        "SCENARIO_TEST_PYTEST_STATUS": verdict.scenario_test_pytest_status,
        "ROWS": [
            {
                "SCENARIO_ID": row.scenario_id,
                "STUB_SYMBOL": row.stub_symbol,
                "BOUNDARY_SYMBOL": row.boundary_symbol,
                "SHARED_CONTRACT_SOURCE": row.shared_contract_source,
                "REQUEST_PARITY": row.request_parity,
                "SUCCESS_RETURN_PARITY": row.success_return_parity,
                "VERDICT": row.verdict,
                "FINDINGS": [
                    {
                        "FIELD": finding.field,
                        "CODE": finding.code,
                        "DETAIL": finding.detail,
                    }
                    for finding in row.findings
                ],
            }
            for row in verdict.rows
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scenario audit stub-contract regression oracle")
    sub = parser.add_subparsers(dest="action", required=True)
    audit = sub.add_parser("audit", help="Evaluate one fixture manifest")
    _ = audit.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="Path to audit_manifest.json under a regression fixture",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: печатает JSON-вердикт и exit 0 только при PASS."""

    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    if args.action != "audit":
        return 2
    try:
        result = evaluate_fixture_manifest(args.manifest.resolve())
    except StubContractAuditError as exc:
        payload = {
            "STATUS": "FAIL",
            "VERDICT": "STUB_CONTRACT_REGRESSION_FAIL",
            "PASS_SCOPE": "REGRESSION_FIXTURE_ONLY",
            "ERROR": exc.code,
        }
        _ = sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return 1
    payload = verdict_to_dict(result)
    _ = sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0 if result.is_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
