"""Fail-closed row-level impact selection for scenario coverage source reviews.

V3 row identity binds only the spec Scenario section and the complete mapped
test module for each plan row. Plan metadata, reviewer policy, model tier and
unrelated repository paths do not invalidate a durable row receipt. Historical
v1 scope is retained for strictly proven old receipts.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

ROW_CONTRACT = "scenario_row_impact_v3"
LEGACY_ROW_CONTRACT = "scenario_row_impact_v1"
_MAX_BYTES = 200 * 1024 * 1024
_MAX_FILES = 6000


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_PRODUCT_SCOPE_DIGEST = _sha(b"opsx-product-scope-v3\0")


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _file_identity(root: Path, path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"ROW_INPUT_UNSAFE: {path}")
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"ROW_INPUT_OUTSIDE: {path}")
    return path.relative_to(root).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0"


def _is_leaf(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return (
        len(rel.parts) >= 2
        and rel.parts[0] == "tests"
        and path.name.startswith("test_")
        and path.suffix == ".py"
    )


def _unsafe_leaf_imports(root: Path, paths: set[Path]) -> bool:
    """Disable row reuse if test-module isolation cannot be established."""
    leaf_modules = {
        ".".join(p.relative_to(root).with_suffix("").parts)
        for p in paths if _is_leaf(p, root)
    }
    leaf_names = {p.stem for p in paths if _is_leaf(p, root)}
    for path in paths:
        if path.suffix != ".py" or path.relative_to(root).parts[0] != "tests":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeError, OSError):
            return True
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names] if isinstance(node, ast.Import)
                    else [str(node.module or "") + "." + a.name for a in node.names] + [str(node.module or "")]
                )
                if any(
                    n == target or n.startswith(target + ".")
                    or n == target.rsplit(".", 1)[-1]
                    or n.endswith("." + target.rsplit(".", 1)[-1])
                    for n in names for target in leaf_modules
                ):
                    return True
                if isinstance(node, ast.ImportFrom) and any(
                    a.name in leaf_names for a in node.names
                ):
                    return True
            if isinstance(node, ast.Name) and node.id == "pytest_plugins":
                return True
            if isinstance(node, ast.Call):
                fn = node.func
                if (
                    (isinstance(fn, ast.Name) and fn.id == "__import__")
                    or (isinstance(fn, ast.Attribute) and fn.attr == "import_module")
                ):
                    # Literal imports of production modules are already shared-hashed.
                    if (
                        not node.args or not isinstance(node.args[0], ast.Constant)
                        or not isinstance(node.args[0].value, str)
                        or node.args[0].value.startswith("tests.")
                    ):
                        return True
    return False


def impact_scope_v1(
    root: Path,
    paths: set[Path],
    mappings: Mapping[str, str],
    *,
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    """Return global authority identity plus individually bound test row keys.

    Non-leaf/shared changes invalidate all. Changes to a known isolated test
    module invalidate exactly the scenarios whose mapping targets that module.
    """
    root = root.resolve()
    if not paths or len(paths) > _MAX_FILES:
        raise ValueError("ROW_INPUTS_INVALID")
    if not mappings or not authority.get("model") or not authority.get("plan_evidence_id"):
        raise ValueError("ROW_AUTHORITY_UNBOUND")
    paths = set(paths)
    if _unsafe_leaf_imports(root, paths):
        leaf_files: set[Path] = set()
    else:
        leaf_files = {p for p in paths if _is_leaf(p, root)}
    shared = paths - leaf_files
    size = 0
    h = hashlib.sha256(b"opsx-row-shared-v1\0")
    for path in sorted(shared):
        blob = _file_identity(root, path)
        size += len(blob)
        if size > _MAX_BYTES:
            raise ValueError("ROW_INPUTS_TOO_LARGE")
        h.update(blob)
    # Changes in leaf membership are global (new/deleted/renamed test modules).
    h.update(_json(sorted(p.relative_to(root).as_posix() for p in leaf_files)))
    h.update(_json(dict(authority)))
    shared_digest = h.hexdigest()
    rows = {}
    for sid, test_node in mappings.items():
        if not isinstance(sid, str) or not sid or not isinstance(test_node, str) or "::" not in test_node:
            raise ValueError("ROW_MAPPING_INVALID")
        rel = Path(test_node.split("::", 1)[0])
        path = resource_path(root, rel)
        if path not in leaf_files:
            # A non-isolated mapped test is included in the shared digest:
            # only the exact same entire shared source can reuse its receipt.
            # A missing mapped file can never produce a SUPPORTED receipt.
            rows[sid] = (
                _sha(
                    b"opsx-row-shared-only-v1\0" + shared_digest.encode() + b"\0"
                    + sid.encode() + b"\0" + test_node.encode()
                ) if path in shared else None
            )
            continue
        blob = _file_identity(root, path)
        rows[sid] = _sha(
            b"opsx-row-v1\0" + shared_digest.encode() + b"\0"
            + sid.encode() + b"\0" + test_node.encode() + b"\0" + blob
        )
    return {"contract": LEGACY_ROW_CONTRACT, "shared_digest": shared_digest, "row_digests": rows}


def _plan_entry(plan_file: Path, scenario_id: str) -> bytes:
    """Extract exactly one SCENARIO_ID row, not other rows or plan metadata."""
    lines = plan_file.read_text(encoding="utf-8").splitlines(keepends=True)
    marker = re.compile(r"^(\s*)-\s+SCENARIO_ID:\s*(.*?)\s*$")
    found = [(i, len(m.group(1))) for i, line in enumerate(lines)
             if (m := marker.match(line)) and m.group(2) == scenario_id]
    if len(found) != 1:
        raise ValueError(f"SCENARIO_PLAN_ENTRY_UNBOUND: {scenario_id}")
    index, indent = found[0]
    end = index + 1
    while end < len(lines):
        line = lines[end]
        if line.strip():
            next_indent = len(line) - len(line.lstrip())
            if next_indent <= indent:
                break
        end += 1
    return "".join(lines[index:end]).encode("utf-8")


def _derived_plan_row_bytes(change: Path, scenario_id: str) -> bytes:
    """Тело plan-row для ``DERIVED::`` из ``scenario-coverage/scenarios/``."""
    from agents.common.scenario_files import scenario_filename

    if not scenario_id.startswith("DERIVED::"):
        raise ValueError(f"SCENARIO_DERIVED_ONLY: {scenario_id}")
    path = change / "scenario-coverage" / "scenarios" / scenario_filename(scenario_id)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"SCENARIO_PLAN_FILE_MISSING: {scenario_id}")
    return path.read_bytes()


def _scenario_section(root: Path, change: Path, scenario_id: str,
                      scoped_paths: set[Path]) -> bytes:
    """Read one spec Scenario block; sibling sections do not enter this hash."""
    if scenario_id.startswith("DERIVED::"):
        return _derived_plan_row_bytes(change, scenario_id)
    if "::" not in scenario_id:
        # Minimal fixtures use synthetic short IDs. Production requires a
        # full spec path::Scenario title via spec_clauses() before row selection.
        specs = sorted(
            p for p in scoped_paths
            if p.is_relative_to(change / "specs") and p.suffix == ".md"
        )
        return b"".join(_file_identity(root, p) for p in specs)
    rel, title = scenario_id.split("::", 1)
    path = resource_path(root, rel)
    if (
        not title or path.is_symlink()
        or not path.resolve().is_relative_to((change / "specs").resolve())
        or not path.is_file() or path.suffix != ".md"
    ):
        raise ValueError(f"SCENARIO_SPEC_UNBOUND: {scenario_id}")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    marker = re.compile(r"^\s*(#{1,6})\s+Scenario\s*[:：]\s*(.*?)\s*$", re.I)
    matches = [(i, len(m.group(1))) for i, line in enumerate(lines)
               if (m := marker.match(line)) and m.group(2) == title]
    if len(matches) != 1:
        raise ValueError(f"SCENARIO_SPEC_AMBIGUOUS: {scenario_id}")
    index, level = matches[0]
    end = index + 1
    heading = re.compile(r"^\s*(#{1,6})\s+")
    while end < len(lines):
        m = heading.match(lines[end])
        if m and len(m.group(1)) <= level:
            break
        end += 1
    return "".join(lines[index:end]).encode("utf-8")


def impact_scope(
    root: Path,
    paths: set[Path],
    mappings: Mapping[str, str],
    *,
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    """V3 freshness: spec Scenario section + mapped test file only.

    Missing mapped tests never yield a reusable positive ``row_digest``. Plan,
    matrix, reviewer policy, model tier and unrelated paths are excluded.
    """
    del authority  # schedule/ledger API only; product identity ignores reviewer.
    root = root.resolve()
    if not paths or len(paths) > _MAX_FILES:
        raise ValueError("ROW_INPUTS_INVALID")
    plan_files = [
        p for p in paths if p.name == "plan.md"
        and p.parent.name == "scenario-coverage"
    ]
    scenario_files = [
        p for p in paths
        if p.parent.name == "scenarios" and p.parent.parent.name == "scenario-coverage"
    ]
    if len(plan_files) == 1:
        change = plan_files[0].parent.parent
    elif scenario_files:
        change = scenario_files[0].parent.parent.parent
    else:
        raise ValueError("ROW_PLAN_UNBOUND")
    if not change.resolve().is_relative_to(root):
        raise ValueError("ROW_PLAN_UNSAFE")
    if not mappings:
        return {
            "contract": ROW_CONTRACT,
            "shared_digest": _PRODUCT_SCOPE_DIGEST,
            "row_digests": {},
        }
    row_digests: dict[str, str | None] = {}
    size = 0
    for sid, node in mappings.items():
        if not isinstance(sid, str) or not sid or not isinstance(node, str) or "::" not in node:
            raise ValueError("ROW_MAPPING_INVALID")
        rel = Path(node.split("::", 1)[0])
        test_path = resource_path(root, rel)
        if (
            not rel.parts or rel.parts[0] != "tests" or rel.suffix != ".py"
            or ".." in rel.parts or test_path.is_symlink()
            or not test_path.resolve().is_relative_to((resource_path(root, "tests")).resolve())
        ):
            raise ValueError("ROW_TEST_SCOPE_INVALID")
        scenario = _scenario_section(root, change, sid, paths)
        if not test_path.is_file():
            row_digests[sid] = None
            continue
        test_data = _file_identity(root, test_path)
        size += len(scenario) + len(test_data)
        if size > _MAX_BYTES:
            raise ValueError("ROW_INPUTS_TOO_LARGE")
        row_digests[sid] = _sha(
            b"opsx-scenario-row-v3\0"
            + sid.encode("utf-8") + b"\0"
            + node.encode("utf-8") + b"\0"
            + scenario + b"\0" + test_data
        )
    return {
        "contract": ROW_CONTRACT,
        "shared_digest": _PRODUCT_SCOPE_DIGEST,
        "row_digests": row_digests,
    }


def row_digest_for_scope(scope: Mapping[str, Any], scenario_id: str) -> str | None:
    """v3 per-row digest из ``impact_scope``; предпочтительный ключ для skip/receipt."""
    digests = scope.get("row_digests")
    if not isinstance(digests, Mapping):
        return None
    value = digests.get(scenario_id)
    return value if isinstance(value, str) and value else None


def row_freshness_token(root: Path, change_id: str, scenario_id: str, node: str) -> str:
    """Deprecated: оркестрация commit/journal. Для evidence reuse — ``row_digests[sid]``.

    Префикс ``opsx-scenario-row-fresh`` ≠ v3 ``row_digests``. Пустой mapping → ``missing``.
    """
    root = root.resolve()
    change = resource_path(root, "openspec", "changes", change_id)
    try:
        scenario = _scenario_section(root, change, scenario_id, set())
    except ValueError:
        scenario = b""
    test_blob = b"missing"
    if "::" in node:
        test_path = resource_path(root, Path(node.split("::", 1)[0]))
        if test_path.is_file() and not test_path.is_symlink():
            try:
                test_blob = _file_identity(root, test_path)
            except ValueError:
                test_blob = b"missing"
    return _sha(
        b"opsx-scenario-row-fresh\0"
        + scenario_id.encode("utf-8") + b"\0"
        + node.encode("utf-8") + b"\0"
        + scenario + b"\0" + test_blob
    )


def select_reusable_rows(
    *,
    plan_ids: list[str],
    mapping: Mapping[str, str],
    current_scope: Mapping[str, Any],
    prior_ledger: Mapping[str, Any] | None,
) -> tuple[dict[str, dict], list[str], dict[str, dict]]:
    """Reuse only supported rows retaining exact source and LLM-audit provenance."""
    selected: dict[str, dict] = {}
    origins: dict[str, dict] = {}
    if (
        prior_ledger is not None
        and prior_ledger.get("verdict") == "ROW_SET_VALID"
        and prior_ledger.get("contract") == ROW_CONTRACT
        and prior_ledger.get("shared_digest") == current_scope["shared_digest"]
        and isinstance(prior_ledger.get("rows"), list)
    ):
        seen: set[str] = set()
        for obj in prior_ledger["rows"]:
            if not isinstance(obj, dict) or not isinstance(obj.get("row"), dict):
                return {}, plan_ids[:], {}
            sid = obj["row"].get("scenario_id")
            if not isinstance(sid, str) or sid in seen:
                return {}, plan_ids[:], {}
            seen.add(sid)
            if (
                sid not in mapping or sid not in plan_ids
                or obj["row"].get("assessment") != "SUPPORTED"
                or obj["row"].get("test_node_id") != mapping[sid]
                or not current_scope["row_digests"].get(sid)
                or obj.get("row_digest") != current_scope["row_digests"].get(sid)
                or not isinstance(obj.get("origin_execution_id"), str)
                or not obj["origin_execution_id"].strip()
                or not isinstance(obj.get("evidence_id"), str)
                or not obj["evidence_id"].strip()
            ):
                continue
            selected[sid] = obj["row"]
            origins[sid] = {
                "origin_execution_id": obj["origin_execution_id"],
                "evidence_id": obj["evidence_id"],
            }
    return selected, [sid for sid in plan_ids if sid not in selected], origins


def ledger_from_validated_rows(
    *,
    plan_ids: list[str],
    current_scope: Mapping[str, Any],
    row_reviews: list[Mapping[str, Any]],
    provenance: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Caller must validate all rows/proofs; store original LLM-audit authority per row."""
    if [r.get("scenario_id") for r in row_reviews] != plan_ids:
        raise ValueError("ROW_LEDGER_INCOMPLETE")
    entries = []
    for row in row_reviews:
        sid = row["scenario_id"]
        if row.get("assessment") == "SUPPORTED" and current_scope["row_digests"].get(sid):
            origin = provenance.get(sid)
            if (
                not isinstance(origin, Mapping)
                or not origin.get("origin_execution_id")
                or not origin.get("evidence_id")
            ):
                raise ValueError("ROW_EVIDENCE_UNBOUND")
            entries.append({
                "row": dict(row), "row_digest": current_scope["row_digests"][sid],
                "origin_execution_id": origin["origin_execution_id"],
                "evidence_id": origin["evidence_id"],
            })
    return {
        "verdict": "ROW_SET_VALID", "contract": ROW_CONTRACT,
        "shared_digest": current_scope["shared_digest"], "rows": entries,
    }


def assemble_rows(
    *,
    plan_ids: list[str],
    cached: Mapping[str, Mapping[str, Any]],
    audited_ids: list[str],
    audited_rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The Gate demands one current evidence row for every frozen plan row."""
    actual = [row.get("scenario_id") for row in audited_rows]
    if actual != audited_ids or len(actual) != len(set(actual)):
        raise ValueError("DELTA_AUDIT_INCOMPLETE")
    if set(cached) & set(audited_ids):
        raise ValueError("DELTA_AUDIT_OVERLAP")
    if len(plan_ids) != len(set(plan_ids)) or set(cached) | set(audited_ids) != set(plan_ids):
        raise ValueError("DELTA_AGGREGATE_INCOMPLETE")
    by_id = {**cached, **dict(zip(audited_ids, audited_rows))}
    return [dict(by_id[sid]) for sid in plan_ids]
