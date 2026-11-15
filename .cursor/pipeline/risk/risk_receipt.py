"""Проверка входов и атомарная публикация receipts команды ``/opsx-risk``.

Helper не выбирает профиль риска и не проектирует проверки. Он fail-closed
проверяет identity обязательных planning/tasking входов, связывает receipt с
текущими байтами artifacts и пишет только receipt выбранного режима.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, cast


CONTRACT_ID = "OPSX-WB-RISK-01"
CHANGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PROFILES = {"LOW", "STANDARD", "HIGH"}
PLANNING_FIELDS = (
    "risk_triggers",
    "planning_lenses",
    "owner_attention_requirements",
    "unresolved_risk_questions",
    "escalation_conditions",
    "evidence",
)
TASKING_FIELDS = (
    "risk_triggers",
    "tasking_lenses",
    "implementation_boundaries",
    "sequencing_constraints",
    "task_split_constraints",
    "must_not_combine",
    "prerequisite_constraints",
    "owner_attention_requirements",
    "unresolved_implementation_questions",
    "escalation_conditions",
    "evidence",
)
TASKING_FIREWALL = (
    re.compile(r"\b(?:npm|poetry)\s+run\b", re.IGNORECASE),
    re.compile(r"\bpytest\b", re.IGNORECASE),
    re.compile(
        r"/opsx-(?:test|browser-check|security-review|verify|plan-verification)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\btests?\s+must\s+pass\b", re.IGNORECASE),
    re.compile(r"\brun\s+(?:the\s+)?(?:tests?|build|lint|typecheck)\b", re.IGNORECASE),
)


class RiskReceiptError(ValueError):
    """Raised when evidence is missing, stale, unsafe, or malformed."""


def _fail(reason: str) -> NoReturn:
    raise RiskReceiptError(reason)


def sha256_bytes(value: bytes) -> str:
    """Return lowercase SHA256 for exact bytes."""

    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    """Return lowercase SHA256 for exact bytes of ``path``."""

    return sha256_bytes(path.read_bytes())


def _lf_hash(path: Path) -> str:
    normalized = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return sha256_bytes(normalized)


def _pin_matches(path: Path, pinned_hex: str) -> bool:
    """Сверка pin с файлом (LF/CRLF/канонический текст), как у ``CheckWorkflowReceipt``."""
    try:
        import sys

        runtime = Path(__file__).resolve().parents[3] / "opsx" / "opsx_runtime"
        root_s = str(runtime)
        if root_s not in sys.path:
            sys.path.insert(0, root_s)
        from check_workflow_receipt import CheckWorkflowReceipt

        return CheckWorkflowReceipt.matches_file(path, pinned_hex)
    except (ImportError, OSError, ValueError):
        return _lf_hash(path) == pinned_hex


def _header(body: str, name: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(name)}:\s*(.+)$", body)
    return match.group(1).strip() if match is not None else None


def _require_file(path: Path, reason: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        _fail(reason)
    return resolved


def _within(root: Path, candidate: Path, reason: str) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        _fail(reason)
    return resolved_candidate


def _classification(path: Path, mode: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RiskReceiptError("CLASSIFICATION_INVALID") from error
    if not isinstance(raw, dict):
        _fail("CLASSIFICATION_INVALID")
    value = cast(dict[str, Any], raw)
    if value.get("risk_profile") not in PROFILES:
        _fail("RISK_PROFILE_INVALID")
    rationale = value.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        _fail("INSUFFICIENT_EVIDENCE")
    fields = PLANNING_FIELDS if mode == "planning" else TASKING_FIELDS
    for field in fields:
        raw_items = value.get(field)
        if not isinstance(raw_items, list) or not raw_items:
            _fail(f"CLASSIFICATION_FIELD_INVALID:{field}")
        items = cast(list[object], raw_items)
        if any(not isinstance(item, str) or not item.strip() for item in items):
            _fail(f"CLASSIFICATION_FIELD_INVALID:{field}")
    evidence = cast(list[str], value["evidence"])
    if evidence == ["none"]:
        _fail("INSUFFICIENT_EVIDENCE")
    if mode == "tasking":
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if any(pattern.search(serialized) for pattern in TASKING_FIREWALL):
            _fail("TASKING_OUTPUT_FIREWALL")
    return value


def _list_lines(title: str, values: list[str]) -> list[str]:
    return [f"{title}:", *(f"- {value}" for value in values), ""]


def _atomic_replace(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            _ = stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _relative(change_root: Path, path: Path) -> str:
    return path.resolve().relative_to(change_root.resolve()).as_posix()


def _validate_ocr(
    *,
    change_id: str,
    owner_contract: Path,
    review_state: str | None,
    review_change_id: str | None,
    reviewed_artifact: Path | None,
    evidence: str | None,
) -> str:
    if review_state != "PASS" or review_change_id != change_id:
        _fail("OWNER_CONTRACT_REVIEW_NOT_PASS")
    if reviewed_artifact is None:
        _fail("OWNER_CONTRACT_REVIEW_NOT_PASS")
    if reviewed_artifact.resolve() != owner_contract.resolve():
        _fail("OWNER_CONTRACT_REVIEW_STALE")
    if evidence != "same-turn-pasted-pass":
        _fail("OWNER_CONTRACT_REVIEW_NOT_CURRENT")
    return "same-turn pasted target OCR PASS"


def _planning_receipt(
    *,
    change_id: str,
    change_root: Path,
    owner_contract: Path,
    source_ocr: str,
    classification: dict[str, Any],
) -> str:
    lines = [
        "# Planning risk",
        "",
        "COMMAND: /opsx-risk",
        "MODE: planning",
        f"CHANGE_ID: {change_id}",
        f"CHANGE_ROOT: {change_root}",
        f"CREATED_AT: {datetime.now(UTC).isoformat()}",
        f"CONTRACT_ID: {CONTRACT_ID}",
        "",
        "STATUS: RISK_CLASSIFIED",
        f"RISK_PROFILE: {classification['risk_profile']}",
        "",
        f"SOURCE_OCR_PASS_RECEIPT: {source_ocr}",
        f"OCR_REVIEWED_ARTIFACT: {owner_contract}",
        f"SOURCE_OWNER_CONTRACT_SHA256: {sha256_file(owner_contract)}",
        "",
        "OCR_EVIDENCE:",
        f"- REVIEW_STATE: PASS for CHANGE_ID: {change_id}",
        "- Target OCR evidence was supplied in the same command turn.",
        "",
    ]
    for field, title in (
        ("risk_triggers", "RISK_TRIGGERS"),
        ("planning_lenses", "PLANNING_LENSES"),
        ("owner_attention_requirements", "OWNER_ATTENTION_REQUIREMENTS"),
        ("unresolved_risk_questions", "UNRESOLVED_RISK_QUESTIONS"),
        ("escalation_conditions", "ESCALATION_CONDITIONS"),
    ):
        lines.extend(_list_lines(title, cast(list[str], classification[field])))
    lines.extend(
        [
            "RATIONALE:",
            cast(str, classification["rationale"]),
            "",
            *_list_lines("EVIDENCE", cast(list[str], classification["evidence"])),
            "NON_CLAIMS:",
            "- This profile does not select tests or verification commands.",
            "- This profile does not certify GREEN, apply, verify, or archive readiness.",
            "- This receipt does not write planning artifacts or expand Owner semantics.",
            "- This profile is independent from the tasking RISK_PROFILE.",
            "",
            "CONSUMER_INTEGRATION: PARTIAL",
            "MISSING_CONSUMERS: /opsx-continue, /opsx-ff, /opsx-update, "
            "/opsx-planning-review",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_planning_risk(
    planning_risk: Path,
    *,
    change_id: str,
    owner_hash: str,
) -> str:
    body = planning_risk.read_text(encoding="utf-8")
    if (
        _header(body, "MODE") != "planning"
        or _header(body, "STATUS") != "RISK_CLASSIFIED"
        or _header(body, "CHANGE_ID") != change_id
    ):
        _fail("PLANNING_RISK_NOT_CLASSIFIED")
    if _header(body, "SOURCE_OWNER_CONTRACT_SHA256") != owner_hash:
        _fail("PLANNING_RISK_PROVENANCE_STALE")
    return sha256_file(planning_risk)


def _resolve_receipt_input(
    change_root: Path, value: object, *, reason: str
) -> tuple[Path, str]:
    if not isinstance(value, dict):
        _fail(reason)
    record = cast(dict[str, object], value)
    raw_path = record.get("path")
    content_hash = record.get("content_hash")
    if not isinstance(raw_path, str) or not isinstance(content_hash, str):
        _fail(reason)
    path = Path(raw_path)
    if not path.is_absolute():
        path = change_root / path
    resolved = _within(change_root, path, reason)
    resolved = _require_file(resolved, reason)
    return resolved, content_hash


def _validate_scenario_pair(
    change_root: Path, change_id: str
) -> tuple[Path, Path]:
    plan = _require_file(
        change_root / "scenario-coverage" / "plan.md",
        "SCENARIO_PLAN_EVIDENCE_MISSING",
    )
    receipt_path = _require_file(
        change_root / "scenario-coverage" / "plan.receipt.json",
        "SCENARIO_PLAN_EVIDENCE_MISSING",
    )
    try:
        loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RiskReceiptError("SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL") from error
    if not isinstance(loaded, dict):
        _fail("SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL")
    receipt = cast(dict[str, object], loaded)
    expected = {
        "SCHEMA_VERSION": 1,
        "SCENARIO_COVERAGE_RECEIPT": True,
        "CHANGE_ID": change_id,
        "MODE": "plan",
        "PLAN_STATUS": "ready",
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        _fail("SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL")
    if receipt.get("MATERIALIZATION_STATUS") not in {"COMMITTED", "ALREADY_CURRENT"}:
        _fail("SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL")
    if receipt.get("PLAN_CONTENT_HASH") != _lf_hash(plan):
        _fail("SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL")
    inputs = receipt.get("INPUTS")
    if not isinstance(inputs, dict):
        _fail("SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL")
    typed_inputs = cast(dict[str, object], inputs)
    design_path, design_hash = _resolve_receipt_input(
        change_root,
        typed_inputs.get("DESIGN_REVISION"),
        reason="SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL",
    )
    if not _pin_matches(design_path, design_hash):
        _fail("SCENARIO_PLAN_EVIDENCE_STALE")
    spec_revisions = typed_inputs.get("SPEC_REVISION")
    if not isinstance(spec_revisions, list):
        _fail("SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL")
    receipt_specs: dict[str, str] = {}
    for revision in cast(list[object], spec_revisions):
        spec_path, spec_hash = _resolve_receipt_input(
            change_root,
            revision,
            reason="SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL",
        )
        receipt_specs[_relative(change_root, spec_path)] = spec_hash
    current_specs = {
        _relative(change_root, path): _lf_hash(path)
        for path in sorted((change_root / "specs").glob("**/spec.md"))
    }
    if set(receipt_specs) != set(current_specs):
        _fail("SCENARIO_PLAN_EVIDENCE_STALE")
    if any(
        not _pin_matches(change_root / path, receipt_specs[path])
        for path in receipt_specs
    ):
        _fail("SCENARIO_PLAN_EVIDENCE_STALE")
    owner_revision = typed_inputs.get("OWNER_CONTRACT_REVISION")
    if owner_revision is not None:
        owner_path, owner_revision_hash = _resolve_receipt_input(
            change_root,
            owner_revision,
            reason="SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL",
        )
        if not _pin_matches(owner_path, owner_revision_hash):
            _fail("SCENARIO_PLAN_EVIDENCE_STALE")
    return plan, receipt_path


def _solution_inputs(
    change_root: Path,
    *,
    scenario_evidence: str,
    change_id: str,
) -> list[Path]:
    design = _require_file(change_root / "design.md", "CHOSEN_SOLUTION_MISSING")
    specs = sorted((change_root / "specs").glob("**/spec.md"))
    if not specs:
        _fail("CHOSEN_SOLUTION_MISSING")
    paths: list[Path] = []
    proposal = change_root / "proposal.md"
    if proposal.is_file():
        paths.append(proposal.resolve())
    paths.extend([design, *(path.resolve() for path in specs)])
    decisions_root = change_root / "decisions"
    if decisions_root.is_dir():
        paths.extend(path.resolve() for path in sorted(decisions_root.glob("**/*.md")))
    if scenario_evidence == "required":
        paths.extend(_validate_scenario_pair(change_root, change_id))
    elif scenario_evidence != "not-required":
        _fail("SCENARIO_REQUIREMENT_DECISION_REQUIRED")
    return paths


def _tasking_receipt(
    *,
    change_id: str,
    change_root: Path,
    owner_hash: str,
    planning_risk: Path,
    planning_hash: str,
    inputs: list[Path],
    scenario_evidence: str,
    classification: dict[str, Any],
) -> str:
    lines = [
        "# Tasking risk",
        "",
        "COMMAND: /opsx-risk",
        "MODE: tasking",
        f"CHANGE_ID: {change_id}",
        f"CHANGE_ROOT: {change_root}",
        f"CREATED_AT: {datetime.now(UTC).isoformat()}",
        f"CONTRACT_ID: {CONTRACT_ID}",
        "",
        "STATUS: RISK_CLASSIFIED",
        f"RISK_PROFILE: {classification['risk_profile']}",
        "",
        f"SOURCE_OWNER_CONTRACT_SHA256: {owner_hash}",
        f"SOURCE_PLANNING_RISK_RECEIPT: {planning_risk}",
        f"SOURCE_PLANNING_RISK_SHA256: {planning_hash}",
        f"SCENARIO_EVIDENCE_REQUIREMENT: {scenario_evidence}",
        "",
        "SOURCE_PLANNING_INPUTS:",
    ]
    for path in inputs:
        lines.extend(
            [
                f"- path: {_relative(change_root, path)}",
                f"  sha256: {sha256_file(path)}",
            ]
        )
    lines.append("")
    for field, title in (
        ("risk_triggers", "RISK_TRIGGERS"),
        ("tasking_lenses", "TASKING_LENSES"),
        ("implementation_boundaries", "IMPLEMENTATION_BOUNDARIES"),
        ("sequencing_constraints", "SEQUENCING_CONSTRAINTS"),
        ("task_split_constraints", "TASK_SPLIT_CONSTRAINTS"),
        ("must_not_combine", "MUST_NOT_COMBINE"),
        ("prerequisite_constraints", "PREREQUISITE_CONSTRAINTS"),
        ("owner_attention_requirements", "OWNER_ATTENTION_REQUIREMENTS"),
        (
            "unresolved_implementation_questions",
            "UNRESOLVED_IMPLEMENTATION_QUESTIONS",
        ),
        ("escalation_conditions", "ESCALATION_CONDITIONS"),
    ):
        lines.extend(_list_lines(title, cast(list[str], classification[field])))
    lines.extend(
        [
            "RATIONALE:",
            cast(str, classification["rationale"]),
            "",
            *_list_lines("EVIDENCE", cast(list[str], classification["evidence"])),
            "NON_CLAIMS:",
            "- This profile does not select tests or verification commands.",
            "- This profile does not write tasks.md or choose the task list.",
            "- This profile does not certify GREEN, apply, verify, or archive readiness.",
            "- This receipt does not expand Owner semantics.",
            "- This profile was classified independently from planning risk.",
            "- TASKING OUTPUT FIREWALL forbids test/build/check execution prescriptions.",
            "",
            "CONSUMER_INTEGRATION: PARTIAL",
            "MISSING_CONSUMERS: /opsx-continue, /opsx-ff, /opsx-update, "
            "/opsx-planning-review",
            "",
        ]
    )
    return "\n".join(lines)


def publish_receipt(args: argparse.Namespace) -> tuple[Path, str]:
    """Validate one mode and atomically publish only its Change-local receipt.

    Returns:
        Receipt path and the selected profile.

    Raises:
        RiskReceiptError: If identity, evidence, classification, or boundaries
            are missing, stale, conflicting, or unsafe.
    """

    change_id = cast(str, args.change_id)
    if CHANGE_ID_PATTERN.fullmatch(change_id) is None:
        _fail("INVALID_CHANGE_ID")
    change_root = cast(Path, args.change_root).resolve()
    if not change_root.is_dir():
        _fail("INVALID_CHANGE_ROOT")
    owner_contract = _require_file(
        change_root / "owner-contract.md", "OWNER_CONTRACT_MISSING"
    )
    classification = _classification(cast(Path, args.classification), args.mode)
    receipt_root = (change_root / "opsx").resolve()
    if args.mode == "planning":
        source_ocr = _validate_ocr(
            change_id=change_id,
            owner_contract=owner_contract,
            review_state=args.ocr_review_state,
            review_change_id=args.ocr_review_change_id,
            reviewed_artifact=args.ocr_reviewed_artifact,
            evidence=args.ocr_evidence,
        )
        receipt = receipt_root / "planning-risk.md"
        body = _planning_receipt(
            change_id=change_id,
            change_root=change_root,
            owner_contract=owner_contract,
            source_ocr=source_ocr,
            classification=classification,
        )
    else:
        planning_risk = _require_file(
            receipt_root / "planning-risk.md", "PLANNING_RISK_RECEIPT_MISSING"
        )
        owner_hash = sha256_file(owner_contract)
        planning_hash = _validate_planning_risk(
            planning_risk, change_id=change_id, owner_hash=owner_hash
        )
        if args.planning_escalation_status == "fired":
            _fail("PLANNING_RISK_ESCALATION_FIRED")
        if args.planning_escalation_status != "clear":
            _fail("PLANNING_RISK_ESCALATION_STATUS_REQUIRED")
        inputs = _solution_inputs(
            change_root,
            scenario_evidence=args.scenario_evidence,
            change_id=change_id,
        )
        receipt = receipt_root / "tasking-risk.md"
        body = _tasking_receipt(
            change_id=change_id,
            change_root=change_root,
            owner_hash=owner_hash,
            planning_risk=planning_risk,
            planning_hash=planning_hash,
            inputs=inputs,
            scenario_evidence=args.scenario_evidence,
            classification=classification,
        )
    receipt = _within(receipt_root, receipt, "RECEIPT_PATH_ESCAPE")
    _atomic_replace(receipt, body)
    return receipt, cast(str, classification["risk_profile"])


def build_parser() -> argparse.ArgumentParser:
    """Build the fail-closed two-mode helper CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("mode", choices=("planning", "tasking"))
    _ = parser.add_argument("--change-id", required=True)
    _ = parser.add_argument("--change-root", type=Path, required=True)
    _ = parser.add_argument("--classification", type=Path, required=True)
    _ = parser.add_argument("--ocr-review-state")
    _ = parser.add_argument("--ocr-review-change-id")
    _ = parser.add_argument("--ocr-reviewed-artifact", type=Path)
    _ = parser.add_argument("--ocr-evidence")
    _ = parser.add_argument(
        "--planning-escalation-status", choices=("clear", "fired")
    )
    _ = parser.add_argument(
        "--scenario-evidence", choices=("required", "not-required")
    )
    return parser


def main() -> int:
    """Run evidence validation and publish one hash-bound risk receipt."""

    args = build_parser().parse_args()
    if args.mode == "planning" and (
        args.planning_escalation_status is not None
        or args.scenario_evidence is not None
    ):
        _fail("MODE_ARGUMENT_CONFLICT")
    if args.mode == "tasking" and any(
        value is not None
        for value in (
            args.ocr_review_state,
            args.ocr_review_change_id,
            args.ocr_reviewed_artifact,
            args.ocr_evidence,
        )
    ):
        _fail("MODE_ARGUMENT_CONFLICT")
    receipt, profile = publish_receipt(args)
    print(
        json.dumps(
            {
                "STATUS": "RISK_CLASSIFIED",
                "MODE": args.mode,
                "CHANGE_ID": args.change_id,
                "RISK_PROFILE": profile,
                "RECEIPT_PATH": str(receipt),
                "RECEIPT_WRITTEN": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RiskReceiptError as error:
        raise SystemExit(f"RISK_RECEIPT_ERROR: {error}") from error
