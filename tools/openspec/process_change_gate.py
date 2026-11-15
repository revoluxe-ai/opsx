"""OPSX process-change pre-merge gate (ECR-LITE V0).

Read-only closure evaluation, ECR schema validation, D3 review rules, and L3
evidence binding at PR ``HEAD``. Does not write product trees or OpenSpec changes.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

_default_root = Path(__file__).resolve().parents[2]
_control_plane = _default_root / ".cursor" / "pipeline" / "control-plane-tests"
if str(_control_plane) not in sys.path:
    sys.path.insert(0, str(_control_plane))

_active_root: Path | None = None

from opsx_dependency_closure import (  # noqa: E402
    CLOSURE_PREDICATE_VERSION,
    evaluate_dependency_closure,
)

GATE_SCHEMA_VERSION = "opsx-process-change-gate-v0"
ECR_SCHEMA_VERSION = "opsx-ecr-lite-v0"
PCP_ID_RE = re.compile(r"^PCP-[0-9]{8}-[a-z0-9][a-z0-9-]{0,31}$")
META_COMMIT_PCP_RE = re.compile(r"PCP-ID:\s*(\S+)", re.I)
META_COMMIT_ECR_RE = re.compile(
    r"\opsx/process-changes/[^\s]+/ecr-lite\.json",
    re.I,
)
WAIVER_POLICY_ID = "process-review-waiver-v0"
WAIVER_POLICY_REL = "opsx/policies/process-review-waiver-v0.md"
ATTESTATION_SCHEMA_VERSION = "opsx-process-review-attestation-v0"

GateStatus = Literal["PASS", "FAIL", "NOT_APPLICABLE"]
Tier = Literal["LITE", "STANDARD", "FULL", "UNKNOWN"]


class ProcessChangeGateError(Exception):
    """Machine-checkable gate violation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class GateResult:
    """Outcome of process-change gate evaluation."""

    status: GateStatus
    code: str
    message: str
    revision_sha: str
    process_paths_touched: tuple[str, ...] = ()
    pcp_id: str | None = None
    classifier_minimum_tier: Tier = "UNKNOWN"
    lifecycle_labels: list[str] = field(default_factory=list)
    closure_status: str | None = None
    l3_report_path: str | None = None
    d3_trust_level: str | None = None


def repo_root() -> Path:
    return _active_root or _default_root


def set_repo_root(root: Path) -> None:
    """Override repository root for tests and subprocess invocations."""
    global _active_root
    _active_root = root.resolve()


def load_process_surfaces(root: Path) -> dict[str, Any]:
    path = resource_path(root, "opsx", "schemas", "process-surfaces-v0.json")
    if not path.is_file():
        raise ProcessChangeGateError("MISSING_MANIFEST", f"missing {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ProcessChangeGateError("INVALID_MANIFEST", "process surfaces manifest invalid")
    return doc


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        return normalized[2:]
    return normalized


def path_matches_globs(repo_path: str, globs: list[str]) -> bool:
    normalized = _normalize_path(repo_path)
    for pattern in globs:
        pat = _normalize_path(pattern)
        if fnmatch.fnmatch(normalized, pat):
            return True
    return False


def git_changed_files(base: str, head: str, root: Path) -> tuple[str, ...]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ProcessChangeGateError(
            "GIT_DIFF_FAILED",
            proc.stderr.strip() or "git diff failed",
        )
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def git_rev_parse(ref: str, root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ProcessChangeGateError(
            "GIT_REF_FAILED",
            proc.stderr.strip() or f"git rev-parse {ref} failed",
        )
    return proc.stdout.strip()


def commits_touching_paths(
    base: str, head: str, paths: set[str], root: Path
) -> list[dict[str, Any]]:
    """Return commits whose tree diff intersects ``paths`` (first-parent range)."""
    proc = subprocess.run(
        ["git", "log", f"{base}..{head}", "--format=%H%x09%s%x09%b", "--first-parent"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ProcessChangeGateError("GIT_LOG_FAILED", "git log failed")
    commits: list[dict[str, Any]] = []
    for block in proc.stdout.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        header = lines[0]
        parts = header.split("\t", 2)
        if len(parts) < 2:
            continue
        sha, subject = parts[0], parts[1]
        body = parts[2] if len(parts) > 2 else ""
        full_message = "\n".join([subject, body, *lines[1:]])
        file_proc = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        touched = {
            _normalize_path(p)
            for p in file_proc.stdout.splitlines()
            if p.strip()
        }
        if touched & paths:
            commits.append({"sha": sha, "message": full_message, "paths": sorted(touched & paths)})
    return commits


def filter_process_paths(
    changed: tuple[str, ...], manifest: dict[str, Any]
) -> tuple[str, ...]:
    globs = manifest.get("path_globs")
    if not isinstance(globs, list):
        raise ProcessChangeGateError("INVALID_MANIFEST", "path_globs missing")
    touched = [p for p in changed if path_matches_globs(p, [str(g) for g in globs])]
    return tuple(sorted(set(touched)))


def discover_ecr_lite_files(root: Path) -> list[Path]:
    base = resource_path(root, "opsx", "process-changes")
    if not base.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        candidate = child / "ecr-lite.json"
        if candidate.is_file():
            out.append(candidate)
    return out


def validate_ecr_lite_schema(doc: dict[str, Any]) -> None:
    """Minimal structural validation (no jsonschema dependency)."""
    required_root = [
        "schema_version",
        "pcp_id",
        "pcp_tier",
        "revision_sha",
        "why",
        "what_paths",
        "author_principal",
        "integration",
        "impact",
        "evidence_refs",
        "review",
        "required_tests",
    ]
    for key in required_root:
        if key not in doc:
            raise ProcessChangeGateError("ECR_SCHEMA", f"missing field {key}")
    if doc.get("schema_version") != ECR_SCHEMA_VERSION:
        raise ProcessChangeGateError("ECR_SCHEMA", "schema_version must be opsx-ecr-lite-v0")
    if doc.get("pcp_tier") != "LITE":
        raise ProcessChangeGateError("ECR_SCHEMA", "pcp_tier must be LITE for ecr-lite.json")
    pcp_id = doc.get("pcp_id")
    if not isinstance(pcp_id, str) or not PCP_ID_RE.match(pcp_id):
        raise ProcessChangeGateError("ECR_SCHEMA", "invalid pcp_id")
    revision = doc.get("revision_sha")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ProcessChangeGateError("ECR_SCHEMA", "invalid revision_sha")
    why = doc.get("why")
    if not isinstance(why, str) or not why.strip() or len(why) > 500:
        raise ProcessChangeGateError("ECR_SCHEMA", "why must be 1..500 chars")
    what_paths = doc.get("what_paths")
    if not isinstance(what_paths, list) or not what_paths:
        raise ProcessChangeGateError("ECR_SCHEMA", "what_paths required")
    promotion = doc.get("integration", {}).get("promotion_target")
    if promotion not in ("dev", "stage"):
        raise ProcessChangeGateError("ECR_SCHEMA", "integration.promotion_target invalid")
    closure = doc.get("impact", {}).get("closure")
    if not isinstance(closure, dict):
        raise ProcessChangeGateError("ECR_SCHEMA", "impact.closure required")
    if closure.get("status") not in ("PASS", "FAIL", "STALE_IDENTITY"):
        raise ProcessChangeGateError("ECR_SCHEMA", "impact.closure.status invalid")
    pred = closure.get("predicate_version")
    if pred != CLOSURE_PREDICATE_VERSION:
        raise ProcessChangeGateError(
            "ECR_SCHEMA",
            f"predicate_version must be {CLOSURE_PREDICATE_VERSION}",
        )
    required_tests = doc.get("required_tests")
    if not isinstance(required_tests, list) or not required_tests:
        raise ProcessChangeGateError("ECR_SCHEMA", "required_tests required")
    if not any(t.get("test_id") == "test_opsx_dependency_closure" for t in required_tests if isinstance(t, dict)):
        raise ProcessChangeGateError("ECR_SCHEMA", "required_tests must include test_opsx_dependency_closure")


def classify_minimum_tier(
    closure: dict[str, Any], process_paths: tuple[str, ...]
) -> Tier:
    if closure.get("status") != "PASS":
        return "UNKNOWN"
    for item in closure.get("global_failures") or []:
        if item.get("class_name") == "STALE_IDENTITY":
            return "UNKNOWN"
    if closure.get("command_blocks"):
        return "STANDARD"
    for path in process_paths:
        norm = _normalize_path(path)
        if norm.startswith("opsx/registry/"):
            return "STANDARD"
        if norm.startswith("opsx_tools/"):
            return "STANDARD"
        if "mutation_entrypoint" in norm or "engineering-adoption" in norm:
            return "STANDARD"
    return "LITE"


def commands_touched(process_paths: tuple[str, ...], manifest: dict[str, Any]) -> bool:
    prefix = str(manifest.get("commands_prefix", ".cursor/commands/"))
    return any(_normalize_path(p).startswith(prefix) for p in process_paths)


def _load_review_attestation(evidence_path: str, root: Path) -> dict[str, Any]:
    path = resource_path(root, evidence_path)
    if not path.is_file():
        raise ProcessChangeGateError(
            "REVIEW_EVIDENCE_MISSING",
            f"attestation evidence missing: {evidence_path}",
        )
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProcessChangeGateError(
            "REVIEW_EVIDENCE_INVALID",
            f"attestation JSON invalid: {evidence_path}: {exc}",
        ) from exc
    if not isinstance(doc, dict):
        raise ProcessChangeGateError("REVIEW_EVIDENCE_INVALID", "attestation root must be object")
    return doc


def validate_review_d3(
    ecr: dict[str, Any],
    commands_changed: bool,
    *,
    head_sha: str | None = None,
    require_revision_binding: bool = False,
) -> str:
    review = ecr.get("review")
    if not isinstance(review, dict):
        raise ProcessChangeGateError("REVIEW_MISSING", "review block required")
    waiver = review.get("waiver")
    if waiver is not None:
        if not isinstance(waiver, dict):
            raise ProcessChangeGateError("WAIVER_INVALID", "review.waiver invalid")
        policy_id = waiver.get("scoped_policy_id")
        if not policy_id:
            raise ProcessChangeGateError("WAIVER_INVALID", "scoped_policy_id required")
        if policy_id != WAIVER_POLICY_ID:
            raise ProcessChangeGateError("WAIVER_INVALID", f"unknown scoped_policy_id {policy_id}")
        policy_path = repo_root() / WAIVER_POLICY_REL
        if not policy_path.is_file():
            raise ProcessChangeGateError("WAIVER_POLICY_MISSING", "waiver policy file absent")
        body = policy_path.read_text(encoding="utf-8")
        if "waiver_authorized: true" not in body:
            raise ProcessChangeGateError("WAIVER_NOT_AUTHORIZED", "policy has no active waiver")
        if not waiver.get("waiver_owner_principal") or not waiver.get("reason"):
            raise ProcessChangeGateError("WAIVER_INVALID", "waiver owner and reason required")
    if not commands_changed:
        return "NOT_REQUIRED"
    attestations = review.get("attestations")
    if not isinstance(attestations, list) or not attestations:
        if waiver:
            return "WAIVER_STRUCTURAL"
        raise ProcessChangeGateError(
            "REVIEW_ATTESTATION_MISSING",
            "commands/** change requires review.attestations or authorized waiver",
        )
    author = str(ecr.get("author_principal", ""))
    pcp_id = str(ecr.get("pcp_id", ""))
    ecr_revision = str(ecr.get("revision_sha", ""))
    root = repo_root()
    bound_ok = False
    for att in attestations:
        if not isinstance(att, dict):
            continue
        reviewer = str(att.get("reviewer_principal", ""))
        if not reviewer:
            raise ProcessChangeGateError("REVIEW_ATTESTATION_INVALID", "reviewer_principal required")
        if reviewer == author:
            raise ProcessChangeGateError(
                "REVIEW_SELF_APPROVAL",
                "reviewer_principal must differ from author_principal (D3)",
            )
        evidence = att.get("evidence_path")
        if not evidence:
            raise ProcessChangeGateError("REVIEW_EVIDENCE_MISSING", "evidence_path required")
        doc = _load_review_attestation(str(evidence), root)
        if doc.get("schema_version") != ATTESTATION_SCHEMA_VERSION:
            raise ProcessChangeGateError(
                "REVIEW_EVIDENCE_INVALID",
                f"attestation schema_version must be {ATTESTATION_SCHEMA_VERSION}",
            )
        if pcp_id and doc.get("pcp_id") != pcp_id:
            raise ProcessChangeGateError(
                "REVIEW_EVIDENCE_MISMATCH",
                "attestation pcp_id does not match ECR",
            )
        if doc.get("reviewer_principal") != reviewer:
            raise ProcessChangeGateError(
                "REVIEW_EVIDENCE_MISMATCH",
                "attestation reviewer_principal does not match ECR attestation",
            )
        if doc.get("author_principal") != author:
            raise ProcessChangeGateError(
                "REVIEW_EVIDENCE_MISMATCH",
                "attestation author_principal does not match ECR",
            )
        bound_revision = doc.get("revision_sha")
        if isinstance(bound_revision, str) and re.fullmatch(r"[0-9a-f]{40}", bound_revision):
            if bound_revision != ecr_revision:
                raise ProcessChangeGateError(
                    "REVIEW_EVIDENCE_MISMATCH",
                    "attestation revision_sha does not match ECR revision_sha",
                )
            bound_ok = True
    if require_revision_binding and not bound_ok:
        raise ProcessChangeGateError(
            "REVIEW_ATTESTATION_UNVERIFIED",
            "D3 structural checks passed but attestation lacks revision_sha binding "
            "(independent review not cryptographically bound; AT-D3 unverified)",
        )
    return "REVISION_BOUND" if bound_ok else "STRUCTURAL_ONLY"


def validate_meta_commits(
    base: str,
    head: str,
    process_paths: tuple[str, ...],
    pcp_id: str,
    ecr_rel: str,
    root: Path,
) -> None:
    path_set = set(process_paths)
    commits = commits_touching_paths(base, head, path_set, root)
    for commit in commits:
        msg = commit["message"]
        if not META_COMMIT_PCP_RE.search(msg):
            raise ProcessChangeGateError(
                "META_COMMIT_PCP_MISSING",
                f"commit {commit['sha'][:8]} touches process paths without PCP-ID:",
            )
        if pcp_id not in msg:
            raise ProcessChangeGateError(
                "META_COMMIT_PCP_MISMATCH",
                f"commit {commit['sha'][:8]} missing PCP-ID {pcp_id}",
            )
        if not META_COMMIT_ECR_RE.search(msg) and ecr_rel not in msg:
            raise ProcessChangeGateError(
                "META_COMMIT_ECR_PATH_MISSING",
                f"commit {commit['sha'][:8]} missing ecr-lite.json path in message",
            )


def run_t1_pytest(root: Path) -> int:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            ".cursor/pipeline/control-plane-tests/test_opsx_dependency_closure.py",
            "-q",
        ],
        cwd=root,
        check=False,
    )
    return proc.returncode


def write_l3_report(
    root: Path,
    pcp_id: str,
    head_sha: str,
    closure: dict[str, Any],
    result: GateResult,
) -> Path:
    rel_dir = Path("opsx/runs/process-change-gate") / pcp_id
    out_dir = resource_path(root, rel_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": GATE_SCHEMA_VERSION,
        "pcp_id": pcp_id,
        "revision_sha": head_sha,
        "gate_status": result.status,
        "gate_code": result.code,
        "classifier_minimum_tier": result.classifier_minimum_tier,
        "closure_status": closure.get("status"),
        "closure_predicate_version": CLOSURE_PREDICATE_VERSION,
        "lifecycle_labels": result.lifecycle_labels,
        "process_paths_touched": list(result.process_paths_touched),
        "d3_trust_level": result.d3_trust_level,
    }
    out_path = out_dir / f"gate-report-{head_sha[:12]}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def _revision_sha_is_ancestor(claimed: str, head_sha: str, root: Path) -> bool:
    """Return True when ``claimed`` is an ancestor of ``head_sha`` in git history."""
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", claimed, head_sha],
        cwd=root,
        check=False,
    )
    return proc.returncode == 0


def _metadata_only_revision_delta(
    claimed: str,
    head_sha: str,
    pcp_id: str,
    root: Path,
) -> bool:
    """True when every path changed between ``claimed`` and HEAD is ECR metadata only.

    AT-2bis: tracked ``revision_sha`` cannot equal PR HEAD when ``ecr-lite.json`` is part
    of the commit graph (self-referential bind). Freshness is preserved by requiring that
    all substantive process-surface edits are contained in ``claimed`` and that only
    binding metadata (ECR, review attestation, CI L3 gate reports) changed afterward.
    """
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{claimed}..{head_sha}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False
    pcp_prefix = f"opsx/process-changes/{pcp_id}/"
    runs_prefix = "opsx/runs/"
    for raw in proc.stdout.splitlines():
        path = _normalize_path(raw.strip())
        if not path:
            continue
        if path.startswith(pcp_prefix) and path.endswith(("ecr-lite.json", "review-attestation.json")):
            continue
        if path.startswith(runs_prefix):
            continue
        return False
    return True


def validate_revision_sha_at_head(
    *,
    claimed: str,
    head_sha: str,
    root: Path,
    pcp_id: str,
    phase: Literal["ci_pr", "local"],
) -> None:
    """AT-2 / AT-2bis: bind ECR input revision to PR HEAD without self-referential SHA."""
    if claimed == head_sha:
        return
    if not _revision_sha_is_ancestor(claimed, head_sha, root):
        raise ProcessChangeGateError(
            "REVISION_SHA_MISMATCH",
            f"revision_sha {claimed} is not HEAD nor ancestor of HEAD (AT-2)",
        )
    if phase == "ci_pr" and not _metadata_only_revision_delta(claimed, head_sha, pcp_id, root):
        raise ProcessChangeGateError(
            "REVISION_SHA_STALE",
            f"revision_sha {claimed} is ancestor of HEAD but process surfaces changed "
            f"after binding commit (AT-2bis freshness)",
        )


def select_ecr_for_change(
    root: Path,
    process_paths: tuple[str, ...],
    changed_all: tuple[str, ...],
) -> Path:
    """Pick the ECR file for this PR (exactly one active PCP directory)."""
    if not process_paths:
        raise ProcessChangeGateError("ECR_MISSING", "no process paths in diff")
    ecr_files = discover_ecr_lite_files(root)
    if not ecr_files:
        raise ProcessChangeGateError(
            "ECR_MISSING",
            "process-surface diff without opsx/process-changes/<pcp-id>/ecr-lite.json (AT-1)",
        )
    normalized_changed = {_normalize_path(c) for c in changed_all}
    ecr_in_diff = [
        p
        for p in ecr_files
        if _normalize_path(str(p.relative_to(root))) in normalized_changed
    ]
    if not ecr_in_diff:
        raise ProcessChangeGateError(
            "ECR_MISSING",
            "process-surface diff without ecr-lite.json change in PR (AT-1)",
        )
    if len(ecr_in_diff) > 1:
        raise ProcessChangeGateError(
            "ECR_AMBIGUOUS",
            "multiple ecr-lite.json files changed; one PCP per PR",
        )
    return ecr_in_diff[0]


def evaluate_gate(
    *,
    base: str,
    head: str,
    root: Path | None = None,
    run_pytest: bool = True,
    check_meta_commits: bool = True,
    enforce_revision_sha: bool = True,
    phase: Literal["ci_pr", "local"] = "ci_pr",
) -> GateResult:
    base_path = root or repo_root()
    manifest = load_process_surfaces(base_path)
    head_sha = git_rev_parse(head, base_path)
    changed_all = git_changed_files(base, head, base_path)
    process_paths = filter_process_paths(changed_all, manifest)

    if not process_paths:
        return GateResult(
            status="NOT_APPLICABLE",
            code="PROCESS_CHANGE_NOT_APPLICABLE",
            message="no process surfaces in diff",
            revision_sha=head_sha,
        )

    try:
        ecr_path = select_ecr_for_change(base_path, process_paths, changed_all)
    except ProcessChangeGateError:
        raise
    ecr_rel = _normalize_path(str(ecr_path.relative_to(base_path)))
    ecr = json.loads(ecr_path.read_text(encoding="utf-8"))
    if not isinstance(ecr, dict):
        raise ProcessChangeGateError("ECR_INVALID", "ecr-lite root must be object")
    validate_ecr_lite_schema(ecr)
    pcp_id = str(ecr["pcp_id"])
    expected_dir = base_path / "opsx" / "process-changes" / pcp_id
    if ecr_path.parent != expected_dir:
        raise ProcessChangeGateError(
            "ECR_PATH_MISMATCH",
            "ecr-lite.json directory must match pcp_id",
        )
    if enforce_revision_sha:
        validate_revision_sha_at_head(
            claimed=str(ecr["revision_sha"]),
            head_sha=head_sha,
            root=base_path,
            pcp_id=pcp_id,
            phase=phase,
        )

    closure = evaluate_dependency_closure(base_path)
    result_closure_status = closure.get("status")
    claimed = ecr.get("impact", {}).get("closure", {}).get("status")
    if claimed == "PASS" and result_closure_status != "PASS":
        raise ProcessChangeGateError(
            "CLOSURE_CLAIM_MISMATCH",
            "ECR claims closure PASS but L3 closure FAIL (AT-3)",
        )
    if result_closure_status != "PASS":
        raise ProcessChangeGateError(
            "CLOSURE_FAIL",
            f"evaluate_dependency_closure status={result_closure_status} (AT-3)",
        )
    for item in closure.get("global_failures") or []:
        if item.get("class_name") == "STALE_IDENTITY":
            raise ProcessChangeGateError("CLOSURE_STALE_IDENTITY", "STALE_IDENTITY in closure")

    minimum_tier = classify_minimum_tier(closure, process_paths)
    if minimum_tier != "LITE":
        raise ProcessChangeGateError(
            "TIER_TOO_HIGH_FOR_LITE",
            f"classifier minimum tier {minimum_tier}; ecr-lite.json insufficient",
        )

    cmds = commands_touched(process_paths, manifest)
    d3_trust = validate_review_d3(
        ecr,
        cmds,
        head_sha=head_sha,
        require_revision_binding=phase == "ci_pr" and cmds,
    )

    if check_meta_commits and phase == "ci_pr":
        validate_meta_commits(base, head, process_paths, pcp_id, ecr_rel, base_path)

    if run_pytest:
        rc = run_t1_pytest(base_path)
        if rc != 0:
            raise ProcessChangeGateError(
                "T1_PYTEST_FAIL",
                "test_opsx_dependency_closure did not pass (G4-min)",
            )

    labels = ["CAPABILITY_WIRED"]
    if phase == "ci_pr":
        labels = ["CAPABILITY_WIRED"]

    gate = GateResult(
        status="PASS",
        code="PROCESS_CHANGE_GATE_PASS",
        message=f"process change gate PASS for {pcp_id}",
        revision_sha=head_sha,
        process_paths_touched=process_paths,
        pcp_id=pcp_id,
        classifier_minimum_tier=minimum_tier,
        lifecycle_labels=labels,
        closure_status=result_closure_status,
        d3_trust_level=d3_trust,
    )
    report_path = write_l3_report(base_path, pcp_id, head_sha, closure, gate)
    gate.l3_report_path = _normalize_path(str(report_path.relative_to(base_path)))
    return gate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OPSX process-change gate (ECR-LITE V0)")
    parser.add_argument(
        "command",
        choices=["ci-gate", "evaluate", "bind-revision", "write-report"],
    )
    parser.add_argument("--base", default="origin/dev")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-meta-commit-check", action="store_true")
    parser.add_argument(
        "--root",
        default=None,
        help="Repository root (default: git toplevel from cwd)",
    )
    args = parser.parse_args(argv)
    if args.root:
        set_repo_root(Path(args.root))
    else:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            set_repo_root(Path(proc.stdout.strip()))
    root = repo_root()
    try:
        if args.command == "evaluate":
            result = evaluate_gate(
                base=args.base,
                head=args.head,
                root=root,
                run_pytest=not args.skip_pytest,
                check_meta_commits=not args.skip_meta_commit_check,
            )
            print(json.dumps(result.__dict__, indent=2, ensure_ascii=False))
            return 0 if result.status in ("PASS", "NOT_APPLICABLE") else 1
        if args.command == "bind-revision":
            ecr_files = discover_ecr_lite_files(root)
            if not ecr_files:
                raise ProcessChangeGateError("ECR_MISSING", "no ecr-lite.json to bind")
            head_sha = git_rev_parse("HEAD", root)
            for path in ecr_files:
                doc = json.loads(path.read_text(encoding="utf-8"))
                doc["revision_sha"] = head_sha
                path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(json.dumps({"revision_sha": head_sha, "updated": len(ecr_files)}, indent=2))
            return 0
        if args.command == "ci-gate":
            result = evaluate_gate(
                base=args.base,
                head=args.head,
                root=root,
                run_pytest=not args.skip_pytest,
                check_meta_commits=not args.skip_meta_commit_check,
                phase="ci_pr",
            )
            print(json.dumps(result.__dict__, indent=2, ensure_ascii=False))
            return 0 if result.status in ("PASS", "NOT_APPLICABLE") else 1
        raise ProcessChangeGateError("UNSUPPORTED", args.command)
    except ProcessChangeGateError as exc:
        payload = {"status": "FAIL", "code": exc.code, "message": exc.message}
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
