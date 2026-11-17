#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opsx_cli.command_reconcile import build_command_matrix
from opsx_cli.migration import _latest_backup_commands


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--core", type=Path, default=ROOT)
    parser.add_argument("--legacy-commands", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--waivers", type=Path, default=None)
    args = parser.parse_args()
    legacy = args.legacy_commands
    if legacy is None:
        from opsx_cli.context import Project
        legacy = _latest_backup_commands(Project.at(args.project))
    waivers = {}
    if args.waivers and args.waivers.is_file():
        waivers = json.loads(args.waivers.read_text(encoding="utf-8"))
    matrix = build_command_matrix(args.project, core=args.core, legacy_commands_root=legacy, waivers=waivers)
    out = args.output or (args.project / "opsx/project/command-reconciliation.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "status": matrix["status"], "blocked": matrix["blocked"], "proven": matrix["proven_or_waived"]}))
    return 0 if matrix["status"] != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())