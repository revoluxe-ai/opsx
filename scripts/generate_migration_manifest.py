#!/usr/bin/env python3
"""Generate consumer migration-manifest.json from legacy tree inventory."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opsx_cli.context import Project
from opsx_cli.migration import build_plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--core", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    plan = build_plan(Project.at(args.project), core=args.core)
    out = args.output or (args.project / "opsx/project/migration-manifest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "schema_version": 1,
        "target": args.project.name,
        "generated_at": plan.get("generated_at"),
        "status": plan.get("status"),
        "entries": plan.get("entries", []),
    }, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "status": plan["status"], "entries": plan["entry_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())