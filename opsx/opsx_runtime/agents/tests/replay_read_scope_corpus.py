#!/usr/bin/env python3
"""Реплей tool-call из ``*.live.ndjson`` через текущий read-scope гейт.

Запуск из ``opsx/opsx_runtime``::

    poetry run python agents/tests/replay_read_scope_corpus.py [--repo ROOT]

Ворота: 0 неожиданных нарушений на локальных путях (вне ``/agent/``, ``/home/``).
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[2]
_REPO = _RUNTIME.parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from agents.config import (  # noqa: E402
    check_paths_in_scope,
    path_allowed_by_scope,
    paths_from_stream_record,
    resolve_read_scope,
)

_FOREIGN = re.compile(r"^(/agent/|/home/|/workspace/|/tmp)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        type=Path,
        default=_REPO,
        help="Корень репозитория для repo_root",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    scope = resolve_read_scope(None)
    ndjson_files = list(repo.glob("openspec/changes/**/**/*.live.ndjson"))
    violations: collections.Counter[tuple[str, str]] = collections.Counter()
    false_positives = 0
    checked = 0
    for path in ndjson_files:
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                if '"tool' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tool, paths = paths_from_stream_record(rec)
                if not tool or not paths:
                    continue
                for raw in paths:
                    if _FOREIGN.match(raw.replace("\\", "/")):
                        continue
                    checked += 1
                    cmd = ""
                    if tool == "shell":
                        args_map = rec.get("args") or {}
                        if isinstance(args_map, dict):
                            cmd = str(
                                args_map.get("command") or args_map.get("cmd") or ""
                            )
                    result = check_paths_in_scope(
                        scope,
                        tool_name=tool,
                        paths=[raw],
                        repo_root=repo,
                        command=cmd,
                    )
                    if result.violations:
                        key = (tool, result.violations[0].rule)
                        violations[key] += 1
                        if path_allowed_by_scope(scope, raw, repo_root=repo):
                            false_positives += 1
    print(f"ndjson_files={len(ndjson_files)} paths_checked={checked}")
    print(
        f"violations_total={sum(violations.values())} "
        f"false_positives={false_positives} distinct={len(violations)}"
    )
    for (tool, rule), count in violations.most_common(40):
        print(f"  {count:5d} tool={tool} rule={rule}")
    return 1 if false_positives else 0


if __name__ == "__main__":
    raise SystemExit(main())
