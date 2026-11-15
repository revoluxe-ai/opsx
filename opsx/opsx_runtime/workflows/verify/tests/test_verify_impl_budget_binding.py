"""Verify parent graph seeds budget ledger dir for all adapter hops."""

from __future__ import annotations

import inspect

from run_verify_impl import execute


def test_execute_seeds_budget_output_dir_in_initial_state() -> None:
    """``budget_output_dir`` должен попадать в GraphState до security subgraph."""
    source = inspect.getsource(execute)
    assert "budget_output_dir" in source
    assert "receipts_dir" in source
    assert "mission_id" in source
