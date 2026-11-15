"""CLI: миграция scenario evidence на v3 digest / canonical tests без повторного audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agents.common.scenario_evidence_migrate import migrate_all_changes, migrate_change_evidence
from agents.common.scenario_migrate import iter_coverage_dirs


def _repo() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate scenario coverage evidence after canonical layout")
    parser.add_argument("--change", action="append", help="Limit to change id (repeatable)")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist migrated receipts/journal (default: dry-run)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write JSON summary (default: stdout only)",
    )
    args = parser.parse_args(argv)
    repo = _repo()
    dry_run = not args.write
    reports = []
    if args.change:
        for change_id in args.change:
            reports.append(migrate_change_evidence(repo, change_id, dry_run=dry_run))
    else:
        if dry_run:
            for coverage in iter_coverage_dirs(repo):
                change_id = coverage.parent.name
                reports.append(migrate_change_evidence(repo, change_id, dry_run=True))
        else:
            reports = migrate_all_changes(repo, dry_run=False)
    payload = [
        {
            "change_id": r.change_id,
            "migrated_supported": r.migrated_supported,
            "migrated_gap": r.migrated_gap,
            "skipped_no_prior": r.skipped_no_prior,
            "repair_proof_remap": r.repair_proof_remap,
            "repair_layout": r.repair_layout,
            "matrix_rebound": r.matrix_rebound,
            "ledger_refreshed": r.ledger_refreshed,
            "layout_problems": r.layout_problems,
            "repair_count": len(r.repair),
            "repair_sample": r.repair[:5],
        }
        for r in reports
    ]
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    if any(r.repair_layout for r in reports):
        return 2
    if any(r.repair for r in reports):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
