"""Idempotent skip design lane при единственном drift protected content_digest."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from workflows.implementation.design_machine_validation import (
    DesignDependentSnapshotResult,
    DesignMachineGateResult,
    design_lane_reuse_ok_after_dependent_snapshot,
)
from workflows.implementation.design_pipeline.graph import _design_lane_idempotent_skip


def test_digest_only_drift_allows_lane_skip() -> None:
    """PROTECTED_CONTEXT_DRIFT + gate PASS → lane skip, файл design не трогаем."""
    snap = DesignDependentSnapshotResult(
        ok=False,
        code="PROTECTED_CONTEXT_DRIFT",
        frozen_digest="a04c1a3a",
        current_digest="ff9fe78d",
    )
    assert design_lane_reuse_ok_after_dependent_snapshot(
        snap,
        machine_gate_ok=True,
    )
    repo = Path("C:/repo")
    with (
        patch(
            "workflows.implementation.design_pipeline.graph.load_apply_design",
            return_value={"status": "APPLY_DESIGN_READY"},
        ),
        patch(
            "workflows.implementation.design_pipeline.graph.is_apply_design_review_fresh",
            return_value=True,
        ),
        patch(
            "workflows.implementation.design_machine_validation.validate_apply_design_machine",
            return_value=DesignMachineGateResult(ok=True, status="PASS", exit_code=0, errors=[]),
        ),
        patch(
            "workflows.implementation.design_machine_validation.evaluate_apply_design_dependent_snapshot",
            return_value=snap,
        ),
    ):
        assert _design_lane_idempotent_skip("visual", repo_root=repo) is True


def test_gate_failure_blocks_skip_despite_digest_drift() -> None:
    """Stub / невалидный канон: gate FAIL — агент design обязателен."""
    snap = DesignDependentSnapshotResult(
        ok=False,
        code="PROTECTED_CONTEXT_DRIFT",
        frozen_digest="a04c1a3a",
        current_digest="ff9fe78d",
    )
    assert not design_lane_reuse_ok_after_dependent_snapshot(
        snap,
        machine_gate_ok=False,
    )
    stub_gate = DesignMachineGateResult(
        ok=False,
        status="FAIL",
        exit_code=1,
        errors=[{"code": "DESIGN_INCOMPLETE", "message": "stub"}],
    )
    repo = Path("C:/repo")
    with (
        patch(
            "workflows.implementation.design_pipeline.graph.load_apply_design",
            return_value={"status": "APPLY_DESIGN_READY"},
        ),
        patch(
            "workflows.implementation.design_pipeline.graph.is_apply_design_review_fresh",
            return_value=True,
        ),
        patch(
            "workflows.implementation.design_machine_validation.validate_apply_design_machine",
            return_value=stub_gate,
        ),
        patch(
            "workflows.implementation.design_machine_validation.evaluate_apply_design_dependent_snapshot",
            return_value=snap,
        ),
    ):
        assert _design_lane_idempotent_skip("visual", repo_root=repo) is False


def test_planning_stale_blocks_digest_only_reuse() -> None:
    """STALE_APPLY_DESIGN — не считается единственным protected drift."""
    snap = DesignDependentSnapshotResult(
        ok=False,
        code="STALE_APPLY_DESIGN",
        frozen_digest="p1",
        current_digest="p2",
    )
    assert not design_lane_reuse_ok_after_dependent_snapshot(
        snap,
        machine_gate_ok=True,
    )
