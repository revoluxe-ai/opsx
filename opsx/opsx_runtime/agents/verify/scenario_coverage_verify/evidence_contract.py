"""Deterministic, read-only evidence contract for scenario coverage.

This checks source identity and the existence of cited test statements.
It does NOT independently prove that an assertion has the claimed semantics:
that remains an explicit, reviewable auditor judgment.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from agents.check_receipts import content_digest
from agents.verify.scenario_coverage_verify.review_semantics import (
    review_proof_error, spec_clauses, trace_mappings,
)
from workflows.artifact_paths import scenario_machine_receipt_path

# The v3 snapshot is limited to the change's coverage plan, specs and
# directly mapped full test files. Production code is intentionally excluded.
_LEGACY_SOURCE_GROUPS = (
    ("tests", "*.py"),
    ("src/stroy", "*.py"),
    ("migrations", "*.py"),
    ("docs", "*.md"),
)
# Existing pinned-git recovery explicitly implements scenario_source_v2 and
# imports this legacy name. Do not silently upgrade its evidence contract.
_SOURCE_GROUPS = _LEGACY_SOURCE_GROUPS
# Changes to these files invalidate verifier authority, not product inputs.
_SCENARIO_VERIFIER_FILES = (
    # This is reviewer/validator authority only. Runtime orchestration, row
    # selection, receipt storage, recovery and SDK adapter are NOT semantics.
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/review_semantics.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/semantic_validation.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/input_pack.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/prompt/get_prompt.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/mapped_test_view.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/schemas.py",
    "opsx/opsx_runtime/agents/response_schema.py",
    # The shared model-answer validation lifecycle and response validation
    # remain part of review authority. SDK transport is not.
    "opsx/opsx_runtime/agents/abc_agent.py",
    "opsx/opsx_runtime/agents/utils/response.py",
)

_MAX_FILES = 6000
_MAX_BYTES = 200 * 1024 * 1024


def _legacy_paths(root: Path, change: Path) -> set[Path]:
    """Pre-isolation inventory, used solely to validate existing observations.

    Never use this inventory for new row identity: every test/doc change used
    to invalidate the whole unrelated scenario set.
    """
    change_id = change.name
    paths = {
        change / "scenario-coverage" / "plan.md",
        scenario_machine_receipt_path(root, change_id, "plan.receipt.json"),
        scenario_machine_receipt_path(root, change_id, "test_trace_matrix.json"),
    }
    paths.update((change / "specs").rglob("*.md"))
    for folder, glob in _LEGACY_SOURCE_GROUPS:
        base = resource_path(root, folder)
        if base.is_dir():
            paths.update(
                p for p in base.rglob(glob)
                if p.relative_to(root).parts[:2] != ("tests", "opsx")
            )
    return paths


def _scoped_test_files(root: Path, change_id: str) -> set[Path]:
    """Only mapped pytest files, not unrelated tests, helpers or conftests.

    Row identity hashes the entire mapped file. Dependencies outside that file
    are intentionally outside this Owner-approved coverage-review freshness
    policy; production behavior and test execution belong to separate gates.
    """
    tests = resource_path(root, "tests")
    try:
        mapping = trace_mappings(root, change_id)
    except (ValueError, OSError) as exc:
        raise ValueError("TRACE_MATRIX_INVALID") from exc
    if not mapping:
        return set()
    included: set[Path] = set()
    for node in mapping.values():
        if not isinstance(node, str) or not node.strip():
            continue
        if "::" not in node:
            raise ValueError("SCENARIO_TEST_NODE_INVALID")
        rel = Path(node.split("::", 1)[0])
        candidate = resource_path(root, rel)
        if (
            not rel.parts or rel.parts[0] != "tests" or rel.suffix != ".py"
            or ".." in rel.parts or candidate.is_symlink()
            or not candidate.resolve().is_relative_to(tests.resolve())
        ):
            raise ValueError("SCENARIO_TEST_SCOPE_INVALID")
        if candidate.is_file():
            included.add(candidate)
        # A missing mapped test is a GAP candidate; its absence is separately
        # encoded in the per-scenario digest and must not grant SUPPORTED.
    return included

def source_paths(repo_root: Path, change_id: str) -> set[Path]:
    """Change-scoped audit inputs; only shared production dependencies are global."""
    root = repo_root.resolve()
    change = resource_path(root, "openspec", "changes", change_id)
    if change.resolve() != change or not change.is_dir():
        raise ValueError("SCENARIO_INPUT_MISSING: invalid change directory")
    paths: set[Path] = {
        change / "scenario-coverage" / "plan.md",
        scenario_machine_receipt_path(root, change_id, "plan.receipt.json"),
        scenario_machine_receipt_path(root, change_id, "test_trace_matrix.json"),
    }
    paths.update((change / "specs").rglob("*.md"))
    scenarios = change / "scenario-coverage" / "scenarios"
    if scenarios.is_dir():
        paths.update(path for path in scenarios.glob("*.md") if path.is_file() and not path.is_symlink())
    paths.update(_scoped_test_files(root, change_id))
    return paths


def legacy_source_digest(repo_root: Path, change_id: str) -> str:
    """Old complete-source identity for explicit checked-only migration."""
    root = repo_root.resolve()
    change = resource_path(root, "openspec", "changes", change_id)
    return _hash_source_paths(root, _legacy_paths(root, change), legacy=True)


def _hash_source_paths(root: Path, paths: set[Path], *, legacy: bool) -> str:
    if len(paths) > _MAX_FILES:
        raise ValueError(f"SCENARIO_INPUT_TOO_BROAD: {len(paths)} > {_MAX_FILES}")
    digest = hashlib.sha256(
        b"opsx-scenario-source-v2\0" if legacy else b"opsx-scenario-source-v3\0"
    )
    size = 0
    for path in sorted(paths, key=lambda p: p.as_posix()):
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"SCENARIO_INPUT_MISSING: {path}")
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise ValueError("SCENARIO_INPUT_TOO_LARGE")
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def scenario_verifier_contract_digest(repo_root: Path, model: Mapping[str, Any] | None) -> str:
    """Reviewer/validator semantics and exact model tier, not execution internals."""
    if not isinstance(model, Mapping) or not isinstance(model.get("id"), str) or not model["id"].strip():
        raise ValueError("SCENARIO_VERIFIER_MODEL_UNBOUND")
    files = {resource_path(repo_root, rel) for rel in _SCENARIO_VERIFIER_FILES}
    if any(not path.is_file() for path in files):
        raise ValueError("SCENARIO_VERIFIER_CONTRACT_MISSING")
    policy = content_digest(repo_root, files)
    identity = json.dumps(
        {"semantic_policy": policy, "model": dict(model)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def input_snapshot(repo_root: Path, change_id: str) -> dict[str, Any]:
    """Source snapshot with current change test closure and shared production."""
    root = repo_root.resolve()
    paths = source_paths(root, change_id)
    source_digest = _hash_source_paths(root, paths, legacy=False)
    return {
        "contract": "scenario_source_v3",
        "source_digest": source_digest,
        "file_count": len(paths),
        "bytes_read": sum(path.stat().st_size for path in paths),
    }
