"""Bounded deterministic producer of scenario test trace mappings.

This producer never invents a test or issues a coverage verdict. A scenario
with no test node is written as an empty ``test_node_id`` for row repair.
Several candidate nodes for one scenario stay an explicit BLOCKED handoff.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import ast
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from agents.state import GraphState
from agents.common.planning_artifacts import inspect_plan_artifacts
from agents.common.scenario_files import (
    plan_receipt_machine_path,
    read_scenario_ids,
    scenario_test_relpath,
    trace_matrix_machine_path,
)
from agents.implementation.scenario_trace_materializer.trace_receipt import (
    WORK_SCOPE_DIGEST_KEY,
    compute_trace_work_scope_digest,
    trace_receipt_pre_skip,
)

_CHANGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _candidate_digest(repo: Path, change: Path) -> str:
    """Bind the writer to the complete planned/test source revision."""
    digest = hashlib.sha256(b"opsx-scenario-trace-producer-v1\0")
    coverage = change / "scenario-coverage"
    paths = [
        coverage / "plan.md",
        plan_receipt_machine_path(coverage),
        *sorted((change / "specs").rglob("*.md")),
        *sorted((coverage / "scenarios").glob("*.md")),
    ]
    tests = resource_path(repo, "tests")
    if tests.is_dir():
        paths.extend(sorted(tests.rglob("*.py")))
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        digest.update(path.relative_to(repo).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()



def _test_node_valid(repo: Path, node: str) -> bool:
    parts = node.split("::")
    if len(parts) < 2 or not parts[0].startswith("tests/"):
        return False
    tests_root = (resource_path(repo, "tests")).resolve()
    path = (resource_path(repo, parts[0])).resolve()
    if not path.is_relative_to(tests_root) or not path.is_file() or path.is_symlink():
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return False
    name = parts[-1].split("[", 1)[0]
    if not name.startswith("test_"):
        return False
    for top in tree.body:
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if len(parts) == 2 and top.name == name:
                return True
        elif isinstance(top, ast.ClassDef) and len(parts) == 3 and top.name == parts[1]:
            if any(isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and
                   f.name == name for f in top.body):
                return True
    return False


def _discover_marked_nodes(repo: Path, scenario_ids: list[str]) -> dict[str, set[str]]:
    """Only accept an explicit scenario ID attached to an actual test function."""
    ids = set(scenario_ids)
    found: dict[str, set[str]] = {sid: set() for sid in ids}
    tests = (resource_path(repo, "tests")).resolve()
    if not tests.is_dir():
        return found
    candidates = sorted(tests.rglob("test_*.py"))
    if len(candidates) > 6000:
        return found
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        relevant = [sid for sid in ids if sid in source]
        if not relevant:
            continue
        try:
            parsed = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        lines = source.splitlines(keepends=True)
        functions: list[tuple[str, Any]] = []
        for item in parsed.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                functions.append((item.name, item))
            elif isinstance(item, ast.ClassDef):
                for method in item.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test_"):
                        functions.append((f"{item.name}::{method.name}", method))
        rel = path.relative_to(repo).as_posix()
        for function_name, item in functions:
            body = "".join(lines[item.lineno - 1:item.end_lineno])
            for sid in relevant:
                if sid in body:
                    found[sid].add(f"{rel}::{function_name}")
        # Module-level marker is only unambiguous if the module has one test.
        if len(functions) == 1:
            for sid in relevant:
                found[sid].add(f"{rel}::{functions[0][0]}")
    return found


def _select_test_node(change_id: str, scenario_id: str, choices: list[str]) -> str | None:
    """Pick a unique mapped node, preferring the canonical scenario test path."""
    canonical = scenario_test_relpath(change_id, scenario_id)
    preferred = [node for node in choices if node.split("::", 1)[0] == canonical]
    if len(preferred) == 1:
        return preferred[0]
    if len(preferred) > 1:
        return None
    if len(choices) == 1:
        return choices[0]
    return None


def materialize_trace(repo_root: Path, change_id: str) -> dict[str, Any]:
    """Собрать ``test_trace_matrix.json`` из уже написанных тестов.

    Нет узла — строка с пустым ``test_node_id``. Два и больше кандидата — BLOCKED.
    """
    root = repo_root.resolve()
    if not _CHANGE.fullmatch(change_id):
        return {"status": "BLOCKED", "reason": "CHANGE_ID_INVALID"}
    change = resource_path(root, "openspec", "changes", change_id)
    directory = change / "scenario-coverage"
    plan_path = directory / "plan.md"
    receipt_path = plan_receipt_machine_path(directory)
    matrix_path = trace_matrix_machine_path(directory)
    if directory.is_symlink() or matrix_path.is_symlink():
        return {"status": "BLOCKED", "reason": "UNSAFE_MATRIX_PATH"}
    try:
        plan_bytes = plan_path.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(receipt_bytes)
        rows = read_scenario_ids(directory, allow_historical=False)
        evidence_id = receipt["PLAN_EVIDENCE_ID"]
        if not rows or len(rows) != len(set(rows)) or not evidence_id:
            return {"status": "BLOCKED", "reason": "PLAN_ROWS_OR_ID_INVALID"}
        # Do not write against a stale/invalid plan; only the matrix itself
        # may be missing or stale.
        planning = inspect_plan_artifacts(root, change_id, phase="planning")
        if planning["status"] != "READY":
            return {"status": "BLOCKED", "reason": planning["code"], "producer": planning.get("producer")}
        readiness = inspect_plan_artifacts(root, change_id, phase="verify")
        if trace_receipt_pre_skip(
            root,
            change_id,
            verify_preflight=readiness,
            matrix_path=matrix_path,
        ):
            return {"status": "ALREADY_CURRENT", "scenario_count": readiness.get("scenario_count", 0)}
        if readiness["status"] == "READY":
            pass
        elif readiness["code"] not in ("TRACE_MATRIX_MISSING", "TRACE_MATRIX_STALE"):
            return {"status": "BLOCKED", "reason": readiness["code"], "producer": readiness.get("producer")}
        source_before = _candidate_digest(root, change)
        previous: dict[str, dict[str, Any]] = {}
        if matrix_path.exists():
            obj = json.loads(matrix_path.read_text(encoding="utf-8"))
            if not isinstance(obj, dict) or not isinstance(obj.get("entries"), list):
                return {"status": "BLOCKED", "reason": "TRACE_MATRIX_INVALID"}
            if obj.get("change_id") not in (None, change_id):
                return {"status": "BLOCKED", "reason": "TRACE_MATRIX_CHANGE_MISMATCH"}
            for entry in obj["entries"]:
                if not isinstance(entry, dict):
                    return {"status": "BLOCKED", "reason": "TRACE_MATRIX_INVALID"}
                sid, node = entry.get("scenario_id"), entry.get("test_node_id")
                if not isinstance(sid, str) or not isinstance(node, str) or sid in previous:
                    return {"status": "BLOCKED", "reason": "TRACE_MATRIX_INVALID"}
                previous[sid] = entry
        marked = _discover_marked_nodes(root, rows)
        entries = []
        unresolved: list[str] = []
        for sid in rows:
            existing_row = previous.get(sid) or {}
            existing = existing_row.get("test_node_id")
            if isinstance(existing, str) and _test_node_valid(root, existing):
                node = existing
            else:
                choices = sorted(x for x in marked[sid] if _test_node_valid(root, x))
                node = _select_test_node(change_id, sid, choices)
                if node is None and choices:
                    unresolved.append(sid)
                    continue
                if node is None:
                    node = ""
            verification = existing_row.get("verification")
            if not isinstance(verification, list) or not all(
                isinstance(x, str) for x in verification
            ):
                verification = ["scenario_seam"]
            entries.append({
                "scenario_id": sid,
                "test_node_id": node,
                "verification": verification,
            })
        if unresolved:
            return {
                "status": "BLOCKED",
                "reason": "TRACE_MAPPING_AMBIGUOUS_OR_MISSING",
                "unresolved": unresolved,
                "handoff": "/opsx-test (explicit scenario-to-test evidence, then re-run)",
            }
        scope_digest = compute_trace_work_scope_digest(root, change_id)
        if not scope_digest:
            return {"status": "BLOCKED", "reason": "TRACE_SCOPE_DIGEST_UNAVAILABLE"}
        payload = {
            "schema_version": 1,
            "change_id": change_id,
            "plan_evidence_id": evidence_id,
            WORK_SCOPE_DIGEST_KEY: scope_digest,
            "entries": entries,
        }
        content = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        if matrix_path.exists() and matrix_path.read_bytes() == content:
            return {"status": "ALREADY_CURRENT", "scenario_count": len(entries)}
        if (
            plan_path.read_bytes() != plan_bytes
            or receipt_path.read_bytes() != receipt_bytes
            or _candidate_digest(root, change) != source_before
        ):
            return {"status": "BLOCKED", "reason": "SOURCE_CHANGED_DURING_MATERIALIZATION"}
        # Atomic publication of the *only* authorized write target.
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".trace-matrix-", suffix=".tmp", dir=str(matrix_path.parent),
        )
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, matrix_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return {"status": "MATERIALIZED", "scenario_count": len(entries),
                "path": str(matrix_path.relative_to(root))}
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"status": "BLOCKED", "reason": f"MATERIALIZATION_INVALID: {exc}"}


class ScenarioTraceMaterializerAgent:
    """Implementation/test-authoring stage writer, never a verify graph node."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        result = materialize_trace(
            Path(str(state["repository_path"])),
            str(state.get("change_id") or ""),
        )
        return {"plan_artifact_materialization_result": result}


async def scenario_trace_materialize_node(state: GraphState) -> dict[str, Any]:
    return await ScenarioTraceMaterializerAgent().run(state)
