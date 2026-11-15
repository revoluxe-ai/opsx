# -*- coding: utf-8 -*-
"""Исполнение opsx bounded-verify gate checks (exit 0 PASS, 1 FAIL, 3 BLOCKED)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
EXIT_BLOCKED = 3


def _change_id() -> str:
    cid = os.environ.get("BOUNDED_VERIFY_CHANGE_ID", "").strip()
    if not cid:
        print("BOUNDED_VERIFY_CHANGE_ID missing", file=sys.stderr)
        sys.exit(EXIT_BLOCKED)
    return cid


def _node_json(script_rel: str, args: list[str]) -> dict:
    completed = subprocess.run(
        ["node", str(REPO_ROOT / script_rel), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        print(completed.stderr or completed.stdout, file=sys.stderr)
        sys.exit(1)
    return json.loads(completed.stdout)


def gate_split_apply_impl_ready() -> None:
    change_id = _change_id()
    rel = f"openspec/changes/{change_id}"
    payload = _node_json(
        "opsx_tools/opsx-apply-verify-completion.mjs",
        ["--change-root", rel],
    )
    if not payload.get("split_apply"):
        return
    marker = str(payload.get("marker") or "IMPL_GAPS")
    if marker != "IMPL_OK":
        print(f"split-apply marker={marker}", file=sys.stderr)
        sys.exit(1)


def gate_apply_green_fresh() -> None:
    change_id = _change_id()
    rel = f"openspec/changes/{change_id}"
    data = _node_json("opsx_tools/bounded-verify-list-stale-green.mjs", [rel])
    stale = data.get("stale") or []
    if stale:
        print(json.dumps(stale), file=sys.stderr)
        sys.exit(1)


def gate_scenario_plan_receipt() -> None:
    change_id = _change_id()
    receipt = (
        REPO_ROOT
        / "openspec"
        / "changes"
        / change_id
        / "scenario-coverage"
        / "plan.receipt.json"
    )
    if not receipt.is_file():
        print("missing scenario-coverage/plan.receipt.json", file=sys.stderr)
        sys.exit(EXIT_BLOCKED)
    plan_md = receipt.parent / "plan.md"
    if not plan_md.is_file():
        print("missing scenario-coverage/plan.md", file=sys.stderr)
        sys.exit(EXIT_BLOCKED)


def gate_pg_provider_receipt() -> None:
    """Owner-gated PG: BLOCKED until PROVIDER_EXECUTION_RECEIPT exists with PASS."""
    change_id = _change_id()
    receipts_dir = (
        REPO_ROOT / "opsx" / "runs" / "scenario-coverage" / change_id / "receipts"
    )
    if not receipts_dir.is_dir():
        print("PROVIDER_EXECUTION_RECEIPT absent", file=sys.stderr)
        sys.exit(EXIT_BLOCKED)
    for path in sorted(receipts_dir.glob("PROVIDER-*.md")):
        text = path.read_text(encoding="utf-8")
        if "RESULT: PASS" in text:
            return
    print("no PASS provider receipt", file=sys.stderr)
    sys.exit(EXIT_BLOCKED)


GATES = {
    "opsx-split-apply-impl-ready": gate_split_apply_impl_ready,
    "opsx-apply-green-fresh": gate_apply_green_fresh,
    "opsx-scenario-plan-receipt": gate_scenario_plan_receipt,
    "opsx-pg-provider-receipt": gate_pg_provider_receipt,
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: run_gate.py <check_id>", file=sys.stderr)
        return 2
    gate_id = argv[1]
    fn = GATES.get(gate_id)
    if fn is None:
        print(f"unknown gate: {gate_id}", file=sys.stderr)
        return 2
    fn()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
