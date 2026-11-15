"""Регрессия repair-scaffold-import-cycle: цепочка импортов split-apply prompt без цикла."""

from __future__ import annotations

import importlib

import pytest

# Модули из traceback run-visual-17 (launcher_import).
_IMPORT_CHAIN = (
    "agents.implementation.prompt.build_split_apply_prompt",
    "agents.implementation.prompt.slice_input_pack",
    "agents.implementation.apply_scaffold.prompt.get_prompt",
    "agents.implementation.apply_design.prompt.get_prompt",
)


@pytest.mark.unit
def test_split_apply_prompt_import_chain_has_no_circular_import() -> None:
    """Повторяет порядок загрузки CLI: каждый модуль импортируется без ImportError."""
    loaded: list[str] = []
    for module_name in _IMPORT_CHAIN:
        mod = importlib.import_module(module_name)
        loaded.append(module_name)
        assert mod is not None
    assert len(loaded) == len(_IMPORT_CHAIN)
