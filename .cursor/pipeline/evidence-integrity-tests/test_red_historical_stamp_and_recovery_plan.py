"""REG: operative stamp vs historical RED; RECOVERY_PAUSED plan surfaces recovery code."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
VISUAL_RED_002 = REPO / "openspec/changes/visual/apply-red/SLICE-002/RED.json"
VISUAL_IMP_002 = REPO / "openspec/changes/visual/apply-implement/SLICE-002/IMPLEMENTATION.json"
VISUAL_RED_003 = REPO / "openspec/changes/visual/apply-red/SLICE-003/RED.json"
VISUAL_IMP_003 = REPO / "openspec/changes/visual/apply-implement/SLICE-003/IMPLEMENTATION.json"
FIX_RED = (
    REPO
    / ".cursor"
    / "commands"
    / "_fixtures"
    / "opsx-apply-red"
    / "03-red-proven.RED.json"
)


def _node_eval(script: str) -> dict[str, object]:
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_visual_slice003_canonical_impl_historical_bound() -> None:
    """REG: canonical frozen identity on IMPLEMENT after operative RED (algorithm drift)."""
    payload = _node_eval(
        f"""
        import {{ readFileSync }} from 'fs';
        const root = {json.dumps(str(REPO))};
        const red = JSON.parse(readFileSync({json.dumps(str(VISUAL_RED_003))}, 'utf8'));
        const imp = JSON.parse(readFileSync({json.dumps(str(VISUAL_IMP_003))}, 'utf8'));
        const m = await import('./opsx_tools/opsx-apply-pipeline-identity.mjs');
        console.log(JSON.stringify({{
          hist: m.isHistoricalRedProvenBound(red, imp, root),
          recovery: m.classifyPostImplementGreenRecovery({{ red, implement: imp, repoRoot: root }}),
        }}));
        """
    )
    recovery = payload["recovery"]
    assert payload["hist"] is True
    assert isinstance(recovery, dict)
    assert recovery.get("disposition") == "GREEN_REFRESH_AUTHORIZED"


def test_visual_slice002_historical_red_bound_after_implement() -> None:
    """После IMPLEMENT: historical gate + operative digest stamp → GREEN_REFRESH."""
    payload = _node_eval(
        f"""
        import {{ readFileSync }} from 'fs';
        import {{ resolve }} from 'path';
        const root = {json.dumps(str(REPO))};
        const red = JSON.parse(readFileSync({json.dumps(str(VISUAL_RED_002))}, 'utf8'));
        const imp = JSON.parse(readFileSync({json.dumps(str(VISUAL_IMP_002))}, 'utf8'));
        const m = await import('./opsx_tools/opsx-apply-pipeline-identity.mjs');
        const bind = await import('./opsx_tools/opsx-apply-red-bindings.mjs');
        const hist = m.isHistoricalRedProvenBound(red, imp, root);
        const recovery = m.classifyPostImplementGreenRecovery({{ red, implement: imp, repoRoot: root }});
        const auth = bind.getRedOperativeTransitionAuthorized();
        const idm = await import('./opsx_tools/opsx-apply-pipeline-identity.mjs');
        const histAuth = auth(red, root, {{ validationPhase: idm.RED_VALIDATION_PHASE_HISTORICAL }});
        console.log(JSON.stringify({{ hist, histAuth, recovery: recovery.disposition, code: recovery.code }}));
        """
    )
    assert payload["hist"] is True
    assert payload["histAuth"] is True
    assert payload["recovery"] == "GREEN_REFRESH_AUTHORIZED"


def test_forged_decision_digest_rejected() -> None:
    base = json.loads(FIX_RED.read_text(encoding="utf-8"))
    base["operative_red_transition"] = {
        "verifier_id": "opsx-apply-red-transition-gate",
        "permitted_transition": "RED_PROVEN",
        "decision_digest": "0" * 64,
    }
    payload = _node_eval(
        f"""
        const red = {json.dumps(base)};
        const bind = await import('./opsx_tools/opsx-apply-red-bindings.mjs');
        const idm = await import('./opsx_tools/opsx-apply-pipeline-identity.mjs');
        const auth = bind.getRedOperativeTransitionAuthorized();
        console.log(JSON.stringify({{
          hist: auth(red, {json.dumps(str(REPO))}, {{ validationPhase: idm.RED_VALIDATION_PHASE_HISTORICAL }})
        }}));
        """
    )
    assert payload["hist"] is False


def test_slice_mismatch_blocks_historical_bound() -> None:
    red = json.loads(VISUAL_RED_002.read_text(encoding="utf-8"))
    imp = json.loads(VISUAL_IMP_002.read_text(encoding="utf-8"))
    imp["slice_id"] = "SLICE-XXX"
    payload = _node_eval(
        f"""
        const red = {json.dumps(red)};
        const imp = {json.dumps(imp)};
        const m = await import('./opsx_tools/opsx-apply-pipeline-identity.mjs');
        console.log(JSON.stringify({{
          hist: m.isHistoricalRedProvenBound(red, imp, {json.dumps(str(REPO))})
        }}));
        """
    )
    assert payload["hist"] is False


def test_visual_plan_not_recovery_pause_with_false_unknown_stage() -> None:
    """Plan для visual: APPLY_GREEN на текущем слайсе, не IMPLEMENT_RED_LINEAGE_BLOCKED."""
    proc = subprocess.run(
        [
            "node",
            str(REPO / "opsx_tools/opsx-super-implement.mjs"),
            "plan",
            "--change-root",
            "openspec/changes/visual",
            "--live-execution",
            "true",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    body = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert body["status"] == "RUNNING", body
    assert "undefined" not in str(body.get("primitive_invocation", ""))
    assert body.get("next_stage") == "APPLY_GREEN"
    assert body.get("next_slice") == "SLICE-003"
    assert body.get("code") not in (
        "IMPLEMENT_RED_LINEAGE_BLOCKED",
        "IMPLEMENT_RED_LINEAGE_AMBIGUOUS",
    )

