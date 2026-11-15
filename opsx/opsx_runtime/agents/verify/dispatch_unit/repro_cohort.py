"""Bounded pytest reproduction cohorts, distinct from causal repair groups.

A shared module or fixture scope is a reason to reproduce tests together,
NOT proof that separate findings have the same root cause.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from agents.verify.group_error.schemas import Finding, GroupErrorResult
from agents.verify.pytest_run_health import affected_test_paths, is_harness_blocker

MAX_COHORT_NODES = 64
_PYTEST_NODE = re.compile(
    r"^(?:pytest:\s*(?:ERROR\s+|FAILED\s+)?|ERROR\s+|FAILED\s+)"
    r"(?P<node>[^\s]+?\.py(?:::[^\s]+)*)"
    r"(?:\s+-.*)?$"
)


def pytest_node_id(observation: str) -> str | None:
    """Canonical pytest node id from gate diagnostics or pytest summary."""
    match = _PYTEST_NODE.match(observation.strip())
    if match is None:
        return None
    return match.group("node").split(" - ", 1)[0]


def pytest_module(node_id: str) -> str:
    return node_id.split("::", 1)[0].replace("\\", "/")


def pytest_issue_kind(observation: str) -> str:
    raw = observation.strip()
    if raw.startswith("ERROR ") or raw.startswith("pytest: ERROR "):
        return "ERROR"
    if raw.startswith("FAILED ") or raw.startswith("pytest: FAILED "):
        return "FAILED"
    return "UNKNOWN"


def _pytest_findings(analysis: Mapping[str, Any] | None) -> list[Finding]:
    if not isinstance(analysis, Mapping):
        return []
    out: list[Finding] = []
    for finding in GroupErrorResult.model_validate(analysis).findings:
        if finding.source_tool != "pytest":
            continue
        if pytest_node_id(finding.observation or ""):
            out.append(finding)
            continue
        if is_harness_blocker(finding.repair_workflow) or affected_test_paths(
            finding.observation or "",
        ):
            out.append(finding)
    return out


def _module_for_finding(finding: Finding) -> str | None:
    node = pytest_node_id(finding.observation or "")
    if node:
        return pytest_module(node)
    paths = affected_test_paths(finding.observation or "")
    return paths[0] if paths else None


def reproduction_cohort(
    analysis: Mapping[str, Any] | None,
    *,
    finding_ids: list[str],
) -> dict[str, Any]:
    """Build a provisional test cohort from existing fresh gate findings.

    Same module and same test directory are *candidate reproduction* boundaries
    for fixtures and order-dependent behavior. Grouping for execution does NOT
    assert common root cause or broaden mutation authority.
    """
    findings = _pytest_findings(analysis)
    by_id = {finding.finding_id: finding for finding in findings}
    selected = [by_id[fid] for fid in finding_ids if fid in by_id]
    if not selected:
        return {"finding_ids": [], "test_nodes": [], "test_modules": [], "basis": "none"}
    harness_only = all(is_harness_blocker(f.repair_workflow) for f in selected)
    anchor_modules = {
        mod
        for f in selected
        if (mod := _module_for_finding(f))
    }
    if harness_only and anchor_modules:
        modules = sorted(anchor_modules)
        return {
            "finding_ids": [f.finding_id for f in selected],
            "test_nodes": [],
            "test_modules": modules,
            "basis": "HARNESS_MODULE",
        }
    anchor_dirs = {str(Path(module).parent) for module in anchor_modules}
    nodes: dict[str, str] = {}
    for finding in findings:
        node = pytest_node_id(finding.observation or "")
        if node is None:
            mod = _module_for_finding(finding)
            if mod and mod in anchor_modules:
                nodes[finding.finding_id] = mod
            continue
        module = pytest_module(node)
        same_module = module in anchor_modules
        common_fixture_scope = str(Path(module).parent) in anchor_dirs
        if finding in selected or same_module or common_fixture_scope:
            nodes[finding.finding_id] = node
    if len(nodes) > MAX_COHORT_NODES:
        return {
            "finding_ids": [],
            "test_nodes": [],
            "test_modules": [],
            "basis": "COHORT_TOO_BROAD",
            "reason": f"{len(nodes)} pytest findings exceed {MAX_COHORT_NODES}",
        }
    modules = sorted({pytest_module(node) for node in nodes.values()})
    return {
        "finding_ids": list(nodes),
        "test_nodes": sorted(set(nodes.values())),
        "test_modules": modules,
        "basis": "module_and_directory_fixture_scope",
    }


def safe_pytest_modules(repo_root: Path, modules: list[str]) -> list[str]:
    """Restrict executable pytest paths to actual repository tests/*.py."""
    root = repo_root.resolve()
    tests_root = (resource_path(root, "tests")).resolve()
    if not modules:
        raise ValueError("empty pytest cohort cannot be verified")
    safe: list[str] = []
    for raw in modules:
        module = pytest_module(raw)
        path = Path(module)
        target = (path if path.is_absolute() else resource_path(root, path)).resolve()
        if not target.is_relative_to(tests_root) or target.suffix != ".py":
            raise ValueError(f"unsafe pytest cohort path: {raw}")
        if not target.is_file():
            raise ValueError(f"pytest cohort module missing: {raw}")
        safe.append(str(target))
    return sorted(set(safe))


def failure_fingerprints(diagnostics: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Stable enough for comparing two full gate snapshots, never F-0001 order."""
    if not isinstance(diagnostics, Mapping):
        return ()
    parts: set[str] = set()
    for tool in ("pytest", "pyright", "mypy"):
        for item in diagnostics.get(tool) or []:
            if not isinstance(item, str):
                continue
            raw = item.strip()
            if not raw:
                continue
            node = pytest_node_id(raw) if tool == "pytest" else None
            token = node if node else raw
            parts.add(f"{tool}:{token}")
    return tuple(sorted(parts))


def cohort_identity(*, run_id: str, test_nodes: list[str]) -> str:
    material = "\\n".join([run_id, *sorted(test_nodes)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
