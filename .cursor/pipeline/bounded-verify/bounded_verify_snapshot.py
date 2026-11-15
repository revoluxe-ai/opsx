# -*- coding: utf-8 -*-
"""Identity helpers для implementation_snapshot (v1 tasks digest + v2 role digests).

Используется при freeze/draft и в ``schedule_write`` для drift detection.
Не пишет plan/ledger.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from bounded_verify_input_manifest import (
    collect_input_manifest,
    manifest_paths_to_files,
    role_digest_with_tombstones,
)
from bounded_verify_split_apply import split_apply_green_identity
from bounded_verify_git import git_head
from bounded_verify_impl_completion import implementation_completion_digest
from bounded_verify_plan import change_dir, BoundedVerifyPlanError

REPO_ROOT = Path(__file__).resolve().parents[3]

PLAN_SCHEMA_V1 = "bounded-verify-plan-v1"
PLAN_SCHEMA_V2 = "bounded-verify-plan-v2"

_TASK_CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*", re.MULTILINE)


def task_identity_digest(tasks_text: str) -> str:
    """Digest task lines with checkbox marks stripped (checkbox-only edits do not move)."""
    normalized_lines: list[str] = []
    for line in tasks_text.splitlines():
        stripped = _TASK_CHECKBOX_RE.sub("- ", line)
        normalized_lines.append(stripped.rstrip())
    body = "\n".join(normalized_lines).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _role_digest_for_paths(paths: list[Path], *, root: Path) -> tuple[str, list[str]]:
    ordered = sorted(paths, key=lambda p: str(p))
    if not ordered:
        return hashlib.sha256(b"").hexdigest(), []
    rel_list: list[str] = []
    for p in ordered:
        try:
            rel_list.append(str(p.relative_to(root)))
        except ValueError:
            rel_list.append(str(p))
    combined = "\n".join(f"{rel}:{_hash_file(p)}" for rel, p in zip(rel_list, ordered)).encode(
        "utf-8"
    )
    return hashlib.sha256(combined).hexdigest(), rel_list


def collect_v2_role_digests(change_id: str, *, root: Path = REPO_ROOT) -> dict[str, Any]:
    """Собирает role digests для v2 snapshot (capabilities-aligned manifest)."""
    cdir = change_dir(change_id, root=root)
    if not cdir.is_dir():
        raise BoundedVerifyPlanError("CHANGE_NOT_FOUND", f"change not found: {change_id}")

    spec_paths: list[Path] = []
    for name in ("proposal.md", "design.md", "owner-contract.md"):
        p = cdir / name
        if p.is_file():
            spec_paths.append(p)
    specs_dir = cdir / "specs"
    if specs_dir.is_dir():
        spec_paths.extend(sorted(specs_dir.rglob("*.md")))

    tasks_path = cdir / "tasks.md"
    tasks_text = tasks_path.read_text(encoding="utf-8") if tasks_path.is_file() else ""

    specs_digest, spec_list = _role_digest_for_paths(spec_paths, root=root)

    manifest = collect_input_manifest(change_id, root=root)
    product_files = manifest_paths_to_files(manifest.product_paths, root=root)
    test_files = manifest_paths_to_files(manifest.test_paths, root=root)

    product_digest, product_list = role_digest_with_tombstones(
        product_files,
        manifest.deleted_product_paths,
        root=root,
    )
    tests_digest, test_list = role_digest_with_tombstones(
        test_files,
        manifest.deleted_test_paths,
        root=root,
    )

    validator_paths: list[Path] = []
    for rel in (
        ".cursor/static-check/registry.yaml",
        "openspec/policies/VERIFY_POLICY.md",
        "opsx/project/capabilities.yaml",
    ):
        p = root / rel
        if p.is_file():
            validator_paths.append(p)
    validators_digest, validator_list = _role_digest_for_paths(validator_paths, root=root)

    apply_green = split_apply_green_identity(change_id, root=root)

    from bounded_verify_s5_obligation_applicability import collect_conditional_inputs_digest
    import hashlib
    import json

    conditional_inputs = collect_conditional_inputs_digest(change_id, root=root)
    conditional_digest = hashlib.sha256(
        json.dumps(conditional_inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    role_digests = {
        "specs": specs_digest,
        "tests": tests_digest,
        "product": product_digest,
        "validators": validators_digest,
        "apply_green": str(apply_green["digest"]),
        "conditional_inputs": conditional_digest,
    }
    content_paths = sorted(
        set(spec_list + product_list + test_list + validator_list + list(manifest.all_paths))
    )
    aggregate_input = "|".join(
        [
            role_digests["specs"],
            role_digests["tests"],
            role_digests["product"],
            role_digests["validators"],
            role_digests["apply_green"],
            role_digests["conditional_inputs"],
            task_identity_digest(tasks_text),
        ]
    ).encode("utf-8")
    content_digest = hashlib.sha256(aggregate_input).hexdigest()
    return {
        "content_digest": content_digest,
        "role_digests": role_digests,
        "content_paths": content_paths,
        "coverage_manifest": {
            "product_paths": list(manifest.product_paths),
            "test_paths": list(manifest.test_paths),
            "deleted_product_paths": list(manifest.deleted_product_paths),
            "deleted_test_paths": list(manifest.deleted_test_paths),
        },
        "provenance_head": manifest.provenance_head or git_head(root) or "",
        "task_identity_digest": task_identity_digest(tasks_text),
        "split_apply": {
            "policy": str(apply_green.get("policy", "NOT_APPLICABLE")),
            "source": str(apply_green.get("source", "")),
        },
    }


def live_implementation_snapshot(
    change_id: str,
    *,
    root: Path = REPO_ROOT,
    plan_schema: str = PLAN_SCHEMA_V1,
) -> dict[str, Any]:
    """Возвращает snapshot dict, сопоставимый с frozen plan."""
    if plan_schema == PLAN_SCHEMA_V2:
        return collect_v2_role_digests(change_id, root=root)
    digest = implementation_completion_digest(change_id, root=root)
    return {"content_digest": digest}


def assert_snapshot_fresh(
    plan: Mapping[str, Any],
    change_id: str,
    *,
    root: Path = REPO_ROOT,
) -> None:
    """Fail-closed drift: frozen snapshot vs live recompute."""
    schema = str(plan.get("schema_version", PLAN_SCHEMA_V1))
    frozen = plan.get("implementation_snapshot")
    if not isinstance(frozen, dict):
        raise BoundedVerifyPlanError("PLAN_INVALID", "implementation_snapshot invalid")
    live = live_implementation_snapshot(change_id, root=root, plan_schema=schema)
    if str(frozen.get("content_digest", "")) != str(live.get("content_digest", "")):
        raise BoundedVerifyPlanError(
            "BLOCKED_SNAPSHOT_DRIFT",
            "implementation_snapshot.content_digest mismatch",
        )
