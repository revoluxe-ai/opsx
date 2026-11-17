#!/usr/bin/env python3
"""S2 planning gates: design-ready/freeze и tasks-ready для continue/ff/update."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

_GATE_DIR = Path(__file__).resolve().parent
if str(_GATE_DIR) not in sys.path:
    sys.path.insert(0, str(_GATE_DIR))

from discovery import discover_for_change, discovery_gaps  # noqa: E402
from doc_acquisition import build_integrated_document_fetcher  # noqa: E402
from doc_capture import validate_captures_for_receipt  # noqa: E402
from not_applicable_guard import validate_not_applicable_independent  # noqa: E402
from trust_provenance import validate_s3_trust_for_receipt  # noqa: E402
from doc_verification import (  # noqa: E402
    DocumentFetcher,
    verify_integrated_documentation_evidence,
)

GATE_IMPLEMENTATION_ID = "architecture-evidence-planning-gate-v1.4.0"
_SCHEMA_REL = Path("opsx/registry/architecture_evidence.schema.json")

_DESIGN_READY = re.compile(r"(?m)^DESIGN_STATUS:\s*READY\b")
_DESIGN_BLOCKED = re.compile(r"(?m)^DESIGN_STATUS:\s*BLOCKED\b")
_TASKS_READY = re.compile(r"(?m)^TASKS_STATUS:\s*READY\b")
_TA_REQUIRED = re.compile(r"(?m)^TECHNOLOGY_ASSESSMENT\s*\nREQUIRED:\s*yes\b", re.I)


@dataclass
class GateFinding:
    code: str
    detail: str


@dataclass
class GateResult:
    ok: bool
    phase: str
    change_id: str
    trust_boundary: dict[str, str] = field(default_factory=dict)
    findings: list[GateFinding] = field(default_factory=list)
    discovery_limitations: list[str] = field(default_factory=list)

    def add(self, code: str, detail: str) -> None:
        self.findings.append(GateFinding(code=code, detail=detail))
        self.ok = False


def _schema_repo(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / _SCHEMA_REL).is_file():
            return candidate
    return start


def _load_oracle() -> ModuleType:
    path = Path(__file__).resolve().parent / "architecture_evidence.py"
    spec = importlib.util.spec_from_file_location("architecture_evidence", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _change_root(repo_root: Path, change_id: str) -> Path:
    return repo_root / "openspec" / "changes" / change_id


def _normalize_change_id(change_id: str, result: GateResult) -> str | None:
    cleaned = (change_id or "").strip()
    if not cleaned:
        result.add("CHANGE_ID_REQUIRED", "change-scoped gate requires non-empty change id")
        return None
    return cleaned


def _design_is_ready(design_text: str) -> bool:
    return bool(_DESIGN_READY.search(design_text))


def _design_is_blocked(design_text: str) -> bool:
    return bool(_DESIGN_BLOCKED.search(design_text))


def _tasks_is_ready(tasks_text: str) -> bool:
    return bool(_TASKS_READY.search(tasks_text))


def _trust_stages(
    *,
    discovered: bool,
    source_inspected: bool,
    compatibility: bool,
    evidence_current: bool,
    gate_accepted: bool,
) -> dict[str, str]:
    return {
        "DISCOVERED": "yes" if discovered else "no",
        "SOURCE_INSPECTED": "yes" if source_inspected else "no",
        "COMPATIBILITY_VERIFIED": "yes" if compatibility else "no",
        "EVIDENCE_CURRENT": "yes" if evidence_current else "no",
        "GATE_ACCEPTED": "yes" if gate_accepted else "no",
    }


def _check_trusted_review_s2(receipt: dict[str, Any], result: GateResult) -> None:
    verifier = receipt.get("verifier") or {}
    summary = receipt.get("summary") or {}
    if verifier.get("kind") != "INDEPENDENT_REVIEW_AGENT":
        return
    if any(summary.get(k) for k in ("ready_for_design_freeze", "ready_for_tasks", "ready_for_apply_preflight")):
        result.add(
            "TRUSTED_REVIEW_NOT_ENFORCED_S2",
            "INDEPENDENT_REVIEW_AGENT cannot establish GATE_ACCEPTED in S2; requires S3",
        )


def _validate_receipt_mandatory(
    oracle: ModuleType,
    receipt: dict[str, Any],
    *,
    repo_root: Path,
    change_root: Path,
    change_id: str,
    design_path: Path,
    fetcher: DocumentFetcher,
    result: GateResult,
    s3_strict: bool = False,
    trust_policy: str = "production",
) -> None:
    schema_root = _schema_repo(repo_root)
    schema = oracle.load_schema(schema_root)
    validation = oracle.validate_receipt(
        receipt,
        repo_root=repo_root,
        design_path=design_path,
        change_root=change_root,
        expected_change_id=change_id,
        expected_design_digest=oracle.sha256_file(design_path),
        expected_inputs_digest=oracle.compute_inputs_digest(change_root),
        schema=schema,
    )
    if not validation.ok:
        for finding in validation.findings:
            result.add(finding.code, finding.detail)
    for doc_f in verify_integrated_documentation_evidence(
        receipt, repo_root=repo_root, fetcher=fetcher
    ):
        result.add(doc_f.code, doc_f.detail)
    inputs_digest = oracle.compute_inputs_digest(change_root)
    for cap_f in validate_captures_for_receipt(
        receipt,
        change_root=change_root,
        expected_change_id=change_id,
        expected_inputs_digest=inputs_digest,
    ):
        detail = cap_f.detail
        if getattr(cap_f, "claim_id", None):
            detail = f"{detail} (claim {cap_f.claim_id})"
        result.add(cap_f.code, detail)
    _check_trusted_review_s2(receipt, result)
    design_digest = oracle.sha256_file(design_path)
    for trust_f in validate_s3_trust_for_receipt(
        receipt,
        repo_root=repo_root,
        change_root=change_root,
        expected_change_id=change_id,
        expected_inputs_digest=inputs_digest,
        design_digest=design_digest,
        s3_strict=s3_strict,
        trust_policy=trust_policy,  # type: ignore[arg-type]
    ):
        detail = trust_f.detail
        if trust_f.claim_id:
            detail = f"{detail} (claim {trust_f.claim_id})"
        result.add(trust_f.code, detail)
    if s3_strict:
        design_text = design_path.read_text(encoding="utf-8")
        design_obligations = oracle.parse_design_coverage_obligations(design_text)
        for na_f in validate_not_applicable_independent(
            receipt,
            change_root=change_root,
            repo_root=repo_root,
            design_text=design_text,
            design_obligations=design_obligations,
        ):
            result.add(na_f.code, na_f.detail)
        summary = receipt.get("summary") or {}
        if summary.get("ready_for_apply_preflight") and not result.ok:
            result.add(
                "APPLY_PREFLIGHT_BLOCKED",
                "ready_for_apply_preflight incompatible with trust findings",
            )


def _require_architecture_stage(result: GateResult, repo_root: Path, change_id: str) -> None:
    """Enforce mandatory reviewed architecture before READY or implementation."""
    path = Path(__file__).resolve().parents[3] / "tools" / "openspec" / "architecture_stage_gate.py"
    if not path.is_file():
        result.add("ARCHITECTURE_STAGE_BLOCKED", "mandatory Architecture gate implementation missing")
        return
    spec = importlib.util.spec_from_file_location("mandatory_opsx_architecture_stage", path)
    if spec is None or spec.loader is None:
        result.add("ARCHITECTURE_STAGE_BLOCKED", "cannot load mandatory Architecture gate")
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        outcome = module.inspect(repo_root, change_id)
    except (ValueError, OSError, UnicodeError) as exc:
        result.add("ARCHITECTURE_STAGE_BLOCKED", str(exc))
        return
    for finding in outcome["findings"]:
        result.add("ARCHITECTURE_STAGE_BLOCKED", finding)


def run_design_ready_gate(
    change_id: str,
    repo_root: Path,
    *,
    fetcher: DocumentFetcher | None = None,
) -> GateResult:
    """Gate на promotion `DESIGN_STATUS: READY` (draft BLOCKED/incomplete пропускается)."""

    result = GateResult(ok=True, phase="design-ready", change_id=change_id)
    normalized = _normalize_change_id(change_id, result)
    if normalized is None:
        return result
    result.change_id = normalized

    change_root = _change_root(repo_root, normalized)
    integrated_fetcher = fetcher or build_integrated_document_fetcher(
        _schema_repo(repo_root), change_root=change_root
    )
    design_path = change_root / "design.md"
    if not design_path.is_file():
        result.trust_boundary = _trust_stages(
            discovered=False,
            source_inspected=False,
            compatibility=False,
            evidence_current=False,
            gate_accepted=True,
        )
        return result

    design_text = design_path.read_text(encoding="utf-8")
    if _design_is_blocked(design_text) or not _design_is_ready(design_text):
        result.trust_boundary = _trust_stages(
            discovered=False,
            source_inspected=False,
            compatibility=False,
            evidence_current=False,
            gate_accepted=True,
        )
        return result

    _require_architecture_stage(result, repo_root, normalized)
    oracle = _load_oracle()
    discovery = discover_for_change(change_root, repo_root)
    result.discovery_limitations = discovery.limitations
    for blocker in discovery.blockers:
        result.add("DISCOVERY_SURFACE_UNRESOLVED", blocker)

    design_obligations = {
        item["obligation_id"] for item in oracle.parse_design_coverage_obligations(design_text)
    }
    for gap in discovery_gaps(discovery, design_obligations):
        result.add(
            "DISCOVERY_OBLIGATION_GAP",
            f"{gap.obligation_id} from {gap.evidence_source}: {gap.rationale}",
        )

    if _TA_REQUIRED.search(design_text) and not design_obligations:
        result.add("COVERAGE_MANIFEST_MISSING", "TECHNOLOGY_ASSESSMENT REQUIRED:yes without obligations")

    receipt_path = change_root / "architecture-evidence" / "ARCH-EVIDENCE.json"
    needs_receipt = bool(discovery.operations) or _TA_REQUIRED.search(design_text) or design_obligations
    if not receipt_path.is_file():
        if needs_receipt:
            result.add("ARCH_EVIDENCE_MISSING", str(receipt_path))
        result.trust_boundary = _trust_stages(
            discovered=bool(discovery.operations),
            source_inspected=False,
            compatibility=False,
            evidence_current=False,
            gate_accepted=result.ok,
        )
        return result

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    _validate_receipt_mandatory(
        oracle,
        receipt,
        repo_root=repo_root,
        change_root=change_root,
        change_id=normalized,
        design_path=design_path,
        fetcher=integrated_fetcher,
        result=result,
    )
    result.trust_boundary = _trust_stages(
        discovered=bool(discovery.operations),
        source_inspected=True,
        compatibility=result.ok,
        evidence_current=result.ok,
        gate_accepted=result.ok,
    )
    return result


def run_design_freeze_gate(change_id: str, repo_root: Path) -> GateResult:
    """Alias границы design freeze ≡ design-ready (S2)."""

    outcome = run_design_ready_gate(change_id, repo_root)
    outcome.phase = "design-freeze"
    return outcome


def run_tasks_ready_gate(
    change_id: str,
    repo_root: Path,
    *,
    fetcher: DocumentFetcher | None = None,
) -> GateResult:
    """Gate перед `TASKS_STATUS: READY` — current receipt + identity binding."""

    result = GateResult(ok=True, phase="tasks-ready", change_id=change_id)
    normalized = _normalize_change_id(change_id, result)
    if normalized is None:
        return result
    result.change_id = normalized

    change_root = _change_root(repo_root, normalized)
    integrated_fetcher = fetcher or build_integrated_document_fetcher(
        _schema_repo(repo_root), change_root=change_root
    )
    tasks_path = change_root / "tasks.md"
    design_path = change_root / "design.md"
    if not tasks_path.is_file() or not _tasks_is_ready(tasks_path.read_text(encoding="utf-8")):
        return result
    _require_architecture_stage(result, repo_root, normalized)
    if not design_path.is_file():
        result.add("ARCH_EVIDENCE_MISSING", "tasks READY without design.md")
        return result

    oracle = _load_oracle()
    design_text = design_path.read_text(encoding="utf-8")
    design_obligations = {
        item["obligation_id"] for item in oracle.parse_design_coverage_obligations(design_text)
    }
    discovery = discover_for_change(change_root, repo_root)
    for blocker in discovery.blockers:
        result.add("DISCOVERY_SURFACE_UNRESOLVED", blocker)

    needs_receipt = (
        bool(design_obligations)
        or bool(discovery.operations)
        or _TA_REQUIRED.search(design_text)
    )

    receipt_path = change_root / "architecture-evidence" / "ARCH-EVIDENCE.json"
    if not receipt_path.is_file():
        if needs_receipt:
            result.add("ARCH_EVIDENCE_MISSING", str(receipt_path))
        return result

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    _validate_receipt_mandatory(
        oracle,
        receipt,
        repo_root=repo_root,
        change_root=change_root,
        change_id=normalized,
        design_path=design_path,
        fetcher=integrated_fetcher,
        result=result,
    )
    return result


def run_planning_review_gate(
    change_id: str,
    repo_root: Path,
    *,
    fetcher: DocumentFetcher | None = None,
    trust_policy: str = "production",
) -> GateResult:
    """S3 gate для /opsx-planning-review: strict trust + independent review."""

    outcome = run_design_ready_gate(change_id, repo_root, fetcher=fetcher)
    outcome.phase = "planning-review"
    _require_architecture_stage(outcome, repo_root, outcome.change_id)
    normalized = outcome.change_id
    change_root = _change_root(repo_root, normalized)
    design_path = change_root / "design.md"
    receipt_path = change_root / "architecture-evidence" / "ARCH-EVIDENCE.json"
    if not receipt_path.is_file():
        outcome.trust_boundary = _trust_stages(
            discovered=True,
            source_inspected=False,
            compatibility=False,
            evidence_current=False,
            gate_accepted=False,
        )
        return outcome

    oracle = _load_oracle()
    integrated_fetcher = fetcher or build_integrated_document_fetcher(
        _schema_repo(repo_root), change_root=change_root
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    strict_result = GateResult(ok=True, phase="planning-review", change_id=normalized)
    _validate_receipt_mandatory(
        oracle,
        receipt,
        repo_root=repo_root,
        change_root=change_root,
        change_id=normalized,
        design_path=design_path,
        fetcher=integrated_fetcher,
        result=strict_result,
        s3_strict=True,
        trust_policy=trust_policy,
    )
    outcome.findings.extend(strict_result.findings)
    outcome.ok = strict_result.ok and outcome.ok
    outcome.trust_boundary = _trust_stages(
        discovered=True,
        source_inspected=strict_result.ok,
        compatibility=strict_result.ok,
        evidence_current=strict_result.ok,
        gate_accepted=strict_result.ok,
    )
    return outcome


def run_apply_preflight_gate(
    change_id: str,
    repo_root: Path,
    *,
    fetcher: DocumentFetcher | None = None,
    trust_policy: str = "production",
) -> GateResult:
    """S3 gate перед /opsx-apply: mandatory change id + strict trust."""

    result = GateResult(ok=True, phase="apply-preflight", change_id=change_id)
    normalized = _normalize_change_id(change_id, result)
    if normalized is None:
        return result
    result.change_id = normalized
    _require_architecture_stage(result, repo_root, normalized)

    change_root = _change_root(repo_root, normalized)
    design_path = change_root / "design.md"
    tasks_path = change_root / "tasks.md"
    if not design_path.is_file():
        result.add("ARCH_EVIDENCE_MISSING", "apply-preflight requires design.md")
        return result

    oracle = _load_oracle()
    design_text = design_path.read_text(encoding="utf-8")
    design_obligations = {
        item["obligation_id"] for item in oracle.parse_design_coverage_obligations(design_text)
    }
    discovery = discover_for_change(change_root, repo_root)
    needs_receipt = (
        bool(design_obligations)
        or bool(discovery.operations)
        or _TA_REQUIRED.search(design_text)
    )
    tasks_ready = tasks_path.is_file() and _tasks_is_ready(tasks_path.read_text(encoding="utf-8"))
    if not tasks_ready:
        result.add("APPLY_PREFLIGHT_BLOCKED", "tasks.md TASKS_STATUS: READY required")
        return result
    if not _design_is_ready(design_text):
        result.add("APPLY_PREFLIGHT_BLOCKED", "design.md DESIGN_STATUS: READY required")
        return result
    if needs_receipt and tasks_ready:
        receipt_path = change_root / "architecture-evidence" / "ARCH-EVIDENCE.json"
        if not receipt_path.is_file():
            result.add("ARCH_EVIDENCE_MISSING", str(receipt_path))
            return result

    review_outcome = run_planning_review_gate(
        normalized, repo_root, fetcher=fetcher, trust_policy=trust_policy
    )
    result.findings.extend(review_outcome.findings)
    result.ok = review_outcome.ok and result.ok
    result.discovery_limitations = review_outcome.discovery_limitations
    result.trust_boundary = review_outcome.trust_boundary
    if tasks_ready and needs_receipt and not result.ok:
        result.add("APPLY_PREFLIGHT_BLOCKED", "critical evidence untrusted or incompatible")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Architecture evidence planning gate")
    parser.add_argument(
        "command",
        choices=(
            "design-ready",
            "design-freeze",
            "tasks-ready",
            "planning-review",
            "apply-preflight",
        ),
    )
    parser.add_argument("--change", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    repo_root = args.root.resolve()
    if args.command == "design-freeze":
        outcome = run_design_freeze_gate(args.change, repo_root)
    elif args.command == "design-ready":
        outcome = run_design_ready_gate(args.change, repo_root)
    elif args.command == "tasks-ready":
        outcome = run_tasks_ready_gate(args.change, repo_root)
    elif args.command == "planning-review":
        outcome = run_planning_review_gate(args.change, repo_root)
    else:
        outcome = run_apply_preflight_gate(args.change, repo_root)
    payload = {
        "ok": outcome.ok,
        "phase": outcome.phase,
        "change_id": outcome.change_id,
        "gate_implementation_id": GATE_IMPLEMENTATION_ID,
        "trust_boundary": outcome.trust_boundary,
        "discovery_limitations": outcome.discovery_limitations,
        "findings": [{"code": f.code, "detail": f.detail} for f in outcome.findings],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
