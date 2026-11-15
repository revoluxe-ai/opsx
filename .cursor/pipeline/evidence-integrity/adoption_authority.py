"""S-EV2: проверка adoption audit и authority v2.

Модуль не пишет receipt, ledger и product. Префикс ``owner_attestation_ref``
не доказывает аутентификацию Owner (``owner_authentication`` всегда
``UNPROVEN``). Делегирование D-OWN-6 не реализуется: на кандидата нужен
структурный ``owner_attestation_ref``. S-EV1 detect-only этот модуль не меняет.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from typing import Any, Mapping

CONTRACT_ID = "EVIDENCE_INTEGRITY_ADOPTION_V0"
CHECK_SCHEMA = "opsx.adoption_authority_check"
AUDIT_SCHEMA = "opsx.adoption_audit"
AUTHORITY_SCHEMA = "opsx.current_byte_adoption_authority"
AUTHORIZATION_CLASS = "CURRENT_BYTE_OWNER_ADOPTION"

OWNER_ATTESTATION_PREFIX = re.compile(
    r"^(store:|cursor-stores/|/cursor/stores/|owner-message:)"
)
VACUOUS_ASSERTION = re.compile(r"hasattr|getsource|NotImplementedError", re.IGNORECASE)

SLICE_BEHAVIORAL = "behavioral"
SLICE_STRUCTURAL = "structural_or_config"
WITNESS_BEHAVIORAL = frozenset({"PRE_IMPL_REPLAY", "MUTATION_REVERT"})
WITNESS_STRUCTURAL = WITNESS_BEHAVIORAL | {"NONE"}
BEHAVIORAL_CHECKS = ("AC1", "AC2", "AC3", "AC4", "AC5", "AC6")
WRITER_ROLES = ("red", "implement", "green")
DELEGATION_KEYS = ("delegation_grant", "scoped_grant", "scoped_owner_grant")

INFO_CODES = frozenset(
    {
        "ATTESTATION_CONTENT_UNVERIFIED",
        "ADOPTION_AUTHORITY_LEGACY",
        "OWNER_AUTHENTICATION_UNPROVEN",
    }
)


def candidate_digest(manifest: Mapping[str, Any]) -> str:
    """SHA-256 канонического JSON манифеста кандидата.

    Args:
        manifest: объект манифеста adoption (тесты, product paths, digests).

    Returns:
        Hex SHA-256. Ключи сортируются, пробелы убираются.
    """
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit_receipt_sha256(audit: Mapping[str, Any]) -> str:
    """SHA-256 канонического JSON audit receipt.

    Args:
        audit: объект ``opsx.adoption_audit``.

    Returns:
        Hex SHA-256 того же канонического кодирования, что и ``candidate_digest``.
    """
    payload = json.dumps(audit, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_object(value: Any) -> bool:
    return isinstance(value, dict)


def _legacy_authority(authority: Mapping[str, Any] | None) -> bool:
    """v1 readable: есть digest, нет полей v2."""
    if not _is_object(authority):
        return False
    version = authority.get("schema_version")
    has_v2_marker = (
        (isinstance(version, int) and version >= 2)
        or "audit_receipt_sha256" in authority
        or "owner_attestation_ref" in authority
        or "bindings" in authority
    )
    if has_v2_marker:
        return False
    if authority.get("authorization_class") != AUTHORIZATION_CLASS:
        return False
    digest = authority.get("candidate_digest")
    return isinstance(digest, str) and bool(digest)


def _authority_fields_incomplete(authority: Mapping[str, Any]) -> bool:
    """True, если объект претендует на v2, но без обязательных полей."""
    ref = authority.get("owner_attestation_ref")
    receipt = authority.get("audit_receipt_sha256")
    digest = authority.get("candidate_digest")
    return not (
        isinstance(ref, str)
        and bool(ref)
        and isinstance(receipt, str)
        and bool(receipt)
        and isinstance(digest, str)
        and bool(digest)
        and _is_object(authority.get("bindings"))
    )


def _v2_claim(authority: Mapping[str, Any] | None, implementation: Mapping[str, Any] | None) -> bool:
    if _is_object(authority):
        version = authority.get("schema_version")
        if isinstance(version, int) and version >= 2:
            return True
        if any(key in authority for key in ("audit_receipt_sha256", "owner_attestation_ref", "bindings")):
            return True
    if not _is_object(implementation):
        return False
    if implementation.get("adoption_contract") == CONTRACT_ID:
        return True
    pointer = implementation.get("current_byte_adoption_authority")
    if not _is_object(pointer):
        return False
    pointer_version = pointer.get("schema_version")
    if isinstance(pointer_version, int) and pointer_version >= 2:
        return True
    return "audit_receipt_sha256" in pointer


def _add(findings: list[dict[str, str]], code: str, evidence: str) -> None:
    severity = "INFO" if code in INFO_CODES else "HARD"
    findings.append({"code": code, "severity": severity, "evidence": evidence})


def _has(findings: list[dict[str, str]], code: str) -> bool:
    return any(item["code"] == code for item in findings)


def _delegation_present(document: Mapping[str, Any] | None) -> bool:
    if not _is_object(document):
        return False
    return any(key in document for key in DELEGATION_KEYS)


def _check_ids(audit: Mapping[str, Any], findings: list[dict[str, str]]) -> str | None:
    """Вернуть override статуса, если независимость не доказана."""
    auditor = audit.get("auditor")
    agent_id = auditor.get("agent_id") if _is_object(auditor) else None
    writers = audit.get("writer_ids")
    if not isinstance(agent_id, str) or not agent_id.strip() or not _is_object(writers):
        _add(findings, "AUDIT_INCONCLUSIVE", "auditor or writer ids absent")
        return "AUDIT_INCONCLUSIVE"
    seen: list[str] = []
    for role in WRITER_ROLES:
        value = writers.get(role)
        if not isinstance(value, str) or not value.strip():
            _add(findings, "AUDIT_INCONCLUSIVE", f"writer id absent: {role}")
            return "AUDIT_INCONCLUSIVE"
        seen.append(value.strip())
    if any(item == agent_id.strip() for item in seen):
        _add(findings, "AUDIT_NOT_INDEPENDENT", "auditor agent_id matches a writer id")
        return "AUDIT_NOT_INDEPENDENT"
    return None


def _witness_kind(audit: Mapping[str, Any]) -> str:
    witness = audit.get("causal_witness")
    if not _is_object(witness):
        return ""
    kind = witness.get("kind")
    return kind if isinstance(kind, str) else ""


def _behavior_texts(audit: Mapping[str, Any]) -> list[str]:
    raw = audit.get("behavior_assertions")
    if not isinstance(raw, list):
        return []
    texts: list[str] = []
    for item in raw:
        if not _is_object(item):
            continue
        assertion = item.get("assertion")
        if isinstance(assertion, str):
            texts.append(assertion)
    return texts


def _checks_by_id(audit: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = audit.get("checks")
    found: dict[str, Mapping[str, Any]] = {}
    if not isinstance(raw, list):
        return found
    for item in raw:
        if _is_object(item) and isinstance(item.get("id"), str):
            found[item["id"]] = item
    return found


def _vacuous(audit: Mapping[str, Any]) -> bool:
    for text in _behavior_texts(audit):
        if VACUOUS_ASSERTION.search(text):
            return True
    for item in _checks_by_id(audit).values():
        evidence = item.get("evidence_ref")
        if isinstance(evidence, str) and VACUOUS_ASSERTION.search(evidence):
            return True
    return False


def _slice_and_witness_block(audit: Mapping[str, Any], findings: list[dict[str, str]]) -> bool:
    """True, если slice_class / witness / AC не дают AUDIT_PASS."""
    slice_class = audit.get("slice_class")
    kind = _witness_kind(audit)
    blocked = False
    if slice_class not in (SLICE_BEHAVIORAL, SLICE_STRUCTURAL):
        _add(findings, "AUDIT_FAIL", "slice_class missing or not in contract")
        return True
    allowed = WITNESS_BEHAVIORAL if slice_class == SLICE_BEHAVIORAL else WITNESS_STRUCTURAL
    if kind not in allowed:
        _add(findings, "AUDIT_FAIL", "causal witness is not valid for slice_class")
        blocked = True
    if _vacuous(audit):
        _add(findings, "AUDIT_FAIL", "vacuous assertion cannot support audit pass")
        blocked = True
    checks = _checks_by_id(audit)
    if slice_class == SLICE_BEHAVIORAL:
        for check_id in BEHAVIORAL_CHECKS:
            item = checks.get(check_id)
            if not _is_object(item) or item.get("status") != "PASS":
                _add(findings, "AUDIT_FAIL", f"behavioral check {check_id} is not PASS")
                blocked = True
        if not _behavior_texts(audit):
            _add(findings, "AUDIT_FAIL", "behavioral slice has no behavior assertion")
            blocked = True
    else:
        for item in checks.values():
            if item.get("status") == "FAIL":
                _add(findings, "AUDIT_FAIL", "structural audit contains a failing check")
                blocked = True
                break
    if audit.get("red_status") not in (None, "ALREADY_SATISFIED"):
        _add(findings, "AUDIT_FAIL", "adoption audit must keep RED status ALREADY_SATISFIED")
        blocked = True
    auditor = audit.get("auditor")
    if _is_object(auditor) and auditor.get("scope") not in (None, "READ_ONLY"):
        _add(findings, "AUDIT_FAIL", "auditor scope must be READ_ONLY")
        blocked = True
    return blocked


def _bind_authority(
    authority: Mapping[str, Any],
    *,
    digest: str,
    receipt_sha: str,
    findings: list[dict[str, str]],
) -> bool:
    """Проверить authority v2. True, если объект можно считать выпущенным."""
    ok = True
    if authority.get("schema") != AUTHORITY_SCHEMA or authority.get("schema_version") != 2:
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "authority is not schema_version 2")
        ok = False
    if authority.get("authorization_class") != AUTHORIZATION_CLASS:
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "wrong authorization_class")
        ok = False
    if authority.get("candidate_digest") != digest:
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "authority candidate_digest mismatch")
        ok = False
    if authority.get("audit_receipt_sha256") != receipt_sha:
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "audit_receipt_sha256 mismatch")
        ok = False
    ref = authority.get("owner_attestation_ref")
    if not isinstance(ref, str) or not ref:
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "owner_attestation_ref missing")
        ok = False
    elif OWNER_ATTESTATION_PREFIX.match(ref):
        _add(findings, "ATTESTATION_CONTENT_UNVERIFIED", "prefix matched; content unverified")
        _add(findings, "OWNER_AUTHENTICATION_UNPROVEN", "prefix is not Owner authentication")
    else:
        _add(findings, "ATTESTATION_REF_NOT_OWNER_HELD", "owner_attestation_ref failed prefix")
        _add(findings, "OWNER_AUTHENTICATION_UNPROVEN", "prefix is not Owner authentication")
        ok = False
    bindings = authority.get("bindings")
    if not _is_object(bindings):
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "bindings missing")
        ok = False
    elif (
        bindings.get("candidate_digest") != digest
        or bindings.get("audit_receipt_sha256") != receipt_sha
        or bindings.get("owner_attestation_ref") != ref
    ):
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "bindings do not match authority fields")
        ok = False
    if _delegation_present(authority):
        _add(
            findings,
            "ADOPTION_AUTHORITY_INVALID",
            "per-candidate owner attestation required; delegation is not in S-EV2",
        )
        ok = False
    return ok


def _bind_implementation(
    implementation: Mapping[str, Any],
    *,
    digest: str,
    receipt_sha: str,
    findings: list[dict[str, str]],
) -> bool:
    ok = True
    if implementation.get("status") != "IMPLEMENTATION_ADOPTED":
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "implementation status is not adopted")
        ok = False
    if implementation.get("branch") != "ADOPTION":
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "implementation branch is not ADOPTION")
        ok = False
    if implementation.get("adoption_contract") != CONTRACT_ID:
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "adoption_contract marker missing")
        ok = False
    pointer = implementation.get("current_byte_adoption_authority")
    if not _is_object(pointer):
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "implementation authority pointer missing")
        return False
    if pointer.get("schema_version") != 2 or pointer.get("candidate_digest") != digest:
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "implementation digest binding mismatch")
        ok = False
    if pointer.get("audit_receipt_sha256") != receipt_sha:
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "implementation audit sha mismatch")
        ok = False
    red_identity = implementation.get("red_identity")
    red_status = red_identity.get("status") if _is_object(red_identity) else None
    if red_status != "ALREADY_SATISFIED":
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "RED status must stay ALREADY_SATISFIED")
        ok = False
    if implementation.get("product_files_modified") is not False or implementation.get("tests_modified") is not False:
        _add(findings, "ADOPTION_AUTHORITY_INVALID", "adoption must not record product or test writes")
        ok = False
    return ok


def check_adoption(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Проверить audit, authority v2 и связку IMPLEMENTATION_ADOPTED.

    Args:
        bundle: ``audit``, необязательные ``authority``, ``implementation``,
            ``manifest``, ``audit_file_sha256``, ``red_status``.

    Returns:
        Отчёт ``opsx.adoption_authority_check``. Файлы не создаёт.
        ``authority_emitted`` истинен только когда audit прошёл и authority v2
        связан. ``owner_authentication`` всегда ``UNPROVEN``.
    """
    findings: list[dict[str, str]] = []
    audit = bundle.get("audit") if _is_object(bundle.get("audit")) else None
    authority = bundle.get("authority") if _is_object(bundle.get("authority")) else None
    implementation = bundle.get("implementation") if _is_object(bundle.get("implementation")) else None
    manifest = bundle.get("manifest") if _is_object(bundle.get("manifest")) else None
    red_status = bundle.get("red_status")
    if not isinstance(red_status, str):
        red_status = audit.get("red_status") if audit else "ALREADY_SATISFIED"
    if not isinstance(red_status, str):
        red_status = ""

    if _delegation_present(audit):
        _add(
            findings,
            "ADOPTION_AUTHORITY_INVALID",
            "per-candidate owner attestation required; delegation is not in S-EV2",
        )

    v2 = _v2_claim(authority, implementation)
    legacy = _legacy_authority(authority) and not v2

    digest = ""
    receipt_sha = ""
    witness_kind = ""
    slice_class = ""
    effective = "ABSENT"
    audit_pass = False

    if audit is None:
        if v2 or (
            implementation
            and implementation.get("status") == "IMPLEMENTATION_ADOPTED"
            and implementation.get("adoption_contract") == CONTRACT_ID
        ):
            _add(findings, "ADOPTION_AUDIT_MISSING", "no audit receipt for adoption claim")
        elif (
            implementation
            and implementation.get("status") == "IMPLEMENTATION_ADOPTED"
            and not legacy
            and not v2
        ):
            _add(findings, "ADOPTION_AUDIT_MISSING", "no audit receipt for adoption claim")
        if legacy:
            _add(findings, "ADOPTION_AUTHORITY_LEGACY", "v1 authority is readable and not v2")
        elif authority is not None and (not v2 or _authority_fields_incomplete(authority)):
            _add(findings, "ADOPTION_AUTHORITY_INVALID", "token or unbound authority is not v2")
    else:
        slice_class = audit.get("slice_class") if isinstance(audit.get("slice_class"), str) else ""
        witness_kind = _witness_kind(audit)
        declared = audit.get("status")
        if not isinstance(declared, str):
            declared = ""
        id_override = _check_ids(audit, findings)
        recomputed = candidate_digest(manifest) if manifest is not None else None
        stated = audit.get("candidate_digest")
        if not isinstance(stated, str) or not stated:
            _add(findings, "AUDIT_STALE", "audit candidate_digest missing")
            digest = recomputed or ""
        else:
            digest = stated
            if recomputed is not None and stated != recomputed:
                _add(findings, "AUDIT_STALE", "audit candidate_digest does not match manifest")
            manifest_sha = audit.get("manifest_sha256")
            if isinstance(manifest_sha, str) and recomputed is not None and manifest_sha != recomputed:
                _add(findings, "AUDIT_STALE", "manifest_sha256 does not match manifest")
        file_sha = bundle.get("audit_file_sha256")
        receipt_sha = file_sha if isinstance(file_sha, str) and file_sha else audit_receipt_sha256(audit)
        blocked = _slice_and_witness_block(audit, findings)
        if declared == "AUDIT_INCONCLUSIVE" or id_override == "AUDIT_INCONCLUSIVE":
            effective = "AUDIT_INCONCLUSIVE"
            if declared == "AUDIT_INCONCLUSIVE" and id_override != "AUDIT_INCONCLUSIVE":
                _add(findings, "AUDIT_INCONCLUSIVE", "audit status is AUDIT_INCONCLUSIVE")
        elif id_override == "AUDIT_NOT_INDEPENDENT":
            effective = "AUDIT_NOT_INDEPENDENT"
        elif _has(findings, "AUDIT_STALE"):
            effective = "AUDIT_STALE"
        elif declared == "AUDIT_FAIL" or blocked or _has(findings, "AUDIT_FAIL"):
            effective = "AUDIT_FAIL"
            if declared == "AUDIT_FAIL" and not _has(findings, "AUDIT_FAIL"):
                _add(findings, "AUDIT_FAIL", "audit status is AUDIT_FAIL")
        elif declared == "AUDIT_PASS":
            effective = "AUDIT_PASS"
            audit_pass = True
        else:
            effective = "AUDIT_INCONCLUSIVE"
            _add(findings, "AUDIT_INCONCLUSIVE", "audit status is not a closed pass or fail")

    if audit_pass and _has(findings, "ADOPTION_AUTHORITY_INVALID"):
        audit_pass = False
        effective = "AUDIT_FAIL"

    authority_ok = False
    if audit_pass and authority is not None:
        authority_ok = _bind_authority(authority, digest=digest, receipt_sha=receipt_sha, findings=findings)
    elif audit_pass and authority is None:
        ref = bundle.get("owner_attestation_ref")
        if isinstance(ref, str) and OWNER_ATTESTATION_PREFIX.match(ref):
            _add(findings, "ATTESTATION_CONTENT_UNVERIFIED", "prefix matched; content unverified")
            _add(findings, "OWNER_AUTHENTICATION_UNPROVEN", "prefix is not Owner authentication")
        elif isinstance(ref, str) and ref:
            _add(findings, "ATTESTATION_REF_NOT_OWNER_HELD", "owner_attestation_ref failed prefix")
            _add(findings, "OWNER_AUTHENTICATION_UNPROVEN", "prefix is not Owner authentication")
            audit_pass = False

    implementation_ok = False
    if audit_pass and authority_ok and implementation is not None:
        implementation_ok = _bind_implementation(
            implementation,
            digest=digest,
            receipt_sha=receipt_sha,
            findings=findings,
        )

    hard = [item for item in findings if item["severity"] == "HARD"]
    authority_emitted = bool(audit_pass and authority_ok and not any(
        item["code"] in {
            "ADOPTION_AUTHORITY_INVALID",
            "ATTESTATION_REF_NOT_OWNER_HELD",
            "ADOPTION_AUDIT_MISSING",
            "AUDIT_NOT_INDEPENDENT",
            "AUDIT_STALE",
            "AUDIT_FAIL",
            "AUDIT_INCONCLUSIVE",
        }
        for item in hard
    ))
    if not authority_ok:
        authority_emitted = False
    authority_permitted = bool(
        audit_pass
        and not _has(findings, "ATTESTATION_REF_NOT_OWNER_HELD")
        and not _has(findings, "ADOPTION_AUTHORITY_INVALID")
        and (
            authority_emitted
            or (
                authority is None
                and isinstance(bundle.get("owner_attestation_ref"), str)
                and bool(OWNER_ATTESTATION_PREFIX.match(str(bundle.get("owner_attestation_ref"))))
            )
        )
    )
    authorized = bool(authority_emitted and implementation is not None and implementation_ok)

    return {
        "schema": CHECK_SCHEMA,
        "schema_version": 1,
        "contract_id": CONTRACT_ID,
        "live_authority": "NO",
        "platform_attestation": "UNPROVEN",
        "owner_authentication": "UNPROVEN",
        "gating": "ADOPTION_AUTHORITY_CHECK",
        "effective_audit_status": effective,
        "authority_permitted": authority_permitted,
        "authority_emitted": authority_emitted,
        "implementation_adopted_authorized": authorized,
        "candidate_digest": digest,
        "audit_receipt_sha256": receipt_sha,
        "red_status": red_status if isinstance(red_status, str) else "",
        "causal_witness_kind": witness_kind,
        "slice_class": slice_class,
        "findings": findings,
        "writes": [],
    }


def main(argv: list[str] | None = None) -> int:
    """Прочитать JSON bundle из файла или stdin и напечатать один отчёт.

    Args:
        argv: аргументы CLI без ``argv[0]``. ``--input`` — путь к JSON.

    Returns:
        0, если отчёт напечатан. 2, если вход не разобран.
    """
    parser = argparse.ArgumentParser(description="S-EV2 adoption authority checker")
    parser.add_argument("--input", dest="input_path", default="")
    args = parser.parse_args(argv)
    raw = ""
    if args.input_path:
        with open(args.input_path, encoding="utf-8") as handle:
            raw = handle.read()
    else:
        raw = sys.stdin.read()
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError:
        sys.stdout.write(json.dumps({"schema": CHECK_SCHEMA, "error": "invalid_json"}) + "\n")
        return 2
    if not _is_object(bundle):
        sys.stdout.write(json.dumps({"schema": CHECK_SCHEMA, "error": "bundle_not_object"}) + "\n")
        return 2
    report = check_adoption(bundle)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
