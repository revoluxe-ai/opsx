#!/usr/bin/env python3
"""Trusted verification entrypoint for CI (policy from verifier code, not Change tree)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_GATE_DIR = Path(__file__).resolve().parent
if str(_GATE_DIR) not in sys.path:
    sys.path.insert(0, str(_GATE_DIR))

from trusted_policy_v1 import TRUST_POLICY_IMPLEMENTATION_ID  # noqa: E402


def _load_gate():
    spec = importlib.util.spec_from_file_location("planning_gate", _GATE_DIR / "planning_gate.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Architecture evidence trusted CI verify")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--change", required=True)
    parser.add_argument(
        "--phase",
        choices=("planning-review", "apply-preflight"),
        default="apply-preflight",
    )
    args = parser.parse_args(argv)
    gate = _load_gate()
    repo_root = args.repo_root.resolve()
    if args.phase == "planning-review":
        outcome = gate.run_planning_review_gate(args.change, repo_root, trust_policy="production")
    else:
        outcome = gate.run_apply_preflight_gate(args.change, repo_root, trust_policy="production")
    payload = {
        "ok": outcome.ok,
        "phase": outcome.phase,
        "trust_policy_implementation_id": TRUST_POLICY_IMPLEMENTATION_ID,
        "gate_implementation_id": gate.GATE_IMPLEMENTATION_ID,
        "findings": [{"code": f.code, "detail": f.detail} for f in outcome.findings],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
