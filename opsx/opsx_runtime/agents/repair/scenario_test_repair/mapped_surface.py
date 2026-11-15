"""Детерминированные границы repair: mapped/canonical surface и peer discovery."""
from __future__ import annotations

from opsx.project_context import resource_path

import ast
import re
from pathlib import Path

from agents.common.scenario_files import scenario_test_relpath
from agents.repair.scenario_test_repair.workflow_pytest_defer import (
    validate_mapped_workflow_deferred_marker,
)
from agents.verify.scenario_coverage_verify.review_semantics import trace_mappings

_SCENARIO_ID_LINE = re.compile(
    r"SCENARIO_ID:\s*(openspec/changes/[^\s]+::[^\n]+)",
)
_BOUNDARY_PG = re.compile(r"boundary_integration", re.IGNORECASE)


def repair_surfaces(repo: Path, change_id: str, scenario_id: str) -> set[str]:
    """Пути tests/**, которые repair может считать primary surface для сценария."""
    surfaces = {scenario_test_relpath(change_id, scenario_id)}
    node = str(trace_mappings(repo, change_id).get(scenario_id) or "")
    if "::" in node:
        surfaces.add(node.split("::", 1)[0])
    return surfaces


def union_repair_surfaces(repo: Path, change_id: str, scenario_ids: list[str]) -> set[str]:
    merged: set[str] = set()
    for scenario_id in scenario_ids:
        merged |= repair_surfaces(repo, change_id, scenario_id)
    return merged


def _normalize_rel(path: str) -> str:
    return path.replace("\\", "/")


def touches_mapped_surface(changed: list[str], surfaces: set[str]) -> bool:
    normalized = {_normalize_rel(item) for item in changed}
    return bool(normalized & surfaces)


def peer_only_edit(changed: list[str], surfaces: set[str]) -> bool:
    """True, если меняли integration/unit, но не mapped/canonical файл."""
    if not changed or touches_mapped_surface(changed, surfaces):
        return False
    return any(
        _normalize_rel(path).startswith("tests/integration/")
        or _normalize_rel(path).startswith("tests/unit/")
        for path in changed
    )


def find_reference_test_nodes(repo: Path, scenario_id: str) -> list[str]:
    """Pytest nodes в tests/**, где встречается точный SCENARIO_ID."""
    needle = f"SCENARIO_ID: {scenario_id}"
    found: list[tuple[int, str]] = []
    tests_root = resource_path(repo, "tests")
    if not tests_root.is_dir():
        return []
    for path in tests_root.rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if needle not in source:
            continue
        rel = path.relative_to(repo).as_posix()
        for name in _pytest_names_in_source(source):
            func_body = _function_source_slice(source, name)
            score = _peer_strength_score(func_body or source)
            node = f"{rel}::{name}"
            if any(existing == node for _, existing in found):
                continue
            found.append((score, node))
    found.sort(key=lambda item: (-item[0], item[1]))
    return [node for _, node in found]


def _pytest_names_in_source(source: str) -> list[str]:
    return re.findall(r"^\s*(?:async\s+)?def\s+(test_[a-zA-Z0-9_]+)\s*\(", source, re.MULTILINE)


def _peer_strength_score(source: str) -> int:
    score = 0
    if "process_pending" in source:
        score += 4
    if "should_send_booking_confirmation_notice" in source:
        score += 3
    if "/integration/" in source or "_pg" in source:
        score += 2
    if "assert_not_called" in source:
        score += 1
    if "TestClient" in source and "process_pending" not in source:
        score -= 2
    return score


def mapped_body_is_weak(repo: Path, change_id: str, scenario_id: str) -> bool:
    node = str(trace_mappings(repo, change_id).get(scenario_id) or "")
    if "::" not in node:
        return True
    rel, func_name = node.split("::", 1)
    path = resource_path(repo, rel)
    if not path.is_file():
        return True
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return True
    func_body = _function_source_slice(source, func_name.split("[", 1)[0])
    if not func_body:
        return True
    if "process_pending" in func_body:
        return False
    if "should_send_booking_confirmation_notice" in func_body:
        return False
    if "WEBHOOK_PATH" in func_body and "process_pending" not in func_body:
        return True
    if "TestClient" in func_body and "webhook" in func_body.lower():
        return True
    return len(func_body) < 800


def _function_source_slice(source: str, function_name: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    lines = source.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            end = getattr(node, "end_lineno", None) or node.lineno
            return "".join(lines[node.lineno - 1 : end])
    return ""


def peer_body_is_strong(source: str) -> bool:
    score = _peer_strength_score(source)
    return score >= 4 or (
        "process_pending" in source and "should_send_booking_confirmation_notice" in source
    )


def expected_boundary_integration_pg(scenario_plan_text: str | None) -> bool:
    if not scenario_plan_text:
        return False
    return bool(_BOUNDARY_PG.search(scenario_plan_text)) and "PG" in scenario_plan_text


def weak_for_expected_verification(source: str, scenario_plan_text: str | None) -> bool:
    """Weak stub detector: PG integration expected but test lacks processor/session seams."""
    if not expected_boundary_integration_pg(scenario_plan_text):
        return False
    markers = (
        "process_pending",
        "SessionLocal",
        "async_session",
        "notifications_pg",
        "fresh_db",
    )
    return not any(marker in source for marker in markers)


def validate_mapped_surface_gates(
    answer_status: str,
    *,
    addressed_ids: list[str],
    actual_changed: list[str],
    repo: Path,
    change_id: str,
) -> str | None:
    """Machine gate после filesystem diff; только для CANDIDATE."""
    if answer_status != "CANDIDATE_PENDING_VERIFY":
        return None
    if not addressed_ids:
        return None
    for scenario_id in addressed_ids:
        surfaces = repair_surfaces(repo, change_id, scenario_id)
        changed_for_gap = actual_changed
        if not touches_mapped_surface(changed_for_gap, surfaces):
            return f"REPAIR_MAPPED_SURFACE_MISSING: {scenario_id}"
        node = str(trace_mappings(repo, change_id).get(scenario_id) or "")
        if "::" in node:
            marker_error = validate_mapped_workflow_deferred_marker(repo, node)
            if marker_error:
                return marker_error
    surfaces_all = union_repair_surfaces(repo, change_id, addressed_ids)
    if peer_only_edit(actual_changed, surfaces_all):
        return "REPAIR_PEER_ONLY_EDIT: integration/unit edit without mapped touch"
    return None
