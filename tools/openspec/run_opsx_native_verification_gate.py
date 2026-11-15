"""OPSX-native verification gate (pinned local verifier, execution-HEAD L3).

Machine-checkable closure, activation hash, and committed-input identity checks.
Canonical verification authority for Owner merge gates — not GitHub Actions
workflow status alone.

Committed ``target_revision`` in projectization / closure receipt is the
**immutable input-revision identity** (AT-2bis-style bind). ``execution_head_sha``
is the git HEAD at verification time. They MAY differ; equality is not required
for PASS. Authoritative verification requires a **clean** worktree and that
on-disk artifacts match ``HEAD`` (no unstaged receipt refresh).

Prerequisites (same as dependency-closure T1):
  - Poetry env with test deps, or repo ``.venv-ae``
  - ``bash opsx_tools/openspec-cli-node/setup.sh`` when node_modules absent
"""

from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

_repo_root = Path(__file__).resolve().parents[2]
_control_plane = _repo_root / ".cursor" / "pipeline" / "control-plane-tests"
if str(_control_plane) not in sys.path:
    sys.path.insert(0, str(_control_plane))

from opsx_dependency_closure import (  # noqa: E402
    CLOSURE_PREDICATE_VERSION,
    DEFERRED_ACTIVATION_HASH_DRIFT_COMMANDS,
    evaluate_dependency_closure,
    git_blob_sha256,
    read_activation_rows,
)

GATE_ARTIFACT_SCHEMA = "opsx-native-verification-gate-v0"
VERIFIER_REL_PATH = "tools/openspec/run_opsx_native_verification_gate.py"
RECEIPT_REL = "opsx/transfer/DEPENDENCY_CLOSURE_RECEIPT.json"
PROJECTIZATION_REL = "opsx/projectization.yaml"


@dataclass
class CheckResult:
    """Single machine-checkable assertion."""

    check_id: str
    status: Literal["PASS", "FAIL"]
    detail: str
    evidence: dict[str, Any] | None = None


@dataclass
class NativeGateReport:
    """L3 report bound to execution HEAD and committed input revision."""

    schema_version: str
    gate_id: str
    generated_at_utc: str
    execution_head_sha: str
    committed_input_revision: str | None
    verifier_identity_sha256: str | None
    closure_predicate_version: str
    checks: list[CheckResult]
    overall_status: Literal["PASS", "FAIL"]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gate_id": self.gate_id,
            "generated_at_utc": self.generated_at_utc,
            "execution_head_sha": self.execution_head_sha,
            "committed_input_revision": self.committed_input_revision,
            "verifier_identity_sha256": self.verifier_identity_sha256,
            "closure_predicate_version": self.closure_predicate_version,
            "overall_status": self.overall_status,
            "checks": [asdict(c) for c in self.checks],
        }


def git_head(root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def git_status_porcelain(root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain"],
        text=True,
    ).strip()


def path_clean_vs_head(root: Path, relpath: str) -> bool:
    """True when ``relpath`` has no unstaged or staged diff vs ``HEAD``."""

    proc = subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", relpath],
        check=False,
    )
    return proc.returncode == 0


def read_json_at_head(root: Path, relpath: str) -> dict[str, Any] | None:
    """Parse JSON blob as committed at ``HEAD`` (returns None if missing)."""

    proc = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{relpath}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def read_yaml_field_at_head(root: Path, relpath: str, field: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{relpath}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    import yaml

    data = yaml.safe_load(proc.stdout) or {}
    value = data.get(field)
    return str(value) if value is not None else None


def verifier_identity_at_head(root: Path) -> str | None:
    return git_blob_sha256(root, VERIFIER_REL_PATH)


def check_execution_head(root: Path) -> tuple[CheckResult, str]:
    head = git_head(root)
    return (
        CheckResult(
            check_id="L3_EXECUTION_HEAD",
            status="PASS",
            detail="git rev-parse HEAD",
            evidence={"execution_head_sha": head},
        ),
        head,
    )


def check_working_tree_clean(root: Path) -> CheckResult:
    porcelain = git_status_porcelain(root)
    ok = porcelain == ""
    return CheckResult(
        check_id="WORKING_TREE_CLEAN",
        status="PASS" if ok else "FAIL",
        detail="empty git status --porcelain" if ok else "dirty worktree",
        evidence={"porcelain": porcelain.splitlines() if porcelain else []},
    )


def check_committed_artifacts_unmodified(root: Path) -> CheckResult:
    receipt_ok = path_clean_vs_head(root, RECEIPT_REL)
    proj_ok = path_clean_vs_head(root, PROJECTIZATION_REL)
    ok = receipt_ok and proj_ok
    return CheckResult(
        check_id="COMMITTED_ARTIFACTS_UNMODIFIED",
        status="PASS" if ok else "FAIL",
        detail=(
            f"receipt_matches_HEAD={receipt_ok} "
            f"projectization_matches_HEAD={proj_ok}"
        ),
        evidence={
            "receipt_path": RECEIPT_REL,
            "projectization_path": PROJECTIZATION_REL,
        },
    )


def committed_input_revision(root: Path) -> str | None:
    """Input-revision pin from committed receipt at HEAD."""

    stored = read_json_at_head(root, RECEIPT_REL)
    if not stored:
        return None
    return str(
        stored.get("target_revision")
        or (stored.get("identity") or {}).get("target_revision")
        or ""
    ) or None


def check_input_revision_pin_consistency(root: Path) -> CheckResult:
    """Committed receipt and projectization must agree on input revision."""

    receipt_rev = committed_input_revision(root)
    proj_rev = read_yaml_field_at_head(root, PROJECTIZATION_REL, "target_revision")
    ok = bool(receipt_rev and proj_rev and receipt_rev == proj_rev)
    return CheckResult(
        check_id="INPUT_REVISION_PIN_CONSISTENCY",
        status="PASS" if ok else "FAIL",
        detail=(
            f"receipt_target_revision={receipt_rev} "
            f"projectization_target_revision={proj_rev}"
        ),
        evidence={
            "committed_input_revision": receipt_rev,
            "projectization_target_revision": proj_rev,
        },
    )


def check_committed_receipt_pass(root: Path) -> CheckResult:
    """Committed closure receipt must record PASS (historical closure bind)."""

    stored = read_json_at_head(root, RECEIPT_REL)
    if not stored:
        return CheckResult(
            check_id="COMMITTED_RECEIPT_PASS",
            status="FAIL",
            detail=f"missing committed {RECEIPT_REL} at HEAD",
        )
    status = stored.get("status")
    ok = status == "PASS"
    return CheckResult(
        check_id="COMMITTED_RECEIPT_PASS",
        status="PASS" if ok else "FAIL",
        detail=f"committed receipt status={status}",
        evidence={
            "committed_input_revision": committed_input_revision(root),
            "receipt_path": RECEIPT_REL,
        },
    )


def check_closure_pass(root: Path, head: str) -> CheckResult:
    closure = evaluate_dependency_closure(root)
    status = closure.get("status")
    target = closure.get("target_revision")
    ok = status == "PASS" and target == head and not closure.get("global_failures")
    return CheckResult(
        check_id="CLOSURE_EVALUATOR_PASS",
        status="PASS" if ok else "FAIL",
        detail=f"status={status} target_revision={target}",
        evidence={
            "status": status,
            "target_revision": target,
            "execution_head_sha": head,
            "global_failures_count": len(closure.get("global_failures") or []),
            "command_blocks_count": len(closure.get("command_blocks") or []),
        },
    )


def check_command_hash_bytes(root: Path) -> CheckResult:
    rows = read_activation_rows(resource_path(root, "opsx/registry/command_activation.ndjson"))
    mismatches: list[dict[str, str]] = []
    for row in rows:
        if row.get("active") is not True or row.get("installed") is not True:
            continue
        command_id = str(row.get("command_id"))
        if command_id in DEFERRED_ACTIVATION_HASH_DRIFT_COMMANDS:
            continue
        relative = f".cursor/commands/{command_id}.md"
        blob_hash = git_blob_sha256(root, relative)
        ledger_hash = str(row.get("command_hash", ""))
        if blob_hash is not None and ledger_hash != blob_hash:
            mismatches.append(
                {
                    "command_id": command_id,
                    "ledger_command_hash": ledger_hash,
                    "git_blob_sha256": blob_hash,
                }
            )
    return CheckResult(
        check_id="COMMAND_HASH_VS_GIT_BLOB",
        status="PASS" if not mismatches else "FAIL",
        detail=f"mismatches={len(mismatches)}",
        evidence={"mismatches": mismatches},
    )


def check_verifier_identity(root: Path) -> CheckResult:
    ident = verifier_identity_at_head(root)
    ok = ident is not None
    return CheckResult(
        check_id="VERIFIER_IDENTITY",
        status="PASS" if ok else "FAIL",
        detail=f"git_blob_sha256({VERIFIER_REL_PATH})",
        evidence={"verifier_identity_sha256": ident, "verifier_path": VERIFIER_REL_PATH},
    )


def run_pytest_closure(root: Path, python: str) -> CheckResult:
    proc = subprocess.run(
        [
            python,
            "-m",
            "pytest",
            ".cursor/pipeline/control-plane-tests/test_opsx_dependency_closure.py",
            "-q",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return CheckResult(
        check_id="T1_PYTEST_DEPENDENCY_CLOSURE",
        status="PASS" if proc.returncode == 0 else "FAIL",
        detail=proc.stdout.strip().splitlines()[-1] if proc.stdout else proc.stderr[:200],
        evidence={"exit_code": proc.returncode},
    )


def refresh_closure_receipt(root: Path) -> dict[str, Any]:
    """Rewrite DEPENDENCY_CLOSURE_RECEIPT.json from live evaluator (authorized repair only)."""

    closure = evaluate_dependency_closure(root)
    receipt_path = resource_path(root, "opsx", "transfer", "DEPENDENCY_CLOSURE_RECEIPT.json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(closure, indent=2) + "\n", encoding="utf-8")
    return closure


def pin_projectization_head(root: Path, head: str) -> None:
    import yaml

    path = resource_path(root, "opsx/projectization.yaml")
    proj = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    proj["target_revision"] = head
    path.write_text(yaml.safe_dump(proj, sort_keys=False), encoding="utf-8")


def run_gate(
    root: Path,
    *,
    gate_id: str,
    run_pytest: bool,
    python: str,
) -> NativeGateReport:
    checks: list[CheckResult] = []
    head_check, head = check_execution_head(root)
    checks.append(head_check)
    checks.append(check_verifier_identity(root))
    checks.append(check_working_tree_clean(root))
    checks.append(check_committed_artifacts_unmodified(root))
    input_rev = committed_input_revision(root)
    checks.append(check_input_revision_pin_consistency(root))
    checks.append(check_committed_receipt_pass(root))
    checks.append(check_closure_pass(root, head))
    checks.append(check_command_hash_bytes(root))
    if run_pytest:
        checks.append(run_pytest_closure(root, python))
    overall = "PASS" if all(c.status == "PASS" for c in checks) else "FAIL"
    return NativeGateReport(
        schema_version=GATE_ARTIFACT_SCHEMA,
        gate_id=gate_id,
        generated_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        execution_head_sha=head,
        committed_input_revision=input_rev,
        verifier_identity_sha256=verifier_identity_at_head(root),
        closure_predicate_version=CLOSURE_PREDICATE_VERSION,
        checks=checks,
        overall_status=overall,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OPSX-native verification gate")
    parser.add_argument(
        "--root",
        type=Path,
        default=_repo_root,
        help="Repository root",
    )
    parser.add_argument(
        "--gate-id",
        default="ecr-lite-v0-native",
        help="Logical gate id for L3 artifact naming",
    )
    parser.add_argument(
        "--skip-pytest",
        action="store_true",
        help="Skip T1 pytest (faster; closure evaluator still runs)",
    )
    parser.add_argument(
        "--write-report",
        type=Path,
        default=None,
        help="Write JSON report to path",
    )
    parser.add_argument(
        "--refresh-receipt",
        action="store_true",
        help=(
            "Repair mode only: rewrite receipt + pin projectization to HEAD. "
            "Does not attest merge-gate PASS; commit artifacts and re-run without this flag."
        ),
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter for pytest subprocess",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.refresh_receipt:
        head = git_head(root)
        pin_projectization_head(root, head)
        refresh_closure_receipt(root)
        repair_report = {
            "schema_version": GATE_ARTIFACT_SCHEMA,
            "mode": "repair_refresh_receipt",
            "execution_head_sha": head,
            "overall_status": "FAIL",
            "detail": (
                "Repair wrote closure artifacts; merge gate PASS requires clean "
                "committed tree and a verification run without --refresh-receipt"
            ),
            "porcelain": git_status_porcelain(root).splitlines(),
        }
        print(json.dumps(repair_report, indent=2))
        return 2
    report = run_gate(
        root,
        gate_id=args.gate_id,
        run_pytest=not args.skip_pytest,
        python=args.python,
    )
    payload = report.to_json()
    print(json.dumps(payload, indent=2))
    if args.write_report:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0 if report.overall_status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
