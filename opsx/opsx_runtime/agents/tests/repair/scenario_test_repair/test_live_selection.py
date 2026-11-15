"""Regression for one explicit pytest marker controlling real model invocation."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest


def _selection_plugin():
    path = Path(__file__).with_name("conftest.py")
    spec = spec_from_file_location("scenario_repair_test_selection_plugin", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _LiveItem:
    def __init__(self):
        self.marks = []

    def get_closest_marker(self, name):
        return pytest.mark.live_agent if name == "live_agent" else None

    def add_marker(self, mark):
        self.marks.append(mark)


@pytest.mark.parametrize(
    "markexpr,should_skip",
    [
        ("", True),
        ("not live_agent", True),
        ("opsx_pipeline", True),
        ("live_agent", False),
    ],
)
def test_only_explicit_live_agent_marker_enables_sdk_tests(
    markexpr, should_skip, monkeypatch,
):
    # A leftover live environment must not authorize a default pytest run.
    monkeypatch.setenv("OPSX_SCENARIO_REPAIR_LIVE", "1")
    item = _LiveItem()
    _selection_plugin().pytest_collection_modifyitems(
        SimpleNamespace(option=SimpleNamespace(markexpr=markexpr)),
        [item],
    )
    assert bool(item.marks) is should_skip
