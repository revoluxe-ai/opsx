#!/usr/bin/env python3
"""Валидатор ARCH-EVIDENCE.json для ARCHITECTURE_DEPENDENCY_EVIDENCE_V1.

Проверяет структуру receipt, независимость verifier, свежесть digest,
покрытие obligations из design.md и соответствие fixture/lockfile evidence.
Не доказывает корректность live API провайдера — только привязку к
авторитетным фикстурам и digest источников.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

CONTRACT_ID = "ARCHITECTURE_DEPENDENCY_EVIDENCE_V1"
VALIDATOR_IMPLEMENTATION_ID = "architecture-evidence-validator-v1"
SCHEMA_VERSION = 1
DEFAULT_SCHEMA_REL = "opsx/registry/architecture_evidence.schema.json"

OBLIGATION_BLOCK_START = "ARCHITECTURE_COVERAGE_OBLIGATIONS"
INTEGRATION_INVENTORY_START = "ARCHITECTURE_INTEGRATION_INVENTORY"
TECH_ASSESSMENT_REQUIRED = re.compile(
    r"(?m)^TECHNOLOGY_ASSESSMENT\s*\nREQUIRED:\s*yes\b",
    re.IGNORECASE,
)


class ArchitectureEvidenceError(Exception):
    """Ошибка валидации с машинным кодом."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass
class Finding:
    """Одно нарушение evidence gate."""

    code: str
    detail: str
    claim_id: str | None = None


@dataclass
class ValidationResult:
    """Итог validate_receipt."""

    ok: bool
    findings: list[Finding] = field(default_factory=list)

    def add(self, code: str, detail: str = "", claim_id: str | None = None) -> None:
        self.findings.append(Finding(code=code, detail=detail, claim_id=claim_id))
        self.ok = False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _parse_design_block(
    design_text: str,
    block_start: str,
    id_key: str,
) -> list[dict[str, str]]:
    """Извлечь записи из машиночитаемого блока design.md."""

    lines = design_text.splitlines()
    start = -1
    for index, line in enumerate(lines):
        if line.strip() == block_start:
            start = index + 1
            break
    if start < 0:
        return []

    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if current:
                entries.append(current)
                current = {}
            continue
        if stripped.startswith("#") and not stripped.startswith("- "):
            break
        if stripped.startswith(f"- {id_key}:"):
            if current:
                entries.append(current)
            current = {id_key: stripped.split(":", 1)[1].strip()}
        elif stripped.startswith("category:") and current:
            current["category"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("design_anchor:") and current:
            current["design_anchor"] = stripped.split(":", 1)[1].strip()
    if current:
        entries.append(current)
    normalized: list[dict[str, str]] = []
    for item in entries:
        oid = item.get(id_key) or item.get("obligation_id")
        if oid and item.get("category"):
            row = dict(item)
            row["obligation_id"] = oid
            normalized.append(row)
    return normalized


def parse_design_coverage_obligations(design_text: str) -> list[dict[str, str]]:
    """Обязательства из COVERAGE_OBLIGATIONS + INTEGRATION_INVENTORY (дедуп по obligation_id)."""

    merged: dict[str, dict[str, str]] = {}
    for block, key in (
        (OBLIGATION_BLOCK_START, "obligation_id"),
        (INTEGRATION_INVENTORY_START, "operation_id"),
    ):
        for item in _parse_design_block(design_text, block, key):
            merged[item["obligation_id"]] = item
    return list(merged.values())


def technology_assessment_required(design_text: str) -> bool:
    """Признак обязательной technology assessment в design.md."""

    return bool(TECH_ASSESSMENT_REQUIRED.search(design_text))


def compute_inputs_digest(change_root: Path) -> str:
    """Digest upstream planning bytes (owner-contract, proposal, design, specs)."""

    paths: list[Path] = []
    for rel in ("owner-contract.md", "proposal.md", "design.md"):
        candidate = change_root / rel
        if candidate.is_file():
            paths.append(candidate)
    specs = change_root / "specs"
    if specs.is_dir():
        paths.extend(sorted(specs.rglob("*.md")))
    lines: list[str] = []
    for path in sorted(paths, key=lambda p: str(p.relative_to(change_root))):
        rel = path.relative_to(change_root).as_posix()
        lines.append(f"path={rel}\0sha256={sha256_file(path)}\n")
    return sha256_bytes("".join(lines).encode("utf-8"))


def load_schema(repo_root: Path) -> dict[str, Any]:
    path = repo_root / DEFAULT_SCHEMA_REL
    if not path.is_file():
        raise ArchitectureEvidenceError("SCHEMA_MISSING", str(path))
    schema = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise ArchitectureEvidenceError("SCHEMA_INVALID", str(path))
    return schema


def _resolve_schema_node(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Разрешить локальный $ref (#/$defs/...)."""

    ref = schema.get("$ref")
    if not ref:
        return schema
    if ref.startswith("#/$defs/"):
        name = ref.removeprefix("#/$defs/")
        target = root.get("$defs", {}).get(name)
        if isinstance(target, dict):
            return target
    return schema


def _schema_check_value(
    value: Any,
    schema: dict[str, Any],
    path: str,
    root: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if root is None:
        root = schema
    schema = _resolve_schema_node(schema, root)
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: const mismatch")
        return errors
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object")
            return errors
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}).keys())
            for key in value:
                if key not in allowed:
                    errors.append(f"{path}: additional property {key}")
        for key, subschema in schema.get("properties", {}).items():
            if key in value:
                errors.extend(
                    _schema_check_value(value[key], subschema, f"{path}.{key}", root)
                )
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required {req}")
    elif expected_type == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: expected array")
            return errors
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            resolved_items = _resolve_schema_node(item_schema, root)
            for index, item in enumerate(value):
                errors.extend(
                    _schema_check_value(item, resolved_items, f"{path}[{index}]", root)
                )
    elif expected_type == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string")
        elif "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: enum violation")
        elif "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            errors.append(f"{path}: pattern violation")
        elif schema.get("minLength", 0) and len(value) < int(schema["minLength"]):
            errors.append(f"{path}: minLength")
    elif expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{path}: expected integer")
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: expected boolean")
    return errors


def validate_receipt_schema(receipt: dict[str, Any], schema: dict[str, Any]) -> list[Finding]:
    """Минимальная проверка по versioned JSON Schema (без внешней jsonschema dep)."""

    findings: list[Finding] = []
    root_props = {
        k: v
        for k, v in schema.items()
        if k in ("type", "properties", "required", "additionalProperties")
    }
    for message in _schema_check_value(receipt, root_props, "$", schema):
        findings.append(Finding(code="SCHEMA_VIOLATION", detail=message))
    claims_schema = schema.get("properties", {}).get("claims")
    if isinstance(claims_schema, dict):
        item_schema = claims_schema.get("items")
        if isinstance(item_schema, dict):
            resolved_claim = _resolve_schema_node(item_schema, schema)
            for index, claim in enumerate(receipt.get("claims") or []):
                for message in _schema_check_value(
                    claim, resolved_claim, f"$.claims[{index}]", schema
                ):
                    findings.append(
                        Finding(
                            code="SCHEMA_VIOLATION",
                            detail=message,
                            claim_id=claim.get("claim_id") if isinstance(claim, dict) else None,
                        )
                    )
    return findings


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArchitectureEvidenceError("RECEIPT_INVALID_JSON", str(path)) from error
    if not isinstance(payload, dict):
        raise ArchitectureEvidenceError("RECEIPT_NOT_OBJECT", str(path))
    return payload


def _load_fixture_api(repo_root: Path, source_path: str) -> dict[str, Any]:
    path = (repo_root / source_path).resolve()
    if not path.is_file():
        raise ArchitectureEvidenceError("FIXTURE_MISSING", source_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ArchitectureEvidenceError("FIXTURE_INVALID", source_path)
    return data


def _verify_source_bytes(repo_root: Path, source: dict[str, Any], result: ValidationResult) -> bool:
    kind = source.get("kind")
    path_str = source.get("path")
    declared = source.get("content_sha256")
    if kind == "mcp_discovery":
        return True
    if kind == "captured_doc":
        if not source.get("capture_id") or not declared:
            result.add(
                "API_CONTRACT_EVIDENCE_MISSING",
                "captured_doc requires capture_id and content_sha256",
            )
            return False
        return True
    if kind in ("lockfile_pin", "authoritative_doc_digest", "fixture_snapshot"):
        if not path_str or not declared:
            result.add(
                "API_CONTRACT_EVIDENCE_MISSING",
                f"{kind} source missing path or content_sha256",
            )
            return False
    elif not path_str or not declared:
        result.add("API_CONTRACT_EVIDENCE_MISSING", "source missing path or content_sha256")
        return False
    path = (repo_root / path_str).resolve()
    if not path.is_file():
        result.add("API_CONTRACT_EVIDENCE_MISSING", f"missing source file {path_str}")
        return False
    actual = sha256_file(path)
    if actual != declared:
        result.add("SOURCE_DIGEST_MISMATCH", f"{path_str} digest mismatch", None)
        result.add("SELF_ATTESTATION_REJECTED", "fabricated or stale source digest", None)
        return False
    return True


def _check_api_against_fixture(
    claim: dict[str, Any],
    fixture: dict[str, Any],
    result: ValidationResult,
) -> None:
    api = claim.get("api") or {}
    expected = fixture.get("api_contract") or fixture
    method = (api.get("method") or "").upper()
    exp_method = (expected.get("method") or "").upper()
    path = api.get("path") or ""
    exp_path = expected.get("path") or ""
    if method != exp_method or path != exp_path:
        result.add(
            "API_CONTRACT_MISMATCH",
            f"expected {exp_method} {exp_path}, claim has {method} {path}",
            claim.get("claim_id"),
        )
    success = api.get("success_semantics") or {}
    exp_success = expected.get("success_semantics") or {}
    if not success.get("status_codes") and exp_success.get("status_codes"):
        result.add(
            "API_CONTRACT_EVIDENCE_MISSING",
            "missing success_semantics.status_codes",
            claim.get("claim_id"),
        )


def _check_library_against_fixture(
    claim: dict[str, Any],
    fixture: dict[str, Any],
    result: ValidationResult,
) -> None:
    library = claim.get("library") or {}
    pin = fixture.get("lockfile_pin") or {}
    if pin.get("package") and library.get("name") != pin.get("package"):
        result.add("LIBRARY_API_INCOMPATIBLE", "package name mismatch", claim.get("claim_id"))
    doc_version = fixture.get("documented_api_version")
    if doc_version and library.get("version_claim") == doc_version:
        result.add(
            "LIBRARY_API_INCOMPATIBLE",
            "version_claim matches wrong doc fixture",
            claim.get("claim_id"),
        )


def _apply_schema_findings(result: ValidationResult, schema_findings: list[Finding]) -> None:
    for finding in schema_findings:
        result.findings.append(finding)
        result.ok = False


def validate_receipt(
    receipt: dict[str, Any],
    *,
    repo_root: Path,
    design_path: Path | None = None,
    change_root: Path | None = None,
    expected_design_digest: str | None = None,
    expected_inputs_digest: str | None = None,
    expected_change_id: str | None = None,
    schema: dict[str, Any] | None = None,
) -> ValidationResult:
    """Проверить receipt; не изменяет файлы."""

    result = ValidationResult(ok=True)

    if schema is None:
        try:
            schema = load_schema(repo_root)
        except ArchitectureEvidenceError as error:
            result.add(error.code, error.detail)
            return result
    _apply_schema_findings(result, validate_receipt_schema(receipt, schema))

    if expected_change_id and receipt.get("change_id") != expected_change_id:
        result.add("STALE_ARCHITECTURE_EVIDENCE", "change_id mismatch")

    if receipt.get("contract_id") != CONTRACT_ID:
        result.add("RECEIPT_CONTRACT_MISMATCH", str(receipt.get("contract_id")))
    if receipt.get("schema_version") != SCHEMA_VERSION:
        result.add("STALE_ARCHITECTURE_EVIDENCE", "schema_version mismatch")

    exec_id = receipt.get("execution_identity") or {}
    if exec_id.get("validator_implementation_id") != VALIDATOR_IMPLEMENTATION_ID:
        result.add("STALE_ARCHITECTURE_EVIDENCE", "validator_implementation_id mismatch")
    if exec_id.get("schema_version") != SCHEMA_VERSION:
        result.add("STALE_ARCHITECTURE_EVIDENCE", "execution_identity.schema_version mismatch")

    writer = receipt.get("design_writer_id") or ""
    verifier = receipt.get("verifier") or {}
    if verifier.get("id") == writer and writer:
        result.add("SELF_ATTESTATION_REJECTED", "verifier equals design_writer_id")
    if verifier.get("kind") == "INDEPENDENT_TOOL" and verifier.get("id") != VALIDATOR_IMPLEMENTATION_ID:
        result.add(
            "SELF_ATTESTATION_REJECTED",
            "INDEPENDENT_TOOL verifier id must equal validator implementation",
        )

    summary = receipt.get("summary") or {}
    if summary.get("ready_for_design_freeze") and verifier.get("id") == writer:
        result.add("SELF_ATTESTATION_REJECTED", "summary PASS with self verifier")

    design_digest = receipt.get("design_digest") or ""
    if design_path and design_path.is_file():
        current = sha256_file(design_path)
        if design_digest != current:
            result.add("STALE_ARCHITECTURE_EVIDENCE", "design_digest mismatch")
        if expected_design_digest and design_digest != expected_design_digest:
            result.add("STALE_ARCHITECTURE_EVIDENCE", "expected design_digest mismatch")
    elif expected_design_digest and design_digest != expected_design_digest:
        result.add("STALE_ARCHITECTURE_EVIDENCE", "design_digest stale")

    inputs_digest = receipt.get("inputs_digest") or ""
    if change_root and change_root.is_dir():
        computed_inputs = compute_inputs_digest(change_root)
        if inputs_digest != computed_inputs:
            result.add("STALE_ARCHITECTURE_EVIDENCE", "inputs_digest mismatch with change_root")
    if expected_inputs_digest and inputs_digest != expected_inputs_digest:
        result.add("STALE_ARCHITECTURE_EVIDENCE", "inputs_digest stale")

    applicability = receipt.get("applicability")
    if applicability == "NOT_APPLICABLE":
        if not receipt.get("scope_rationale"):
            result.add("API_CONTRACT_EVIDENCE_MISSING", "NOT_APPLICABLE requires scope_rationale")
        if receipt.get("claims"):
            result.add("API_CONTRACT_EVIDENCE_MISSING", "NOT_APPLICABLE must not carry claims")
        _reject_summary_pass_with_findings(result, summary)
        return result

    design_text = ""
    design_obligations: list[dict[str, str]] = []
    if design_path and design_path.is_file():
        design_text = design_path.read_text(encoding="utf-8")
        design_obligations = parse_design_coverage_obligations(design_text)

    if design_path and design_path.is_file() and technology_assessment_required(design_text):
        if not design_obligations:
            result.add(
                "COVERAGE_MANIFEST_MISSING",
                "TECHNOLOGY_ASSESSMENT REQUIRED:yes but no ARCHITECTURE_COVERAGE_OBLIGATIONS "
                "or ARCHITECTURE_INTEGRATION_INVENTORY entries",
            )

    receipt_coverage = receipt.get("coverage_obligations") or []
    if receipt_coverage:
        design_ids = {item["obligation_id"] for item in design_obligations}
        for item in receipt_coverage:
            if not isinstance(item, dict):
                continue
            oid = item.get("obligation_id")
            if oid and oid not in design_ids:
                result.add(
                    "COVERAGE_MANIFEST_MISSING",
                    f"receipt coverage_obligations includes unknown obligation_id {oid}",
                )

    claims = receipt.get("claims") or []
    if not isinstance(claims, list):
        result.add("RECEIPT_INVALID", "claims must be array")
        return result

    claims_by_obligation = {
        str(item.get("obligation_id")): item for item in claims if isinstance(item, dict)
    }

    for obligation in design_obligations:
        oid = obligation["obligation_id"]
        if oid not in claims_by_obligation:
            code = (
                "LIBRARY_VERSION_UNCONFIRMED"
                if obligation.get("category") == "LIBRARY"
                else "API_CONTRACT_EVIDENCE_MISSING"
            )
            result.add(code, f"missing claim for design obligation {oid}")

    policy = receipt.get("design_policy_assertions") or {}
    if policy.get("idempotency_asserted_proven"):
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            api = claim.get("api") or {}
            if api.get("idempotency_posture") == "UNKNOWN":
                result.add(
                    "UNKNOWN_IDEMPOTENCY_ASSERTED_AS_PROVEN",
                    f"claim {claim.get('claim_id')}",
                    claim.get("claim_id"),
                )
    if policy.get("unsafe_auto_retry_after_timeout") and not policy.get(
        "safe_retry_policy_documented"
    ):
        result.add("UNSAFE_RETRY_WITHOUT_POLICY", "design policy flags unsafe retry")

    for claim in claims:
        if not isinstance(claim, dict):
            continue
        cid = claim.get("claim_id")
        sources = claim.get("sources") or []
        if claim.get("result") == "PASS" and not sources:
            result.add("API_CONTRACT_EVIDENCE_MISSING", "PASS claim without sources", cid)

        mcp_only = sources and all(s.get("kind") == "mcp_discovery" for s in sources)
        if claim.get("result") == "PASS" and mcp_only:
            result.add("LIBRARY_VERSION_UNCONFIRMED", "mcp_discovery cannot justify PASS", cid)

        for source in sources:
            if not isinstance(source, dict):
                continue
            if not _verify_source_bytes(repo_root, source, result):
                continue
            if source.get("kind") != "fixture_snapshot" or not source.get("path"):
                continue
            try:
                fixture = _load_fixture_api(repo_root, str(source["path"]))
            except ArchitectureEvidenceError as error:
                result.add(error.code, error.detail, cid)
                continue
            category = claim.get("category")
            if category == "EXTERNAL_API":
                _check_api_against_fixture(claim, fixture, result)
            elif category == "LIBRARY":
                _check_library_against_fixture(claim, fixture, result)
            elif category == "ARCHITECTURE_INVARIANT":
                inv = claim.get("architecture_invariant") or {}
                expected = fixture.get("invariant") or {}
                if expected.get("required") and inv.get("statement") != expected.get("statement"):
                    result.add(
                        "ARCHITECTURE_INVARIANT_VIOLATION",
                        "invariant statement mismatch",
                        cid,
                    )
                if fixture.get("violation_expected"):
                    result.add(
                        "ARCHITECTURE_INVARIANT_VIOLATION",
                        "fixture marks invariant violation",
                        cid,
                    )

    for claim in claims:
        if isinstance(claim, dict) and claim.get("result") == "FAIL":
            if any(summary.get(flag) for flag in ("ready_for_design_freeze", "ready_for_tasks", "ready_for_apply_preflight")):
                result.add(
                    "API_CONTRACT_EVIDENCE_MISSING",
                    "summary ready flags set with FAIL claim",
                    claim.get("claim_id"),
                )

    _reject_summary_pass_with_findings(result, summary)

    return result


def _reject_summary_pass_with_findings(
    result: ValidationResult,
    summary: dict[str, Any],
) -> None:
    """Запретить summary ready при любых findings."""

    if not result.ok:
        for flag in ("ready_for_design_freeze", "ready_for_tasks", "ready_for_apply_preflight"):
            if summary.get(flag):
                result.add(
                    "API_CONTRACT_EVIDENCE_MISSING",
                    f"summary.{flag} true while validation findings exist",
                )


def _fail_cli(code: str, detail: str = "") -> NoReturn:
    print(json.dumps({"ok": False, "code": code, "detail": detail}, ensure_ascii=False))
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate ARCH-EVIDENCE.json")
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--design", type=Path, default=None, help="design.md path")
    parser.add_argument("--expected-design-digest", default=None)
    parser.add_argument("--expected-inputs-digest", default=None)
    parser.add_argument("--expected-change-id", default=None)
    parser.add_argument(
        "--change-root",
        type=Path,
        default=None,
        help="openspec change dir for inputs_digest binding",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    try:
        schema = load_schema(repo_root)
    except ArchitectureEvidenceError as error:
        _fail_cli(error.code, error.detail)

    receipt = load_json(args.receipt.resolve())
    design_path = args.design.resolve() if args.design else None
    change_root = args.change_root.resolve() if args.change_root else None
    outcome = validate_receipt(
        receipt,
        repo_root=repo_root,
        design_path=design_path,
        change_root=change_root,
        expected_design_digest=args.expected_design_digest,
        expected_inputs_digest=args.expected_inputs_digest,
        expected_change_id=args.expected_change_id,
        schema=schema,
    )
    payload = {
        "ok": outcome.ok,
        "contract_id": CONTRACT_ID,
        "validator_implementation_id": VALIDATOR_IMPLEMENTATION_ID,
        "findings": [
            {"code": f.code, "detail": f.detail, "claim_id": f.claim_id}
            for f in outcome.findings
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
