# -*- coding: utf-8 -*-
"""Harness-only: simulate external Bug Team worker writing authoritative harvest artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HOST_DISPATCH_MODULE = "runtime.bug_team_host.dispatch_opsx_bug_team"
HOST_PROVENANCE = "runtime.bug_team_host.dispatch_opsx_bug_team"


def publish_controlled_worker_evidence(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    case_id: str,
    finding_id: str,
    plan_id: str,
    work_item_id: str,
) -> None:
    """Write filesystem evidence as if produced outside orch runtime (acceptance / integration)."""
    case_root = repo_root / "opsx" / "runs" / "bug-cases" / case_id
    results = case_root / "results"
    results.mkdir(parents=True, exist_ok=True)
    green = results / f"GREEN-{finding_id}.md"
    green.write_text(
        "\n".join(
            [
                f"TEST_ID: RED-{finding_id}",
                f"BUG_CASE_ID: {case_id}",
                f"FINDING_ID: {finding_id}",
                "COMMAND: /opsx-test verify mode",
                "PRODUCTION_HASH_AFTER: acceptance-worker",
                "TEST_CONTENT_SHA256: acceptance-worker-sha",
                "STATUS: GREEN_CONFIRMED",
                "TARGETED_REGRESSIONS: none",
                f"PROVENANCE: {HOST_PROVENANCE}",
                f"WORKER_EXECUTION_ID: {execution_id}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    packet = results / f"BUG-TEAM-{finding_id}.json"
    packet.write_text(
        json.dumps(
            {
                "schema_version": "opsx-bug-team-packet-v1",
                "CLOSURE_READINESS": "READY_FOR_CLOSURE",
                "BLOCKING_FINDINGS": "NONE",
                "CASE_STATUS": "CLOSING",
                "mission_id": mission_id,
                "case_id": case_id,
                "finding_id": finding_id,
                "plan_id": plan_id,
                "work_item_id": work_item_id,
                "execution_id": execution_id,
                "head": head,
                "PROVENANCE": HOST_PROVENANCE,
                "EXECUTION_TERMINAL": "CONFIRMED",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_terminal(repo_root, mission_id=mission_id, execution_id=execution_id, head=head)


def _write_terminal(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
) -> None:
    marker = (
        repo_root
        / "opsx"
        / "runs"
        / "orch-missions"
        / mission_id
        / f"execution-terminal-{execution_id}.json"
    )
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "execution_terminal": "CONFIRMED",
        "mission_id": mission_id,
        "execution_id": execution_id,
        "head": head,
        "source": HOST_PROVENANCE,
        "host_dispatch_module": HOST_DISPATCH_MODULE,
    }
    marker.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
