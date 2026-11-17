#!/usr/bin/env python3
"""Rewrite donor authority banners in canonical Cursor command sources."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opsx_cli.authority import DONOR_OVERRIDE_HEADING, assert_portable_managed_content, neutralize_command_authority

GLOBS = (
    ".cursor/commands/*.md",
    ".cursor/skills/**/SKILL.md",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Verify only; do not write")
    args = parser.parse_args()
    changed = 0
    violations = 0
    for pattern in GLOBS:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if not DONOR_OVERRIDE_HEADING.search(text) and "STROY-SYSTEM" not in text:
                try:
                    assert_portable_managed_content(path.as_posix(), text)
                except Exception:
                    violations += 1
                continue
            updated = neutralize_command_authority(text)
            try:
                assert_portable_managed_content(path.relative_to(ROOT).as_posix(), updated)
            except Exception:
                violations += 1
                continue
            if updated != text:
                changed += 1
                if not args.check:
                    path.write_text(updated, encoding="utf-8")
    print({"changed": changed, "violations": violations, "check_only": args.check})
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())