"""Archive verify hook: executable Channel Impact Guard (single mechanism)."""

from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--change", required=True)
    parser.add_argument("--base", default="origin/stage")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    script = resource_path(root, ".cursor", "pipeline", "channel-impact", "channel_impact_guard.py")
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "verify-gate",
            "--change",
            args.change,
            "--base",
            args.base,
            "--head",
            args.head,
        ],
        cwd=root,
        check=False,
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
