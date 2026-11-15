"""Извлечение литералов констант из Python-исходника (без import runtime)."""

from __future__ import annotations

import ast
from typing import Any


def _eval_const(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        if isinstance(node, ast.Set):
            return {_eval_const(elt) for elt in node.elts}
        if isinstance(node, ast.List):
            return [_eval_const(elt) for elt in node.elts]
        return tuple(_eval_const(elt) for elt in node.elts)
    if isinstance(node, ast.Dict):
        return {
            _eval_const(k): _eval_const(v)
            for k, v in zip(node.keys, node.values, strict=True)
        }
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "frozenset" and len(node.args) == 1:
            inner = _eval_const(node.args[0])
            return frozenset(inner)
    raise ValueError(f"unsupported literal node: {ast.dump(node)}")


def literal_from_module(source: str, name: str) -> Any:
    """Вернуть значение ``name = <literal>`` из модуля."""

    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return _eval_const(node.value)
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value is not None:
                return _eval_const(node.value)
    raise KeyError(name)
