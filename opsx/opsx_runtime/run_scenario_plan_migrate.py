"""CLI миграции scenario-coverage: ``--check``, ``--write``, ``--write-tests``.

Проверяет changes и archive. ``--check`` (режим по умолчанию) ничего не пишет.
``--write`` выносит строки сценариев в файлы и оставляет change-level ``plan.md``.
``--write-tests`` переносит тесты в canonical layout (целиком или AST-split).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agents.common.scenario_migrate import (
    check_coverage,
    iter_coverage_dirs,
    write_coverage,
    write_tests_for_change,
)


def _change_id(coverage: Path) -> str:
    return coverage.parent.name


def execute(repo: Path, *, write: bool, write_tests: bool) -> int:
    """Выполнить выбранный режим. Ненулевой код — раскладка не сошлась."""
    failures = 0
    for coverage in iter_coverage_dirs(repo):
        label = coverage.relative_to(repo).as_posix()
        if write:
            write_coverage(coverage)
        if write_tests:
            layout_problems = check_coverage(
                coverage, repo=repo, require_canonical_tests=False,
            )
            if layout_problems:
                failures += 1
                print(f"FAIL {label}: {', '.join(layout_problems)}")
                continue
            report = write_tests_for_change(repo, _change_id(coverage))
            if report["repair"]:
                print(
                    f"REPAIR {label}: {len(report['repair'])} scenario rows "
                    "need scenario test repair"
                )
            problems = check_coverage(coverage, repo=repo, require_canonical_tests=True)
            if problems:
                failures += 1
                print(f"FAIL {label}: {', '.join(problems)}")
                continue
            print(f"OK {label}: tests_moved={len(report['moved'])}")
        else:
            problems = check_coverage(coverage, repo=repo)
            if problems:
                failures += 1
                print(f"FAIL {label}: {', '.join(problems)}")
                continue
            print(f"OK {label}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    """Разобрать аргументы и запустить миграцию от корня репозитория."""
    parser = argparse.ArgumentParser(description="Migrate scenario plans to one file per scenario")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true", help="Split plan rows into scenarios/")
    parser.add_argument(
        "--write-tests",
        action="store_true",
        help="Move scenario tests to tests/scenarios/<change>/ (split or copy)",
    )
    parser.add_argument("--check", action="store_true", help="Validate layout (default)")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    return execute(repo, write=args.write, write_tests=args.write_tests)


if __name__ == "__main__":
    sys.exit(main())
