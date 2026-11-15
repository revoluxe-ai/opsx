"""Регрессия: canonical/legacy frozen identity → GREEN dispatch (без live visual artifacts)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]
FIX_RED = (
    REPO
    / ".cursor"
    / "commands"
    / "_fixtures"
    / "opsx-apply-red"
    / "03-red-proven.RED.json"
)
FIX_IMP = (
    REPO
    / ".cursor"
    / "commands"
    / "_fixtures"
    / "opsx-apply-implement"
    / "03-implementation-written.IMPLEMENTATION.json"
)


def _node(script: str) -> dict[str, object]:
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_legacy_fixture_historical_bound() -> None:
    """Golden 03: legacy frozen identity pairs RED + IMPLEMENT for historical gate."""
    payload = _node(
        f"""
        import {{ readFileSync }} from 'fs';
        const root = {json.dumps(str(REPO))};
        const red = JSON.parse(readFileSync({json.dumps(str(FIX_RED))}, 'utf8'));
        const imp = JSON.parse(readFileSync({json.dumps(str(FIX_IMP))}, 'utf8'));
        const m = await import('./opsx_tools/opsx-apply-pipeline-identity.mjs');
        console.log(JSON.stringify({{
          match: m.implementFrozenTestIdentityMatchesRedBytes(red, imp, root),
          hist: m.isHistoricalRedProvenBound(red, imp, root),
        }}));
        """
    )
    assert isinstance(payload["match"], dict)
    assert payload["match"].get("ok") is True
    assert payload["hist"] is True


def test_forged_impl_frozen_rejected() -> None:
    red = json.loads(FIX_RED.read_text(encoding="utf-8"))
    imp = json.loads(FIX_IMP.read_text(encoding="utf-8"))
    imp["frozen_test_content_identity"] = "f" * 64
    payload = _node(
        f"""
        const red = {json.dumps(red)};
        const imp = {json.dumps(imp)};
        const m = await import('./opsx_tools/opsx-apply-pipeline-identity.mjs');
        console.log(JSON.stringify({{
          hist: m.isHistoricalRedProvenBound(red, imp, {json.dumps(str(REPO))}),
          recovery: m.classifyPostImplementGreenRecovery({{ red, implement: imp, repoRoot: {json.dumps(str(REPO))} }}),
        }}));
        """
    )
    assert payload["hist"] is False
    recovery = payload["recovery"]
    assert isinstance(recovery, dict)
    assert recovery.get("disposition") in (
        "FROZEN_TEST_MUTATED_REQUIRES_RE_RED",
        "BLOCKED",
    )


def test_hop_lane_dispatch_includes_machine_errors() -> None:
    from agents.implementation.hop_lane_status import graph_dispatch_answer

    state = {
        "answer": {"slice_id": "SLICE-003"},
        "implementation_machine_gate_errors": [
            {"code": "R2_EXECUTION", "message": "exit_code mismatch"},
        ],
    }
    out = graph_dispatch_answer(state, "SLICE-003")
    assert out.get("slice_id") == "SLICE-003"
    errors = out.get("machine_gate_errors")
    assert isinstance(errors, list)
    assert errors[0]["code"] == "R2_EXECUTION"
