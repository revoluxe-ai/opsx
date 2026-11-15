"""Preflight validation for pytest disable shapes (fail-closed)."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class FunctionSkipPlan:
    kind: Literal["function_skip"]
    path: Path
    func_name: str


@dataclass(frozen=True)
class ParamExplicitIdPlan:
    kind: Literal["param_explicit_id"]
    path: Path
    func_name: str
    param_id: str


@dataclass(frozen=True)
class ParamUniqueScalarPlan:
    kind: Literal["param_unique_scalar"]
    path: Path
    func_name: str
    param_id: str
    scalar_value: str


PytestDisablePlan = FunctionSkipPlan | ParamExplicitIdPlan | ParamUniqueScalarPlan


def _func_def_name(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return node.name


def _parametrize_calls(func_node: ast.AST) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for dec in getattr(func_node, "decorator_list", []):
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if dec.func.attr == "parametrize":
                calls.append(dec)
    return calls


def _literal_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
        return str(node.value)
    return None


def _collect_explicit_param_ids(call: ast.Call) -> dict[str, ast.AST]:
    """Map pytest node id segment → param AST for explicit id= on pytest.param."""

    mapping: dict[str, ast.AST] = {}
    if len(call.args) < 2:
        return mapping
    values = call.args[1]
    if not isinstance(values, (ast.List, ast.Tuple)):
        return mapping
    for elt in values.elts:
        if isinstance(elt, ast.Call) and isinstance(elt.func, ast.Name):
            if elt.func.id != "pytest.param":
                continue
            pid: str | None = None
            for kw in elt.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        pid = kw.value.value
            if pid:
                mapping[pid] = elt
    return mapping


def _unique_scalar_literals(call: ast.Call) -> dict[str, int]:
    """Count scalar literal appearances in a simple list parametrize values arg."""

    counts: dict[str, int] = {}
    if len(call.args) < 2 or not isinstance(call.args[1], ast.List):
        return counts
    for elt in call.args[1].elts:
        if isinstance(elt, ast.Call):
            return {}
        lit = _literal_str(elt)
        if lit is None:
            return {}
        counts[lit] = counts.get(lit, 0) + 1
    return counts


def _parametrize_has_unsupported_ids(call: ast.Call) -> bool:
    """``ids=`` on ``@pytest.mark.parametrize`` (list/callable) is unsupported."""

    for kw in call.keywords:
        if kw.arg == "ids":
            return True
    return False


def _param_call_has_complex_marks(param_call: ast.Call) -> bool:
    for kw in param_call.keywords:
        if kw.arg == "marks" and kw.value is not None:
            return True
    return False


def validate_pytest_disable(root: Path, node_id: str) -> PytestDisablePlan | None:
    """Return disable plan or None when shape is unsupported (fail-closed)."""

    if "::" not in node_id:
        return None
    rel, func_part = node_id.split("::", 1)
    path = root / rel
    if not path.is_file():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    func_name = func_part.split("[", 1)[0]
    param_id = func_part.split("[", 1)[1].rstrip("]") if "[" in func_part else None
    target_func: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _func_def_name(node) == func_name:
                target_func = node
                break
    if target_func is None:
        return None
    if param_id is None:
        return FunctionSkipPlan(kind="function_skip", path=path, func_name=func_name)
    for call in _parametrize_calls(target_func):
        if _parametrize_has_unsupported_ids(call):
            return None
        explicit = _collect_explicit_param_ids(call)
        if param_id in explicit:
            if _param_call_has_complex_marks(explicit[param_id]):
                return None
            return ParamExplicitIdPlan(
                kind="param_explicit_id",
                path=path,
                func_name=func_name,
                param_id=param_id,
            )
        if isinstance(call.args[1], ast.Tuple):
            return None
        if isinstance(call.args[1], ast.List) and any(
            isinstance(e, ast.Call) for e in call.args[1].elts
        ):
            return None
        counts = _unique_scalar_literals(call)
        if not counts or counts.get(param_id, 0) != 1:
            return None
        return ParamUniqueScalarPlan(
            kind="param_unique_scalar",
            path=path,
            func_name=func_name,
            param_id=param_id,
            scalar_value=param_id,
        )
    return None


def apply_validated_pytest_plan(
    plan: PytestDisablePlan,
    *,
    reason: str,
) -> bool:
    """Apply a pre-validated pytest disable plan to source."""

    text = plan.path.read_text(encoding="utf-8")
    if "import pytest" not in text:
        text = "import pytest\n" + text
    if plan.kind == "function_skip":
        marker = f'@pytest.mark.skip(reason="{reason}")'
        if marker in text:
            return True
        needle = f"def {plan.func_name}("
        idx = text.find(needle)
        if idx < 0:
            return False
        line_start = text.rfind("\n", 0, idx) + 1
        indent = text[line_start:idx]
        text = text[:line_start] + f'{indent}@pytest.mark.skip(reason="{reason}")\n' + text[line_start:]
        plan.path.write_text(text, encoding="utf-8")
        return True
    if plan.kind == "param_explicit_id":
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == plan.func_name:
                for call in _parametrize_calls(node):
                    mapping = _collect_explicit_param_ids(call)
                    if plan.param_id not in mapping:
                        continue
                    old = ast.get_source_segment(text, mapping[plan.param_id]) or ""
                    if "pytest.mark.skip" in old:
                        return True
                    skip_kw = f'marks=pytest.mark.skip(reason="{reason}")'
                    if "marks=" in old:
                        return False
                    new = old.replace("pytest.param(", f"pytest.param(", 1)
                    if old.endswith(")"):
                        new = old[:-1] + f", {skip_kw})"
                    text = text.replace(old, new, 1)
                    plan.path.write_text(text, encoding="utf-8")
                    return True
        return False
    if plan.kind == "param_unique_scalar":
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == plan.func_name:
                for call in _parametrize_calls(node):
                    if len(call.args) < 2 or not isinstance(call.args[1], ast.List):
                        continue
                    for elt in call.args[1].elts:
                        lit = _literal_str(elt)
                        if lit != plan.scalar_value:
                            continue
                        old = ast.get_source_segment(text, elt) or lit
                        if "pytest.mark.skip" in old:
                            return True
                        new = (
                            f"pytest.param({plan.scalar_value}, "
                            f"marks=pytest.mark.skip(reason={reason!r}))"
                        )
                        text = text.replace(old, new, 1)
                        if "import pytest" not in text:
                            text = "import pytest\n" + text
                        plan.path.write_text(text, encoding="utf-8")
                        return True
        return False
    return False


def verify_pytest_plan_applied(plan: PytestDisablePlan, *, reason: str) -> bool:
    """Post-apply: source reflects the planned disable (collect-safe check)."""

    text = plan.path.read_text(encoding="utf-8")
    if plan.kind == "function_skip":
        pat = rf"@pytest\.mark\.skip\([^)]+\)\s*\n(?:async\s+def|def)\s+{re.escape(plan.func_name)}\s*\("
        return re.search(pat, text, re.MULTILINE) is not None
    if plan.kind == "param_explicit_id":
        return "pytest.mark.skip" in text and (
            f'id="{plan.param_id}"' in text or f"id='{plan.param_id}'" in text
        )
    if plan.kind == "param_unique_scalar":
        return (
            f"pytest.param({plan.scalar_value}" in text and "pytest.mark.skip" in text
        )
    return False


def collect_pytest_file_paths(plans: list[PytestDisablePlan]) -> list[Path]:
    """Unique pytest files in a prepared delta."""

    return sorted({p.path for p in plans}, key=lambda p: p.as_posix())
