"""Перенос legacy verify-логов и machine receipts в opsx/logs и opsx/receipts.

Использование (из корня репозитория):
  poetry run python opsx/opsx_runtime/scripts/migrate_openspec_runtime_artifacts.py --dry-run
  poetry run python opsx/opsx_runtime/scripts/migrate_openspec_runtime_artifacts.py
"""

from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import hashlib
import shutil
import sys
from pathlib import Path
from typing import Literal

_RUNTIME = Path(__file__).resolve().parents[1]
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from workflows.implementation.stage_receipt_writer import repair_visual_slice_004_false_green

_LOG_GLOBS = ("*.live.log", "*.live.ndjson", "*.conversation.json", "visual-run-*.log")
_RECEIPT_NAMES = ("IMPL-VERIFY-RECEIPT.json",)
_RECEIPT_DIRS = ("scenario-rows", "check-receipts")
_FINDINGS_SUFFIX = "-findings.json"
_APPLY_STAGES = (
    "apply-design",
    "apply-design-review",
    "apply-scaffold",
    "apply-red",
    "apply-implement",
    "apply-green",
)
_SCENARIO_MACHINE = (
    "plan.receipt.json",
    "authoring.receipt.json",
    "test_trace_matrix.json",
    "row_walk_journal.json",
)

MoveReport = Literal["moved", "kept-dest-identical", "conflict-kept-dest"]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _move_file_safe(
    src: Path,
    dest: Path,
    *,
    dry_run: bool,
    reports: list[tuple[MoveReport, str, str]],
) -> bool:
    """Перенос файла без затирания существующего назначения."""
    if not src.is_file():
        return False
    rel_src = src.as_posix()
    rel_dest = dest.as_posix()
    if dest.is_file():
        if _file_digest(src) == _file_digest(dest):
            reports.append(("kept-dest-identical", rel_src, rel_dest))
            return False
        reports.append(("conflict-kept-dest", rel_src, rel_dest))
        return False
    if dry_run:
        reports.append(("moved", rel_src, rel_dest))
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    reports.append(("moved", rel_src, rel_dest))
    return True


def _merge_tree_safe(
    src_dir: Path,
    dest_dir: Path,
    *,
    dry_run: bool,
    reports: list[tuple[MoveReport, str, str]],
) -> int:
    """Пофайловое слияние каталога без rmtree назначения."""
    count = 0
    if not src_dir.is_dir():
        return 0
    for src in sorted(src_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(src_dir)
        dest = dest_dir / rel
        if _move_file_safe(src, dest, dry_run=dry_run, reports=reports):
            count += 1
    return count


def migrate_verify_dir(
    repo: Path,
    change_id: str,
    *,
    dry_run: bool,
    reports: list[tuple[MoveReport, str, str]],
) -> dict[str, int]:
    legacy = resource_path(repo, "openspec", "changes", change_id, "verify")
    if not legacy.is_dir():
        return {"moved": 0, "removed_dirs": 0}
    log_dest = resource_path(repo, "opsx", "logs", change_id, "legacy")
    receipt_dest = resource_path(repo, "opsx", "receipts", change_id)
    moved = 0

    for pattern in _LOG_GLOBS:
        for src in legacy.glob(pattern):
            if _move_file_safe(src, log_dest / src.name, dry_run=dry_run, reports=reports):
                moved += 1

    streams = legacy / "scenario-row-streams"
    if streams.is_dir():
        target_streams = log_dest / "scenario-row-streams"
        moved += _merge_tree_safe(streams, target_streams, dry_run=dry_run, reports=reports)

    for name in _RECEIPT_NAMES:
        src = legacy / name
        if _move_file_safe(src, receipt_dest / name, dry_run=dry_run, reports=reports):
            moved += 1

    for dirname in _RECEIPT_DIRS:
        src = legacy / dirname
        if src.is_dir():
            moved += _merge_tree_safe(src, receipt_dest / dirname, dry_run=dry_run, reports=reports)

    for src in legacy.glob(f"*{_FINDINGS_SUFFIX}"):
        if _move_file_safe(src, receipt_dest / src.name, dry_run=dry_run, reports=reports):
            moved += 1

    for src in legacy.glob("scenario-recovery-*"):
        if src.is_dir():
            moved += _merge_tree_safe(src, receipt_dest / src.name, dry_run=dry_run, reports=reports)

    removed_dirs = 0
    if not dry_run and legacy.is_dir():
        try:
            if not any(legacy.iterdir()):
                legacy.rmdir()
                removed_dirs = 1
        except OSError:
            pass

    return {"moved": moved, "removed_dirs": removed_dirs}


def migrate_apply_receipts(
    repo: Path,
    change_id: str,
    *,
    dry_run: bool,
    reports: list[tuple[MoveReport, str, str]],
) -> int:
    """Перенос apply-* и scenario machine receipts в opsx/receipts."""
    src_root = resource_path(repo, "openspec", "changes", change_id)
    dest_root = resource_path(repo, "opsx", "receipts", change_id)
    moved = 0
    for stage in _APPLY_STAGES:
        src = src_root / stage
        if src.is_dir():
            moved += _merge_tree_safe(src, dest_root / stage, dry_run=dry_run, reports=reports)
    scenario_src = src_root / "scenario-coverage"
    scenario_dest = dest_root / "scenario-coverage"
    for name in _SCENARIO_MACHINE:
        src_file = scenario_src / name
        if _move_file_safe(src_file, scenario_dest / name, dry_run=dry_run, reports=reports):
            moved += 1
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--change", action="append", default=[], help="Limit to change id(s)")
    parser.add_argument(
        "--repair-visual-slice-004",
        action="store_true",
        help="Удалить ложный GREEN visual SLICE-004",
    )
    args = parser.parse_args()
    repo = _repo_root()
    reports: list[tuple[MoveReport, str, str]] = []
    if args.repair_visual_slice_004 and not args.dry_run:
        _ = repair_visual_slice_004_false_green(repo_root=repo)
    changes_root = resource_path(repo, "openspec", "changes")
    if not changes_root.is_dir():
        print("no openspec/changes")
        return 0
    total_moved = 0
    for child in sorted(changes_root.iterdir()):
        if not child.is_dir() or child.name == "archive":
            continue
        change_id = child.name
        if args.change and change_id not in args.change:
            continue
        stats = migrate_verify_dir(repo, change_id, dry_run=args.dry_run, reports=reports)
        total_moved += stats["moved"]
        total_moved += migrate_apply_receipts(
            repo, change_id, dry_run=args.dry_run, reports=reports,
        )
        print(f"{change_id}: verify_moved={stats['moved']} empty_verify_removed={stats['removed_dirs']}")
    for kind, src, dest in reports:
        print(f"  [{kind}] {src} -> {dest}")
    print(f"total_reports={len(reports)} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
