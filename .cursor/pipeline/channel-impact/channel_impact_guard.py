# -*- coding: utf-8 -*-
"""Channel Impact Guard V0 — single policy module for planning, verify, and CI."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

import yaml

GUARD_SCHEMA_VERSION = "channel-impact-guard-v0"
ARTIFACT_NAME = "channel-impact-guard.json"
CHANGE_PREFIX = "openspec/changes/"

Applicability = Literal["APPLICABLE", "NOT_APPLICABLE", "NO_CHANGE", "UNRESOLVED"]
GatePhase = Literal["planning_freeze", "planning_apply", "archive", "ci_pr"]

CHANNEL_DECISIONS = frozenset({"CHANGED", "NOT_AFFECTED", "NO_CHANGE", "EXCLUDED"})


class ChannelImpactGuardError(Exception):
    """Guard violation with stable machine code."""

    def __init__(self, code: str, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


@dataclass(frozen=True)
class GuardResult:
    """Outcome of one guard evaluation."""

    status: Literal["PASS", "FAIL", "NOT_APPLICABLE"]
    code: str
    message: str
    applicability: Applicability | None
    surfaces_touched: tuple[str, ...]
    change_ids: tuple[str, ...]
    business_surfaces_touched: bool


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_channel_impact_registry(root: Path | None = None) -> dict[str, Any]:
    base = root or repo_root()
    yaml_path = base / "opsx" / "project" / "capabilities.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ChannelImpactGuardError("INVALID_REGISTRY", "capabilities.yaml invalid")
    block = data.get("channel_impact")
    if not isinstance(block, dict):
        raise ChannelImpactGuardError("MISSING_REGISTRY", "channel_impact block missing")
    return block


def mandatory_enforced_channels(registry: dict[str, Any]) -> tuple[str, ...]:
    raw = registry.get("mandatory_enforced_channels")
    if isinstance(raw, list) and raw:
        return tuple(str(x) for x in raw)
    return ("telegram", "max")


def optional_not_enforced_channels(registry: dict[str, Any]) -> tuple[str, ...]:
    raw = registry.get("optional_not_enforced_channels")
    if isinstance(raw, list):
        return tuple(str(x) for x in raw)
    return ("avito",)


def all_surface_globs(registry: dict[str, Any]) -> tuple[str, ...]:
    keys = ("surface_globs", "business_effect_globs", "openspec_artifact_globs")
    out: list[str] = []
    for key in keys:
        val = registry.get(key)
        if isinstance(val, list):
            out.extend(str(g) for g in val)
    return tuple(out)


def _normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def path_matches_globs(repo_path: str, globs: Sequence[str]) -> bool:
    normalized = _normalize_repo_path(repo_path)
    for pattern in globs:
        if fnmatch.fnmatch(normalized, _normalize_repo_path(pattern)):
            return True
    return False


def surfaces_in_paths(paths: Sequence[str], globs: Sequence[str]) -> tuple[str, ...]:
    touched = [p for p in paths if path_matches_globs(p, globs)]
    return tuple(sorted(set(touched)))


def git_changed_files(base: str, head: str, *, root: Path) -> tuple[str, ...]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ChannelImpactGuardError(
            "GIT_DIFF_FAILED",
            proc.stderr.strip() or "git diff failed",
        )
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def git_head_sha(root: Path) -> str:
    return git_rev_parse("HEAD", root)


def git_rev_parse(ref: str, root: Path) -> str:
    """Resolve a git ref to full SHA (used for verified execution identity)."""
    proc = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ChannelImpactGuardError(
            "GIT_REF_FAILED",
            proc.stderr.strip() or f"git rev-parse {ref} failed",
        )
    return proc.stdout.strip()


def change_ids_from_paths(paths: Sequence[str]) -> tuple[str, ...]:
    ids: set[str] = set()
    for raw in paths:
        path = _normalize_repo_path(raw)
        if not path.startswith(CHANGE_PREFIX):
            continue
        rest = path[len(CHANGE_PREFIX) :]
        change_id = rest.split("/", 1)[0]
        if change_id:
            ids.add(change_id)
    return tuple(sorted(ids))


def artifact_path(change_id: str, *, root: Path) -> Path:
    return root / "openspec" / "changes" / change_id / ARTIFACT_NAME


def change_root(change_id: str, *, root: Path) -> Path:
    return root / "openspec" / "changes" / change_id


def load_declaration(change_id: str, *, root: Path) -> dict[str, Any]:
    path = artifact_path(change_id, root=root)
    if not path.is_file():
        raise ChannelImpactGuardError(
            "UNRESOLVED_APPLICABILITY",
            f"missing {ARTIFACT_NAME} for change {change_id}",
            detail={"change_id": change_id, "path": str(path)},
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ChannelImpactGuardError("INVALID_ARTIFACT", "artifact root must be object")
    if doc.get("schema_version") != GUARD_SCHEMA_VERSION:
        raise ChannelImpactGuardError(
            "INVALID_ARTIFACT",
            f"schema_version must be {GUARD_SCHEMA_VERSION}",
        )
    return doc


def _lf_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_scenario_receipt(change_id: str, *, root: Path) -> None:
    """Bind PA to existing scenario-coverage receipt (no self-declared PASS)."""
    change_dir = change_root(change_id, root=root)
    receipt_path = change_dir / "scenario-coverage" / "plan.receipt.json"
    plan_path = change_dir / "scenario-coverage" / "plan.md"
    if not receipt_path.is_file() or not plan_path.is_file():
        raise ChannelImpactGuardError(
            "SCENARIO_RECEIPT_MISSING",
            "scenario-coverage/plan.receipt.json and plan.md required",
        )
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ChannelImpactGuardError(
            "SCENARIO_RECEIPT_INVALID",
            str(error),
        ) from error
    if not isinstance(receipt, dict):
        raise ChannelImpactGuardError("SCENARIO_RECEIPT_INVALID", "receipt must be object")
    expected = {
        "SCHEMA_VERSION": 1,
        "SCENARIO_COVERAGE_RECEIPT": True,
        "CHANGE_ID": change_id,
        "MODE": "plan",
        "PLAN_STATUS": "ready",
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ChannelImpactGuardError(
                "SCENARIO_RECEIPT_INTEGRITY_FAIL",
                f"receipt field {key} mismatch",
            )
    if receipt.get("MATERIALIZATION_STATUS") not in {"COMMITTED", "ALREADY_CURRENT"}:
        raise ChannelImpactGuardError("SCENARIO_RECEIPT_INTEGRITY_FAIL", "not materialized")
    if receipt.get("PLAN_CONTENT_HASH") != _lf_hash(plan_path):
        raise ChannelImpactGuardError("SCENARIO_RECEIPT_STALE", "plan.md hash drift")


def _reject_legacy_self_declared_pa(declaration: dict[str, Any]) -> None:
    """Block self-declared product_acceptance PASS (evidence must be machine-bound)."""
    legacy = declaration.get("product_acceptance")
    if not isinstance(legacy, dict):
        return
    if legacy.get("status") in ("PASS", "READY"):
        raise ChannelImpactGuardError(
            "PA_SELF_DECLARED_FORBIDDEN",
            "product_acceptance.status is not machine evidence; use product_acceptance_evidence + scenario receipt",
            detail={"legacy_keys": sorted(legacy.keys())},
        )
    refs = legacy.get("scenario_evidence_refs")
    if isinstance(refs, list) and refs:
        raise ChannelImpactGuardError(
            "PA_SELF_DECLARED_FORBIDDEN",
            "scenario_evidence_refs without scenario-coverage receipt binding is forbidden",
        )


def _validate_execution_identity(
    declaration: dict[str, Any],
    *,
    root: Path,
    expected_head: str | None = None,
) -> None:
    pa = declaration.get("product_acceptance_evidence")
    if not isinstance(pa, dict):
        raise ChannelImpactGuardError(
            "PA_EVIDENCE_MISSING",
            "product_acceptance_evidence block required (not self-declared PASS)",
        )
    rev = pa.get("git_revision")
    head = expected_head or git_head_sha(root)
    if not isinstance(rev, str) or rev != head:
        raise ChannelImpactGuardError(
            "PA_EVIDENCE_STALE_REVISION",
            "product_acceptance_evidence.git_revision must match verified HEAD",
            detail={"expected": head, "declared": rev},
        )
    receipt_rel = pa.get("scenario_coverage_receipt")
    if receipt_rel != "scenario-coverage/plan.receipt.json":
        raise ChannelImpactGuardError(
            "PA_EVIDENCE_INVALID_RECEIPT_PATH",
            "scenario_coverage_receipt must be scenario-coverage/plan.receipt.json",
        )


def _validate_mandatory_channel_decisions(
    declaration: dict[str, Any],
    *,
    registry: dict[str, Any],
) -> None:
    decisions = declaration.get("mandatory_channel_decisions")
    if not isinstance(decisions, dict):
        raise ChannelImpactGuardError(
            "MANDATORY_CHANNEL_DECISIONS_MISSING",
            "mandatory_channel_decisions required for each enforced channel",
        )
    enforced = mandatory_enforced_channels(registry)
    for channel in enforced:
        entry = decisions.get(channel)
        if not isinstance(entry, dict):
            raise ChannelImpactGuardError(
                "MANDATORY_CHANNEL_DECISION_MISSING",
                f"no decision for mandatory channel {channel}",
                detail={"channel": channel},
            )
        decision = entry.get("decision")
        if decision not in CHANNEL_DECISIONS:
            raise ChannelImpactGuardError(
                "MANDATORY_CHANNEL_DECISION_INVALID",
                f"invalid decision for {channel}",
            )
        if decision in ("NO_CHANGE", "NOT_AFFECTED"):
            just = entry.get("justification")
            if not isinstance(just, str) or len(just.strip()) < 12:
                raise ChannelImpactGuardError(
                    "CHANNEL_DECISION_UNJUSTIFIED",
                    f"{channel} {decision} requires justification (min 12 chars)",
                )
        if decision == "EXCLUDED":
            raise ChannelImpactGuardError(
                "MANDATORY_CHANNEL_EXCLUDED_FORBIDDEN",
                f"cannot EXCLUDE mandatory channel {channel}",
            )


def _only_optional_channel_surfaces(
    touched: Sequence[str],
    optional: Sequence[str],
) -> bool:
    if not touched:
        return False
    for path in touched:
        lower = path.lower()
        if "avito" in lower and not any(
            req in lower for req in ("telegram", "/max/", "channels/max")
        ):
            continue
        if any(f"channels/{opt}" in lower or f"webhooks/{opt}" in lower for opt in optional):
            if not any(
                f"channels/{req}" in lower or f"webhooks/{req}" in lower
                for req in ("telegram", "max")
            ):
                continue
        return False
    return True


def evaluate_change_impact(
    change_id: str,
    *,
    root: Path,
    registry: dict[str, Any],
    diff_paths: Sequence[str] | None = None,
) -> tuple[tuple[str, ...], bool]:
    """Surfaces touched for a change (diff + openspec artifact paths)."""
    globs = all_surface_globs(registry)
    touched: set[str] = set()
    if diff_paths:
        touched.update(surfaces_in_paths(diff_paths, globs))
    croot = change_root(change_id, root=root)
    if croot.is_dir():
        for rel in (
            "spec.md",
            "design.md",
            "owner-contract.md",
        ):
            p = croot / rel
            if p.is_file():
                touched.add(p.relative_to(root).as_posix())
        for spec in (croot / "specs").rglob("spec.md"):
            touched.add(spec.relative_to(root).as_posix())
    business_globs = registry.get("business_effect_globs") or []
    business_hit = bool(surfaces_in_paths(tuple(touched) if touched else diff_paths or (), business_globs))
    if diff_paths:
        business_hit = business_hit or bool(surfaces_in_paths(diff_paths, business_globs))
    return tuple(sorted(touched)), business_hit


def _validate_declaration_semantics(
    declaration: dict[str, Any],
    *,
    surfaces_touched: Sequence[str],
    business_surfaces: bool,
    registry: dict[str, Any],
    root: Path,
    head_sha: str | None = None,
) -> Applicability:
    _reject_legacy_self_declared_pa(declaration)
    applicability = declaration.get("applicability")
    if applicability not in ("APPLICABLE", "NOT_APPLICABLE", "NO_CHANGE"):
        raise ChannelImpactGuardError(
            "UNRESOLVED_APPLICABILITY",
            "applicability must be APPLICABLE, NOT_APPLICABLE, or NO_CHANGE",
        )

    _validate_mandatory_channel_decisions(declaration, registry=registry)

    optional = optional_not_enforced_channels(registry)
    only_avito = _only_optional_channel_surfaces(surfaces_touched, optional)

    if business_surfaces or surfaces_touched:
        if applicability == "NOT_APPLICABLE" and not only_avito:
            raise ChannelImpactGuardError(
                "DECLARATION_MISMATCH",
                "business/channel surfaces touched but applicability NOT_APPLICABLE",
                detail={"surfaces": list(surfaces_touched), "business": business_surfaces},
            )

    if applicability == "NO_CHANGE":
        justification = declaration.get("no_change_justification")
        if not isinstance(justification, str) or len(justification.strip()) < 12:
            raise ChannelImpactGuardError("NO_CHANGE_UNJUSTIFIED", "NO_CHANGE needs justification")

    if applicability == "APPLICABLE":
        change_id = declaration.get("change_id")
        if not isinstance(change_id, str) or not change_id:
            raise ChannelImpactGuardError("INVALID_ARTIFACT", "change_id required for APPLICABLE")
        if head_sha is not None:
            _validate_scenario_receipt(change_id, root=root)
            _validate_execution_identity(declaration, root=root, expected_head=head_sha)

    if applicability == "NOT_APPLICABLE":
        if business_surfaces and not only_avito:
            raise ChannelImpactGuardError(
                "AMBIGUOUS_APPLICABILITY",
                "cannot claim NOT_APPLICABLE with business-effect surfaces",
            )

    return applicability  # type: ignore[return-value]


def evaluate_change(
    change_id: str,
    *,
    surfaces_touched: Sequence[str],
    business_surfaces: bool,
    root: Path,
    registry: dict[str, Any],
    head_sha: str | None = None,
) -> GuardResult:
    declaration = load_declaration(change_id, root=root)
    declaration = {**declaration, "change_id": change_id}
    applicability = _validate_declaration_semantics(
        declaration,
        surfaces_touched=surfaces_touched,
        business_surfaces=business_surfaces,
        registry=registry,
        root=root,
        head_sha=head_sha,
    )
    return GuardResult(
        status="PASS",
        code="CHANNEL_IMPACT_GUARD_PASS",
        message=f"change {change_id} guard PASS ({applicability})",
        applicability=applicability,
        surfaces_touched=tuple(surfaces_touched),
        change_ids=(change_id,),
        business_surfaces_touched=business_surfaces,
    )


def evaluate_diff_scope(
    *,
    base: str,
    head: str,
    root: Path | None = None,
    phase: GatePhase = "ci_pr",
) -> GuardResult:
    base_path = root or repo_root()
    registry = load_channel_impact_registry(base_path)
    globs = all_surface_globs(registry)
    all_changed = git_changed_files(base, head, root=base_path)
    touched = surfaces_in_paths(all_changed, globs)
    business = bool(
        surfaces_in_paths(all_changed, registry.get("business_effect_globs") or [])
    )

    if not touched and not business:
        return GuardResult(
            status="NOT_APPLICABLE",
            code="CHANNEL_IMPACT_NOT_APPLICABLE",
            message="no channel/business surfaces in diff",
            applicability=None,
            surfaces_touched=(),
            change_ids=(),
            business_surfaces_touched=False,
        )

    change_ids = change_ids_from_paths(all_changed)
    if not change_ids:
        raise ChannelImpactGuardError(
            "UNRESOLVED_NO_CHANGE_CONTOUR",
            "channel/business impact in diff without openspec change contour",
            detail={"surfaces": list(touched)},
        )

    head_sha = git_rev_parse(head, base_path)
    for change_id in change_ids:
        _, biz = evaluate_change_impact(change_id, root=base_path, registry=registry, diff_paths=all_changed)
        result = evaluate_change(
            change_id,
            surfaces_touched=touched,
            business_surfaces=business or biz,
            root=base_path,
            registry=registry,
            head_sha=head_sha,
        )
        if result.status != "PASS":
            return result

    return GuardResult(
        status="PASS",
        code="CHANNEL_IMPACT_GUARD_PASS",
        message=f"guard PASS for {', '.join(change_ids)}",
        applicability="APPLICABLE",
        surfaces_touched=touched,
        change_ids=change_ids,
        business_surfaces_touched=business,
    )


def assert_planning_ready(change_id: str, *, root: Path | None = None, phase: GatePhase = "planning_apply") -> None:
    """Planning readiness before apply-design / freeze (machine gate)."""
    base_path = root or repo_root()
    registry = load_channel_impact_registry(base_path)
    touched, business = evaluate_change_impact(change_id, root=base_path, registry=registry)
    optional = optional_not_enforced_channels(registry)
    if not touched and not business:
        return
    if _only_optional_channel_surfaces(touched, optional) and not business:
        return

    artifact = artifact_path(change_id, root=base_path)
    if not artifact.is_file():
        raise ChannelImpactGuardError(
            "UNRESOLVED_APPLICABILITY",
            f"planning blocked: missing {ARTIFACT_NAME}",
            detail={"change_id": change_id, "surfaces": list(touched), "business": business},
        )

    declaration = load_declaration(change_id, root=base_path)
    declaration = {**declaration, "change_id": change_id}
    _reject_legacy_self_declared_pa(declaration)
    applicability = declaration.get("applicability")
    if applicability in (None, "UNRESOLVED"):
        raise ChannelImpactGuardError("UNRESOLVED_APPLICABILITY", "applicability unresolved at planning gate")

    if applicability == "APPLICABLE":
        _validate_mandatory_channel_decisions(declaration, registry=registry)
        pa = declaration.get("product_acceptance_evidence")
        if not isinstance(pa, dict) or pa.get("planning_status") not in ("READY", "PASS"):
            raise ChannelImpactGuardError(
                "PLANNING_PA_INCOMPLETE",
                "APPLICABLE planning requires product_acceptance_evidence.planning_status READY|PASS",
            )
        _validate_scenario_receipt(change_id, root=base_path)

    _validate_declaration_semantics(
        declaration,
        surfaces_touched=touched,
        business_surfaces=business,
        registry=registry,
        root=base_path,
        head_sha=None,
    )


def assert_planning_ready_for_freeze(change_id: str, *, root: Path | None = None) -> None:
    assert_planning_ready(change_id, root=root, phase="planning_freeze")


def run_archive_verify_gate(
    change_id: str,
    *,
    base: str,
    head: str,
    root: Path | None = None,
) -> GuardResult:
    """Executable archive verify gate (must be invoked by verify automation)."""
    base_path = root or repo_root()
    try:
        return archive_gate(change_id, base=base, head=head, root=base_path)
    except ChannelImpactGuardError:
        raise


def archive_gate(change_id: str, *, base: str, head: str, root: Path | None = None) -> GuardResult:
    result = evaluate_diff_scope(base=base, head=head, root=root, phase="archive")
    base_path = root or repo_root()
    if result.status == "NOT_APPLICABLE":
        touched, business = evaluate_change_impact(
            change_id,
            root=base_path,
            registry=load_channel_impact_registry(base_path),
        )
        if touched or business:
            raise ChannelImpactGuardError(
                "AMBIGUOUS_APPLICABILITY",
                "change has impact surfaces but diff scope NOT_APPLICABLE",
            )
        return result
    if change_id not in result.change_ids:
        raise ChannelImpactGuardError(
            "CHANGE_SCOPE_MISMATCH",
            f"verify change {change_id} not in diff set {result.change_ids}",
        )
    base_path = root or repo_root()
    registry = load_channel_impact_registry(base_path)
    head_sha = git_rev_parse(head, base_path)
    declaration = load_declaration(change_id, root=base_path)
    declaration = {**declaration, "change_id": change_id}
    _validate_declaration_semantics(
        declaration,
        surfaces_touched=result.surfaces_touched,
        business_surfaces=result.business_surfaces_touched,
        registry=registry,
        root=root or repo_root(),
        head_sha=head_sha,
    )
    _validate_scenario_receipt(change_id, root=root or repo_root())
    _validate_execution_identity(declaration, root=root or repo_root(), expected_head=head_sha)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Channel Impact Guard V0")
    parser.add_argument(
        "command",
        choices=("ci-gate", "freeze-check", "archive-gate", "planning-gate", "verify-gate"),
    )
    parser.add_argument("--base", default="origin/stage")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--change", default="")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: current working directory)",
    )
    args = parser.parse_args(argv)
    root = (args.root or Path.cwd()).resolve()
    try:
        if args.command == "ci-gate":
            result = evaluate_diff_scope(base=args.base, head=args.head, root=root)
            print(json.dumps(result.__dict__, indent=2, default=str))
            return 0 if result.status in ("PASS", "NOT_APPLICABLE") else 1
        if args.command in ("freeze-check", "planning-gate"):
            if not args.change:
                print("change required", file=sys.stderr)
                return 2
            assert_planning_ready(args.change, root=root)
            print("PLANNING_CHANNEL_IMPACT_OK")
            return 0
        if args.command in ("archive-gate", "verify-gate"):
            if not args.change:
                print("change required", file=sys.stderr)
                return 2
            result = run_archive_verify_gate(
                args.change, base=args.base, head=args.head, root=root
            )
            print(json.dumps(result.__dict__, indent=2, default=str))
            return 0 if result.status in ("PASS", "NOT_APPLICABLE") else 1
    except ChannelImpactGuardError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        if error.detail:
            print(json.dumps(error.detail), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
