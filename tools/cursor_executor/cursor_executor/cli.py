"""Minimal CLI for adapter dogfood (no secrets on argv)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .executor import CursorExecutor
from .schemas import CursorExecutionRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cursor executor adapter V0.1")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("execute", help="Execute a request JSON file")
    run_p.add_argument("--request", required=True, help="Path to request JSON")

    rec_p = sub.add_parser("reconcile", help="Reconcile by execution_id")
    rec_p.add_argument("--execution-id", required=True)

    args = parser.parse_args(argv)
    executor = CursorExecutor()

    if args.command == "execute":
        payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
        request = CursorExecutionRequest.from_json(payload)
        result = asyncio.run(executor.execute(request))
        print(json.dumps(result.to_json(), indent=2))
        return 0 if result.terminal_class.value == "SUCCEEDED" else 1

    if args.command == "reconcile":
        result = asyncio.run(executor.reconcile(args.execution_id))
        print(json.dumps(result.to_json(), indent=2))
        return 0 if result.terminal_class.value == "SUCCEEDED" else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
