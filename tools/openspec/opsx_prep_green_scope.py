"""CLI wrapper — DEPRECATED for discovery; use /opsx-prep-green-scope (agent)."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    msg = (
        "tools/openspec/opsx_prep_green_scope.py is DEPRECATED.\n"
        "STAGE1: Owner /opsx-prep-green-scope → agent runs /opsx-static-check FULL.\n"
        "See .cursor/pipeline/prep-green-scope/AGENT_PLAYBOOK.md\n"
    )
    print(msg, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
