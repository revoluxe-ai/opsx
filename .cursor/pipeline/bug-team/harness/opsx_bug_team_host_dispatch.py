# -*- coding: utf-8 -*-
"""CLI production dispatch boundary for /opsx-bug-team (host wiring).

Cursor host and acceptance harness invoke this script to obtain a durable
case_root before Task worker dispatch.

Usage:
  python harness/opsx_bug_team_host_dispatch.py --args "free form bug text"
  python harness/opsx_bug_team_host_dispatch.py --args "case=<UUID>" --repo /path/to/repo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.bug_team_host import dispatch_opsx_bug_team  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch /opsx-bug-team host routing")
    parser.add_argument("--args", default="", help="Command arguments (invocation string)")
    parser.add_argument("--context", default="", help="Optional context text for empty args")
    parser.add_argument("--repo", default="", help="Git repo root (default: git toplevel from cwd)")
    parser.add_argument(
        "--acceptance-store",
        default="",
        help="Harness-only isolated OPSX_BUG_CASE_STORE_ROOT fleet directory",
    )
    parser.add_argument(
        "--execution-mode",
        default="CONTROLLED_LIVE_UNCOMMITTED",
        help="Execution mode passed to activation gate",
    )
    ns = parser.parse_args()
    repo = Path(ns.repo).resolve() if ns.repo else None
    acceptance = Path(ns.acceptance_store).resolve() if ns.acceptance_store else None
    try:
        result = dispatch_opsx_bug_team(
            ns.args,
            repo_root=repo,
            context_text=ns.context,
            execution_mode=ns.execution_mode,
            acceptance_store_root=acceptance,
        )
    except Exception as exc:
        print(json.dumps({"STATUS": "DISPATCH_FAILED", "ERROR": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result.to_receipt(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
