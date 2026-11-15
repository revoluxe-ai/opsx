"""Recover independently proved scenario rows from a finished historical SDK run.

This is an explicit, fail-closed migration, not a shortcut from transcript prose
to PASS. It proves the original source digest against a pinned Git tree, checks
that normative current sources did not change, independently validates every
SUPPORTED proof, and writes one atomic receipt per row. Mixed FAIL/BLOCKED
aggregate output never authorizes an aggregate PASS.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import ast
import fnmatch
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from agents.common.scenario_files import read_scenario_ids
from agents.verify.scenario_coverage_verify.delta_rows import ROW_CONTRACT, impact_scope
from agents.verify.scenario_coverage_verify.evidence_contract import (
    _MAX_BYTES, _MAX_FILES, _SOURCE_GROUPS, _legacy_paths, input_snapshot,
    legacy_source_digest, scenario_verifier_contract_digest, source_paths, trace_mappings,
)
from agents.verify.scenario_coverage_verify.row_receipts import (
    row_key, save_supported_row, write_observation,
)
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageGap, ScenarioCoverageRowReview, ScenarioCoverageVerifyAnswer,
)
from agents.verify.scenario_coverage_verify.row_single import verify_single_answer


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True,
    )
    return result.stdout


def _is_source_path(name: str, change_id: str) -> bool:
    # Match evidence_contract.source_paths() exactly. Live snapshots exclude
    # tests/opsx/**; including those files here falsely invalidates pinned
    # historical source_digest for an otherwise unchanged Git tree.
    if name.startswith("tests/opsx/"):
        return False
    prefix = f"openspec/changes/{change_id}/"
    machine_prefix = f"opsx/receipts/{change_id}/scenario-coverage/"
    if name in {
        prefix + "scenario-coverage/plan.md",
        machine_prefix + "plan.receipt.json",
        machine_prefix + "test_trace_matrix.json",
    }:
        return True
    if name.startswith(prefix + "specs/") and name.endswith(".md"):
        return True
    return any(
        name.startswith(folder + "/")
        and fnmatch.fnmatchcase(PurePosixPath(name).name, pattern)
        for folder, pattern in _SOURCE_GROUPS
    )


def _semantic_path(name: str, change_id: str) -> bool:
    return (
        name.startswith(("tests/", "src/stroy/", "migrations/", "docs/"))
        or name.startswith(f"openspec/changes/{change_id}/specs/")
        or name in {
            f"openspec/changes/{change_id}/scenario-coverage/plan.md",
            f"opsx/receipts/{change_id}/scenario-coverage/plan.receipt.json",
            f"opsx/receipts/{change_id}/scenario-coverage/test_trace_matrix.json",
        }
    )


def source_digest_at_git_ref(root: Path, ref: str, change_id: str) -> tuple[str, set[str]]:
    """Recompute original scenario_source_v2 bytes from a pinned Git tree."""
    sha = _git(root, "rev-parse", "--verify", ref + "^{commit}").decode().strip()
    if len(sha) != 40 or sha != ref:
        raise ValueError("RECOVERY_ANCHOR_MUST_BE_FULL_SHA")
    names = [
        p.decode("utf-8") for p in _git(root, "ls-tree", "-r", "-z", "--name-only", sha)
        .split(b"\0") if p
    ]
    selected = {p for p in names if _is_source_path(p, change_id)}
    if not selected or len(selected) > _MAX_FILES:
        raise ValueError("RECOVERY_SOURCE_SET_INVALID")
    roots = [
        "tests", "src/stroy", "migrations", "docs",
        "opsx/opsx_runtime/agents/verify/scenario_coverage_verify",
        "opsx/opsx_runtime/agents/common",
        "opsx/opsx_runtime/agents/implementation/scenario_trace_materializer",
        "opsx/opsx_runtime/agents/implementation/apply",
        "opsx/opsx_runtime/workflows/verify",
        f"openspec/changes/{change_id}/scenario-coverage",
        f"openspec/changes/{change_id}/specs",
    ]
    present = [
        folder for folder in roots
        if any(p.startswith(folder + "/") for p in names)
    ]
    proc = subprocess.Popen(
        ["git", "archive", "--format=tar", sha, *sorted(present)],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.stdout is None:
        raise ValueError("RECOVERY_ARCHIVE_UNAVAILABLE")
    digest = hashlib.sha256(b"opsx-scenario-source-v2\0")
    previous = ""
    seen: set[str] = set()
    bytes_read = 0
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for item in tar:
                name = item.name.rstrip("/")
                if name not in selected:
                    continue
                if not item.isfile() or name <= previous or name in seen:
                    raise ValueError("RECOVERY_ARCHIVE_IDENTITY_INVALID")
                previous = name
                seen.add(name)
                if item.size < 0 or bytes_read + item.size > _MAX_BYTES:
                    raise ValueError("RECOVERY_ARCHIVE_TOO_LARGE")
                digest.update(name.encode("utf-8") + b"\0")
                stream = tar.extractfile(item)
                if stream is None:
                    raise ValueError("RECOVERY_ARCHIVE_MEMBER_MISSING")
                total = 0
                while chunk := stream.read(65536):
                    total += len(chunk)
                    bytes_read += len(chunk)
                    digest.update(chunk)
                if total != item.size:
                    raise ValueError("RECOVERY_ARCHIVE_TRUNCATED")
                digest.update(b"\0")
        if seen != selected:
            raise ValueError("RECOVERY_ARCHIVE_SOURCE_SET_MISMATCH")
    finally:
        proc.stdout.close()
        proc.wait()
    if proc.returncode != 0:
        raise ValueError("RECOVERY_GIT_ARCHIVE_FAILED")
    return digest.hexdigest(), selected



def source_digest_v3_at_git_ref(
    root: Path, ref: str, change_id: str,
) -> tuple[str, set[str]]:
    """Check the current change-scoped inventory against an immutable Git tree.

    The current inventory includes mapped tests and their static dependencies.
    Requiring each selected path at the pinned revision prevents untracked or
    newly added relevant files from becoming unproved historical evidence.
    """
    sha = _git(root, "rev-parse", "--verify", ref + "^{commit}").decode().strip()
    if len(sha) != 40 or sha != ref:
        raise ValueError("RECOVERY_ANCHOR_MUST_BE_FULL_SHA")
    selected = {
        path.relative_to(root).as_posix()
        for path in source_paths(root, change_id)
    }
    if not selected or len(selected) > _MAX_FILES:
        raise ValueError("RECOVERY_SOURCE_SET_INVALID")
    digest = hashlib.sha256(b"opsx-scenario-source-v3\0")
    size = 0
    for rel in sorted(selected):
        if PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
            raise ValueError("RECOVERY_SOURCE_PATH_UNSAFE")
        try:
            # The captured v3 source_digest hashes WORKTREE bytes. Git blobs
            # may contain LF where Git checkout expands to CRLF on Windows.
            # Reconstitute the exact checkout representation for this pinned
            # blob rather than comparing different byte domains.
            attrs = _git(root, "check-attr", "filter", "working-tree-encoding", "--", rel)
            attr_lines = attrs.decode("utf-8").splitlines()
            if len(attr_lines) != 2 or any(
                not line.endswith((": unspecified", ": unset"))
                for line in attr_lines
            ):
                # git cat-file --filters can invoke arbitrary external smudge
                # filters; refuse such paths rather than executing them during
                # read-only recovery. Unknown encodings also fail closed.
                raise ValueError(f"RECOVERY_V3_EXTERNAL_FILTER_UNSAFE: {rel}")
            original = _git(root, "cat-file", "--filters", f"{sha}:{rel}")
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"RECOVERY_V3_ANCHOR_PATH_MISSING: {rel}") from exc
        size += len(original)
        if size > _MAX_BYTES:
            raise ValueError("RECOVERY_SOURCE_TOO_LARGE")
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(original)
        digest.update(b"\0")
    return digest.hexdigest(), selected


def _same_function(
    root: Path, ref: str, path: str, name: str,
    *, current_path: str | None = None,
) -> bool:
    old = _git(root, "show", f"{ref}:{path}").decode("utf-8")
    new = (resource_path(root, current_path or path)).read_text(encoding="utf-8")
    def segment(content: str) -> str:
        tree = ast.parse(content)
        funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        if len(funcs) != 1:
            raise ValueError("RECOVERY_POLICY_FUNCTION_UNBOUND")
        return ast.dump(funcs[0], include_attributes=False)
    try:
        old_segment = segment(old)
    except ValueError:
        if name not in (
            "review_proof_error", "spec_clauses", "trace_mappings",
            "verify_single_answer",
        ):
            raise
        # On the v3 anchor, these functions were extracted from
        # evidence_contract.py into review_semantics.py unchanged.
        semantic_path = "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/review_semantics.py"
        old_segment = segment(_git(root, "show", f"{ref}:{semantic_path}").decode("utf-8"))
    return old_segment == segment(new)


def prove_recovery_freshness(
    root: Path,
    *,
    change_id: str,
    original_digest: str,
    anchor: str,
) -> None:
    """Confirm v2 or v3 revision identity; never upgrade by changed hash alone."""
    v2_digest, v2_paths = source_digest_at_git_ref(root, anchor, change_id)
    v3_scope: set[str] | None = None
    if original_digest == v2_digest:
        anchored_paths = v2_paths
        current = _legacy_paths(root, resource_path(root, "openspec", "changes", change_id))
        pathspec = [
            "tests", "src/stroy", "migrations", "docs",
            f"openspec/changes/{change_id}/specs",
            f"openspec/changes/{change_id}/scenario-coverage",
        ]
    else:
        v3_digest, v3_paths = source_digest_v3_at_git_ref(root, anchor, change_id)
        if original_digest != v3_digest:
            raise ValueError("RECOVERY_ORIGINAL_DIGEST_UNPROVEN")
        anchored_paths = v3_paths
        current = source_paths(root, change_id)
        v3_scope = v3_paths
        # Avoid the Windows command-line limit for thousands of file args.
        # Broad inventory is filtered to exact v3 scoped paths below.
        pathspec = [
            "tests", "src/stroy", "migrations",
            f"openspec/changes/{change_id}/specs",
            f"openspec/changes/{change_id}/scenario-coverage",
        ]
    if input_snapshot(root, change_id)["source_digest"] != original_digest:
        if legacy_source_digest(root, change_id) != original_digest:
            raise ValueError("RECOVERY_CURRENT_SOURCE_STALE")
    current_semantic = {
        p.relative_to(root).as_posix()
        for p in current if _semantic_path(p.relative_to(root).as_posix(), change_id)
    }
    previous_semantic = {p for p in anchored_paths if _semantic_path(p, change_id)}
    if current_semantic != previous_semantic:
        raise ValueError("RECOVERY_SEMANTIC_FILE_MEMBERSHIP_CHANGED")
    if any((resource_path(root, p)).is_symlink() for p in current_semantic):
        raise ValueError("RECOVERY_SEMANTIC_SYMLINK")
    changed = set(_git(root, "diff", "--name-only", anchor, "--", *pathspec).decode().splitlines())
    untracked = set(_git(root, "ls-files", "--others", "--exclude-standard", "--", *pathspec).decode().splitlines())
    if v3_scope is not None:
        # A foreign/unused test file is not an input of this change.
        changed &= v3_scope
        untracked &= v3_scope
    if changed:
        raise ValueError("RECOVERY_NORMATIVE_SOURCE_CHANGED")
    if untracked:
        raise ValueError("RECOVERY_UNTRACKED_NORMATIVE_SOURCE")
    policy_checks = [
        ("opsx/opsx_runtime/agents/verify/scenario_coverage_verify/prompt/get_prompt.py", "get_prompt"),
        ("opsx/opsx_runtime/agents/verify/scenario_coverage_verify/evidence_contract.py", "review_proof_error"),
        ("opsx/opsx_runtime/agents/verify/scenario_coverage_verify/evidence_contract.py", "spec_clauses"),
        ("opsx/opsx_runtime/agents/verify/scenario_coverage_verify/evidence_contract.py", "trace_mappings"),
        ("opsx/opsx_runtime/agents/verify/scenario_coverage_verify/row_single.py", "verify_single_answer"),
        ("opsx/opsx_runtime/agents/verify/scenario_coverage_verify/semantic_validation.py", "_audit_proof_error"),
    ]
    moved = {"review_proof_error", "spec_clauses", "trace_mappings", "verify_single_answer"}
    for path, fn in policy_checks:
        current_path = (
            "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/review_semantics.py"
            if fn in moved else path
        )
        if not _same_function(root, anchor, path, fn, current_path=current_path):
            raise ValueError(f"RECOVERY_REVIEW_POLICY_CHANGED: {path}:{fn}")
    # Parser rules moved together with the reviewer functions. Their literal
    # patterns are normative and must not drift across a recovered run.
    semantic_path = "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/review_semantics.py"
    old_path = (
        semantic_path if original_digest != v2_digest
        else "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/evidence_contract.py"
    )
    def parser_constants(data: bytes) -> dict[str, str]:
        tree = ast.parse(data.decode("utf-8"))
        found: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in ("_SCENARIO_HEADING", "_CLAUSE")
                for t in node.targets
            ):
                found[node.targets[0].id] = ast.dump(node.value, include_attributes=False)
        return found
    old_rules = parser_constants(_git(root, "show", f"{anchor}:{old_path}"))
    current_rules = parser_constants((resource_path(root, semantic_path)).read_bytes())
    if set(old_rules) != {"_SCENARIO_HEADING", "_CLAUSE"} or old_rules != current_rules:
        raise ValueError("RECOVERY_REVIEW_PARSER_CHANGED")
    for path in (
        "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/schemas.py",
        "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/input_pack.py",
    ):
        if _git(root, "show", f"{anchor}:{path}") != (resource_path(root, path)).read_bytes():
            raise ValueError(f"RECOVERY_SCHEMA_OR_PLAN_LOADER_CHANGED: {path}")


def _final_response(path: Path) -> dict[str, Any]:
    contents = path.read_bytes()
    turns = json.loads(contents)
    if not isinstance(turns, list):
        raise ValueError("RECOVERY_CONVERSATION_INVALID")
    messages = [
        step["message"]["text"] for turn in turns
        if isinstance(turn, dict) for step in (turn.get("turn") or {}).get("steps", [])
        if isinstance(step, dict) and step.get("type") == "assistantMessage"
        and isinstance(step.get("message"), dict)
        and isinstance(step["message"].get("text"), str)
    ]
    if not messages:
        raise ValueError("RECOVERY_CONVERSATION_HAS_NO_REPLY")
    answer = json.loads(messages[-1])
    if not isinstance(answer, dict) or not isinstance(answer.get("row_reviews"), list):
        raise ValueError("RECOVERY_FINAL_REPLY_INVALID")
    return answer


def recover(
    *,
    root: Path,
    change_id: str,
    anchor: str,
    conversation_path: Path,
    meta_path: Path,
    write: bool,
    model_options: list[Any] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    if conversation_path.is_symlink() or meta_path.is_symlink():
        raise ValueError("RECOVERY_SYMLINK_ARTIFACT_FORBIDDEN")
    conversation_path = conversation_path.resolve()
    meta_path = meta_path.resolve()
    if not conversation_path.is_relative_to(root) or not meta_path.is_relative_to(root):
        raise ValueError("RECOVERY_EXTERNAL_ARTIFACT_FORBIDDEN")
    colocated_pair = (
        conversation_path.parent == meta_path.parent
        and conversation_path.name == "conversation.json"
        and meta_path.name == "meta.json"
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if (
        meta.get("sdk_status") != "finished"
        or meta.get("agent") != "scenario_coverage_verify"
        or meta.get("change_id") != change_id
        or not isinstance(meta.get("run_uuid"), str)
        or not meta["run_uuid"].strip()
        or not isinstance(meta.get("sdk_run_id"), str)
        or not meta["sdk_run_id"].strip()
        or not isinstance(meta.get("agent_id"), str)
        or not meta["agent_id"].strip()
    ):
        raise ValueError("RECOVERY_SDK_TERMINAL_EVIDENCE_MISSING")
    if not colocated_pair:
        # Modern serial executions store stream conversations separately from
        # canonical SDK run metadata. Bind the exact execution + SDK run and
        # row-keyed filename; a mere suffix or guessed metadata is insufficient.
        execution_id = meta["run_uuid"]
        sdk_run_id = meta["sdk_run_id"]
        metadata_root = (
            resource_path(root, "opsx", "runs", "dialogs", change_id, execution_id, "scenario_coverage_verify")
        )
        allowed_meta = (
            meta_path == metadata_root / "meta.json"
            or meta_path == metadata_root / "revisions" / sdk_run_id / "meta.json"
        )
        expected_parents = {
            (
                resource_path(root, "openspec", "changes", change_id, "verify", "scenario-row-streams", execution_id)
            ).resolve(),
            (
                resource_path(root, "opsx", "logs", change_id, "legacy", "scenario-row-streams", execution_id)
            ).resolve(),
            metadata_root.resolve(),
            (metadata_root / "revisions" / sdk_run_id).resolve(),
        }
        legacy_path = meta.get("legacy_conversation_json")
        expected_rel = conversation_path.relative_to(root).as_posix()
        if (
            not allowed_meta
            or conversation_path.parent.resolve() not in expected_parents
            or not isinstance(legacy_path, str)
            or not legacy_path.replace("\\", "/").endswith("/" + expected_rel)
        ):
            raise ValueError("RECOVERY_RUN_ARTIFACT_PAIR_MISMATCH")
    answer = _final_response(conversation_path)
    if not colocated_pair:
        raw_rows = answer.get("row_reviews")
        if (
            not isinstance(raw_rows, list) or len(raw_rows) != 1
            or not isinstance(raw_rows[0], dict)
            or conversation_path.name != row_key(raw_rows[0].get("scenario_id")) + ".conversation.json"
        ):
            raise ValueError("RECOVERY_SCENARIO_STREAM_IDENTITY_MISMATCH")
    from workflows.artifact_paths import scenario_machine_receipt_path

    plan = resource_path(root, "openspec", "changes", change_id, "scenario-coverage")
    receipt_path = scenario_machine_receipt_path(root, change_id, "plan.receipt.json")
    plan_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    plan_id = plan_receipt.get("PLAN_EVIDENCE_ID")
    plan_ids = read_scenario_ids(plan, allow_historical=True)
    mapping = trace_mappings(root, change_id)
    if (
        not isinstance(plan_id, str) or not plan_id
        or answer.get("plan_evidence_id") != plan_id
        or not plan_ids or len(set(plan_ids)) != len(plan_ids)
        or set(mapping) != set(plan_ids)
    ):
        raise ValueError("RECOVERY_PLAN_IDENTITY_MISMATCH")
    rows = [
        ScenarioCoverageRowReview.model_validate(item)
        for item in answer["row_reviews"]
    ]
    if len(rows) == 1:
        # Modern serial SDK runs contain exactly one independently attributable
        # scenario. Require full schema/identity validation, not a 56-row batch.
        try:
            reviewed = ScenarioCoverageVerifyAnswer.model_validate(answer)
            sid = rows[0].scenario_id
            verify_single_answer(
                answer=reviewed, scenario_id=sid, mapped_node=mapping[sid],
                plan_id=plan_id, source_digest=answer.get("source_digest"),
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("RECOVERY_SINGLE_ROW_INVALID") from exc
    elif [r.scenario_id for r in rows] != plan_ids:
        raise ValueError("RECOVERY_PLAN_ROWS_INCOMPLETE")
    if any(
        row.test_node_id and row.test_node_id != mapping[row.scenario_id]
        for row in rows
    ):
        raise ValueError("RECOVERY_TRACE_MAPPING_CHANGED")
    original_digest = answer.get("source_digest")
    if not isinstance(original_digest, str) or len(original_digest) != 64:
        raise ValueError("RECOVERY_SOURCE_DIGEST_MISSING")
    prove_recovery_freshness(
        root, change_id=change_id,
        original_digest=original_digest, anchor=anchor,
    )
    before = input_snapshot(root, change_id)
    # A validated historical v2 answer is not stale merely because the
    # current change-scoped snapshot uses the v3 hash namespace. Original v2
    # bytes were independently proved against the pinned Git archive above.
    if (
        before["source_digest"] != original_digest
        and legacy_source_digest(root, change_id) != original_digest
    ):
        raise ValueError("RECOVERY_SOURCE_STALE")
    model = {"id": meta.get("model_id"), "options": model_options if model_options is not None else []}
    if not isinstance(model["id"], str) or not model["id"]:
        raise ValueError("RECOVERY_MODEL_UNBOUND")
    scope = impact_scope(
        root, source_paths(root, change_id), mapping,
        authority={
            "model": model, "plan_evidence_id": plan_id,
            "verifier_contract_digest": scenario_verifier_contract_digest(root, model),
        },
    )
    from agents.verify.scenario_coverage_verify import row_receipts
    rejected_proofs: dict[str, str] = {}
    for row in rows:
        if row.assessment == "SUPPORTED":
            reason = row_receipts.review_proof_error(
                root, change_id, row, expected_node=mapping[row.scenario_id],
            )
            if reason is not None:
                rejected_proofs[row.scenario_id] = str(reason)
            elif not scope["row_digests"].get(row.scenario_id):
                rejected_proofs[row.scenario_id] = "ROW_INPUT_UNBOUND"
    after = input_snapshot(root, change_id)
    if before["source_digest"] != after["source_digest"]:
        raise ValueError("RECOVERY_SOURCE_CHANGED_DURING_VALIDATION")
    counts = {"SUPPORTED": 0, "GAP": 0, "INSPECTION_BLOCKED": 0, "CONTRACT_CONFLICT": 0}
    written = {"observations": 0, "supported_receipts": 0}
    evidence_id = f"{meta['sdk_run_id']}:{meta['agent_id']}"
    artifact_digest = hashlib.sha256(conversation_path.read_bytes()).hexdigest()
    for row in rows:
        counts[row.assessment] += 1
        if row.scenario_id in rejected_proofs:
            # One bad historical proof must not destroy 30 independently
            # valid sibling proofs. This row remains pending and has no PASS.
            continue
        if not write:
            continue
        digest = scope["row_digests"].get(row.scenario_id)
        matching_gaps = [
            ScenarioCoverageGap.model_validate(item)
            for item in answer.get("gaps", [])
            if item.get("scenario_id") == row.scenario_id
        ]
        write_observation(
            root, change_id=change_id, row=row,
            row_digest=digest, source_digest=before["source_digest"],
            plan_evidence_id=plan_id, origin_execution_id=meta["run_uuid"],
            evidence_id=evidence_id, source_artifact_digest=artifact_digest,
            recovery_review_mode=(
                "SINGLE_ROW_VALIDATED" if len(rows) == 1 else "BATCH_DIAGNOSTIC_ONLY"
            ),
            row_identity_contract=ROW_CONTRACT if len(rows) == 1 else "",
            # Multi-row legacy replies were never validated as independent
            # terminal decisions. They remain diagnostic observations; only
            # the strict single-row response has reusable negative authority.
            shared_digest=scope["shared_digest"] if len(rows) == 1 else None,
            gaps=matching_gaps,
            problem=(
                "; ".join(f"{g.gap_class}: {g.evidence}" for g in matching_gaps)
                if row.assessment == "GAP"
                else str(answer.get("block_reason") or "")
                if row.assessment in ("INSPECTION_BLOCKED", "CONTRACT_CONFLICT")
                else ""
            ),
            gap_class=matching_gaps[0].gap_class if matching_gaps else "",
        )
        written["observations"] += 1
        if row.assessment == "SUPPORTED":
            if not save_supported_row(
                root, change_id=change_id, row=row, mapping=mapping,
                row_digest=digest, plan_evidence_id=plan_id,
                origin_execution_id=meta["run_uuid"], evidence_id=evidence_id,
                row_identity_contract=ROW_CONTRACT if len(rows) == 1 else "",
            ):
                # The observation may be committed, but the positive proof is
                # missing. This is an explicit recovery failure, never success.
                raise ValueError("RECOVERY_PASS_RECEIPT_NOT_PERSISTED")
            written["supported_receipts"] += 1
    return {
        "status": (
            "RECOVERED_WITH_REJECTIONS" if write and rejected_proofs
            else "RECOVERED" if write else "DRY_RUN"
        ),
        "original_execution_id": meta["run_uuid"],
        "sdk_evidence_id": evidence_id,
        "plan_evidence_id": plan_id,
        "source_digest": before["source_digest"],
        "row_count": len(rows),
        "review_mode": "single" if len(rows) == 1 else "batch",
        "counts": counts,
        "rejected_supported_proofs": rejected_proofs,
        "eligible_supported_receipts": counts["SUPPORTED"] - len(rejected_proofs),
        "written": written,
        "sdk_calls": 0,
        "aggregate_verdict": "NOT_ASSERTED",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--change", required=True)
    parser.add_argument("--anchor", required=True, help="Full 40-character Git revision at original audit")
    parser.add_argument("--conversation", type=Path, required=True)
    parser.add_argument("--meta", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = args.repo.resolve()
    def resolve(p: Path) -> Path:
        return p if p.is_absolute() else resource_path(root, p)
    result = recover(
        root=root, change_id=args.change, anchor=args.anchor,
        conversation_path=resolve(args.conversation),
        meta_path=resolve(args.meta), write=args.write,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
