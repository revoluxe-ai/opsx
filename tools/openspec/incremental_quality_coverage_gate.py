#!/usr/bin/env python3
"""CLI gate для OPSX-INCREMENTAL-QUALITY-COVERAGE-V1."""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = project_root()
_IQC_DIR = resource_path(_REPO_ROOT, ".cursor/pipeline/incremental-quality-coverage")
if str(_IQC_DIR) not in sys.path:
    sys.path.insert(0, str(_IQC_DIR))

from iqc_quality_audit import audit_incremental_quality_coverage  # noqa: E402
from iqc_states import ManifestActivation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Incremental quality coverage gate (IQC V1)")
    parser.add_argument(
        "--mode",
        choices=("audit", "enforce"),
        default="audit",
        help="audit: always; enforce: FAIL on gaps only when manifest ACTIVE",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    args = parser.parse_args()

    report = audit_incremental_quality_coverage(_REPO_ROOT)
    activation = str(report.get("manifest_activation", ""))
    status = str(report.get("STATUS", "BLOCKED"))

    if args.mode == "enforce" and activation == ManifestActivation.ACTIVE.value:
        if status not in ("PASS",):
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(f"IQC_ENFORCE_FAIL status={status}")
            return 1
    elif args.mode == "enforce" and activation != ManifestActivation.ACTIVE.value:
        report = {**report, "ENFORCE_SKIPPED": "PROPOSED_MANIFEST"}

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"STATUS={report.get('STATUS')} activation={activation}")
    if status == "BLOCKED":
        return 3
    if status == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
