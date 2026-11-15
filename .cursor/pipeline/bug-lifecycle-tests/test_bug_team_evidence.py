"""Harvest evidence binding rejects text-only / unbound markers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_PIPELINE = REPO_ROOT / ".cursor/pipeline"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from bug_lifecycle.bug_team_evidence import validate_harvest_bundle


def test_text_only_green_without_execution_binding_fails(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    case_id = "CASE-1"
    finding_id = "F1"
    results = root / "opsx/runs/bug-cases" / case_id / "results"
    results.mkdir(parents=True)
    (results / f"GREEN-{finding_id}.md").write_text(
        "\n".join(
            [
                "TEST_ID: T1",
                f"BUG_CASE_ID: {case_id}",
                f"FINDING_ID: {finding_id}",
                "TEST_CONTENT_SHA256: sha",
                "STATUS: GREEN_CONFIRMED",
                "PROVENANCE: HOST_BUG_TEAM",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (results / f"BUG-TEAM-{finding_id}.json").write_text(
        json.dumps(
            {
                "CLOSURE_READINESS": "READY_FOR_CLOSURE",
                "mission_id": "M",
                "case_id": case_id,
                "finding_id": finding_id,
                "execution_id": "exec-1",
                "head": "h1",
                "work_item_id": "W1",
                "PROVENANCE": "HOST_BUG_TEAM",
                "EXECUTION_TERMINAL": "CONFIRMED",
            }
        ),
        encoding="utf-8",
    )
    ok, reason = validate_harvest_bundle(
        root,
        mission_id="M",
        execution_id="exec-1",
        head="h1",
        case_id=case_id,
        finding_id=finding_id,
        work_item_id="W1",
        production=True,
    )
    assert ok is False
    assert "execution" in reason
