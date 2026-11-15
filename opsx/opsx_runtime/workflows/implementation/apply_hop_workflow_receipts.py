"""Фабрики ``CheckWorkflowReceipt`` для split-apply hop-агентов."""

from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path
from typing import Any, Mapping

from agents.verify.impl_verify.preflight import change_root, load_change_artifact_bundle
from check_workflow_receipt import CheckWorkflowReceipt
from workflows.artifact_paths import (
    apply_design_artifact_path,
    apply_design_review_receipt_path,
    scenario_coverage_planning_dir,
    stage_receipt_path,
)
from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)
from workflows.implementation.slice_receipt import load_stage_receipt
from workflows.repo_paths import resolve_opsx_repository_root
from workflows.verify.cycle_checks import CheckName


def _repo(state: Mapping[str, Any]) -> Path:
    raw = state.get("repository_path") or state.get("workspace")
    if not raw:
        raise ValueError("repository_path required")
    return resolve_opsx_repository_root(Path(str(raw)))


def _change(state: Mapping[str, Any]) -> str:
    change_id = str(state.get("change_id") or "").strip()
    if not change_id:
        raise ValueError("change_id required")
    return change_id


def _slice(state: Mapping[str, Any]) -> str:
    slice_id = str(state.get("implementation_slice_id") or "").strip()
    if not slice_id:
        raise ValueError("implementation_slice_id required")
    return slice_id


def apply_design_workflow_receipt(state: Mapping[str, Any]) -> CheckWorkflowReceipt:
    """Planning-only pin для APPLY DESIGN."""
    repo = _repo(state)
    change_id = _change(state)
    root = change_root(change_id, repo_root=repo)
    paths = [str(resource_path(root, "tasks.md"))]
    for name in ("proposal.md", "design.md", "owner-contract.md"):
        candidate = resource_path(root, name)
        if candidate.is_file():
            paths.append(str(candidate.relative_to(repo).as_posix()))
    specs = resource_path(root, "specs")
    if specs.is_dir():
        paths.extend(
            p.relative_to(repo).as_posix()
            for p in sorted(specs.rglob("*"))
            if p.is_file()
        )
    return CheckWorkflowReceipt(
        CheckName.APPLY_DESIGN,
        tuple(paths),
        include_change_artifacts=False,
    )


def apply_design_review_workflow_receipt(state: Mapping[str, Any]) -> CheckWorkflowReceipt:
    repo = _repo(state)
    change_id = _change(state)
    design_path = apply_design_artifact_path(repo, change_id)
    rel = [design_path.relative_to(repo).as_posix()] if design_path.is_file() else []
    return CheckWorkflowReceipt(
        CheckName.APPLY_DESIGN_REVIEW,
        tuple(rel),
        include_change_artifacts=False,
    )


def apply_scaffold_workflow_receipt(state: Mapping[str, Any]) -> CheckWorkflowReceipt:
    repo = _repo(state)
    change_id = _change(state)
    rels: list[str] = []
    for p in (
        apply_design_artifact_path(repo, change_id),
        apply_design_review_receipt_path(repo, change_id),
    ):
        if p.is_file():
            rels.append(p.relative_to(repo).as_posix())
    return CheckWorkflowReceipt(
        CheckName.APPLY_SCAFFOLD,
        tuple(rels),
        include_change_artifacts=False,
    )


def apply_red_workflow_receipt(state: Mapping[str, Any]) -> CheckWorkflowReceipt:
    repo = _repo(state)
    change_id = _change(state)
    slice_id = _slice(state)
    rels: list[str] = []
    scaffold = stage_receipt_path(repo, change_id, STAGE_APPLY_SCAFFOLD, slice_id)
    design = apply_design_artifact_path(repo, change_id)
    if design.is_file():
        rels.append(design.relative_to(repo).as_posix())
    if scaffold.is_file():
        rels.append(scaffold.relative_to(repo).as_posix())
    rules = resource_path(repo, ".cursor", "rules", "testing-rules.mdc")
    if rules.is_file():
        rels.append(rules.relative_to(repo).as_posix())
    return CheckWorkflowReceipt(CheckName.APPLY_RED, tuple(rels), include_change_artifacts=False)


def apply_implement_workflow_receipt(state: Mapping[str, Any]) -> CheckWorkflowReceipt:
    repo = _repo(state)
    change_id = _change(state)
    slice_id = _slice(state)
    rels: list[str] = []
    for stage in (STAGE_APPLY_SCAFFOLD, STAGE_APPLY_RED):
        p = stage_receipt_path(repo, change_id, stage, slice_id)
        if p.is_file():
            rels.append(p.relative_to(repo).as_posix())
    design = apply_design_artifact_path(repo, change_id)
    if design.is_file():
        rels.append(design.relative_to(repo).as_posix())
    red = load_stage_receipt(change_id, slice_id, STAGE_APPLY_RED, repo_root=repo)
    if isinstance(red, dict):
        for key in ("test_paths", "fixture_paths"):
            for item in red.get(key) or []:
                text = str(item).replace("\\", "/")
                if text:
                    rels.append(text)
    return CheckWorkflowReceipt(CheckName.APPLY_IMPLEMENT, tuple(sorted(set(rels))), include_change_artifacts=False)


def apply_green_workflow_receipt(state: Mapping[str, Any]) -> CheckWorkflowReceipt:
    repo = _repo(state)
    change_id = _change(state)
    slice_id = _slice(state)
    rels: list[str] = []
    for stage in (STAGE_APPLY_RED, STAGE_APPLY_IMPLEMENT):
        p = stage_receipt_path(repo, change_id, stage, slice_id)
        if p.is_file():
            rels.append(p.relative_to(repo).as_posix())
    design = apply_design_artifact_path(repo, change_id)
    if design.is_file():
        rels.append(design.relative_to(repo).as_posix())
    red = load_stage_receipt(change_id, slice_id, STAGE_APPLY_RED, repo_root=repo)
    if isinstance(red, dict):
        for key in ("test_paths", "fixture_paths"):
            for item in red.get(key) or []:
                text = str(item).replace("\\", "/")
                if text:
                    rels.append(text)
    return CheckWorkflowReceipt(CheckName.APPLY_GREEN, tuple(sorted(set(rels))), include_change_artifacts=False)


def scenario_coverage_plan_workflow_receipt(state: Mapping[str, Any]) -> CheckWorkflowReceipt:
    repo = _repo(state)
    change_id = _change(state)
    root = change_root(change_id, repo_root=repo)
    paths: list[str] = []
    for name in ("proposal.md", "design.md", "tasks.md"):
        p = resource_path(root, name)
        if p.is_file():
            paths.append(p.relative_to(repo).as_posix())
    specs = resource_path(root, "specs")
    if specs.is_dir():
        paths.extend(
            f.relative_to(repo).as_posix()
            for f in sorted(specs.rglob("*"))
            if f.is_file()
        )
    return CheckWorkflowReceipt(
        CheckName.SCENARIO_COVERAGE_PLAN,
        tuple(paths),
        include_change_artifacts=False,
    )


def scenario_test_author_workflow_receipt(state: Mapping[str, Any]) -> CheckWorkflowReceipt:
    repo = _repo(state)
    change_id = _change(state)
    rels: list[str] = []
    plan_md = scenario_coverage_planning_dir(repo, change_id) / "plan.md"
    if plan_md.is_file():
        rels.append(plan_md.relative_to(repo).as_posix())
    scenarios = scenario_coverage_planning_dir(repo, change_id) / "scenarios"
    if scenarios.is_dir():
        rels.extend(
            p.relative_to(repo).as_posix()
            for p in sorted(scenarios.rglob("*.md"))
            if p.is_file()
        )
    return CheckWorkflowReceipt(
        CheckName.SCENARIO_TEST_AUTHOR,
        tuple(rels),
        include_change_artifacts=False,
    )
