"""Preflight lint: weak mapped tests vs EXPECTED_VERIFICATION в scenario files."""
from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path

from agents.common.scenario_files import read_scenario_ids, scenario_filename
from agents.repair.scenario_test_repair.mapped_surface import weak_for_expected_verification
from agents.verify.scenario_coverage_verify.review_semantics import trace_mappings


def assess_weak_mapped_stub_row(
    repo_root: Path,
    change_id: str,
    scenario_id: str,
) -> str | None:
    coverage = resource_path(repo_root, "openspec", "changes", change_id, "scenario-coverage")
    mapping = trace_mappings(repo_root, change_id)
    node = str(mapping.get(scenario_id) or "")
    if "::" not in node:
        return None
    rel = node.split("::", 1)[0]
    test_path = resource_path(repo_root, rel)
    if not test_path.is_file():
        return None
    plan_path = coverage / "scenarios" / scenario_filename(scenario_id)
    plan_text = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else None
    try:
        source = test_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if weak_for_expected_verification(source, plan_text):
        return (
            f"TRACE_WEAK_MAPPED_STUB:{scenario_id} "
            "(boundary_integration+PG expected; handoff=/opsx-test authorship)"
        )
    return None


def assess_weak_mapped_stubs(repo_root: Path, change_id: str) -> str | None:
    coverage = resource_path(repo_root, "openspec", "changes", change_id, "scenario-coverage")
    try:
        order = read_scenario_ids(coverage, allow_historical=False)
    except ValueError as exc:
        return str(exc)
    mapping = trace_mappings(repo_root, change_id)
    weak: list[str] = []
    for scenario_id in order:
        node = str(mapping.get(scenario_id) or "")
        if "::" not in node:
            continue
        rel = node.split("::", 1)[0]
        test_path = resource_path(repo_root, rel)
        if not test_path.is_file():
            continue
        plan_path = coverage / "scenarios" / scenario_filename(scenario_id)
        plan_text = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else None
        try:
            source = test_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if weak_for_expected_verification(source, plan_text):
            weak.append(scenario_id)
    if not weak:
        return None
    sample = weak[0]
    return (
        "TRACE_WEAK_MAPPED_STUB: mapped test(s) lack boundary_integration seams; "
        f"example={sample}; handoff=/opsx-test authorship"
    )
