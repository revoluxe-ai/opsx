"""Native `/opsx-materialize-bug-queue` writer (authorized case-ledger mutations)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .green_authority import validate_green_file
from .receipts import utc_now, write_receipt

BUG_CASES = Path("opsx/runs/bug-cases")
MATERIALIZE_DIR = BUG_CASES / "materialize"
WRITER = "/opsx-materialize-bug-queue"


class MaterializeError(Exception):
    def __init__(self, reason: str, **extra: str) -> None:
        self.reason = reason
        self.extra = extra
        super().__init__(reason)


@dataclass(frozen=True)
class MaterializeContext:
    repo_root: Path
    mission_id: str
    execution_id: str
    head: str
    change_id: str
    production: bool = True


def _utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text.strip())[:24].strip("-")
    return cleaned or "MANUAL"


def mode_intake(
    ctx: MaterializeContext,
    *,
    finding_id: str,
    location: str,
    raw_report: str,
    case_id: str | None = None,
) -> dict[str, Any]:
    case = case_id or f"BUGCASE-{_slug(finding_id)}-{_utc_slug()}"
    case_root = ctx.repo_root / BUG_CASES / case
    intake_id = "INTAKE-001"
    for path in (case_root / "intakes", case_root / "findings", case_root / "results", case_root / "handoffs"):
        path.mkdir(parents=True, exist_ok=True)

    (case_root / "intakes" / f"{intake_id}.md").write_text(
        "\n".join(
            [
                f"INTAKE_ID: {intake_id}",
                "SOURCE_TYPE: orch_mission",
                f"OBSERVED_SYMPTOM: {raw_report[:200]}",
                f"FINDING_ID: {finding_id}",
                f"LOCATION: {location}",
                "STATUS: intake_recorded",
                "",
                "RAW_REPORT:",
                raw_report,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (case_root / "findings" / f"{finding_id}.md").write_text(
        "\n".join(
            [
                f"FINDING_ID: {finding_id}",
                f"CASE_ID: {case}",
                f"CHANGE: {ctx.change_id}",
                "STATUS: intake_recorded",
                f"LOCATION: {location}",
                f"MISSION_ID: {ctx.mission_id}",
                f"EXECUTION_ID: {ctx.execution_id}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (case_root / "CASE.md").write_text(
        "\n".join(
            [
                f"CASE_ID: {case}",
                f"PRIMARY_INTAKE_ID: {intake_id}",
                f"ROOT_FINDING_ID: {finding_id}",
                "STATUS: intake_recorded",
                "CURRENT_STAGE: intake_recorded",
                f"LAST_COMMAND: {WRITER} mode=intake",
                f"MISSION_ID: {ctx.mission_id}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (case_root / "handoffs" / "COMMON_HANDOFF.md").write_text(
        f"CASE_ID: {case}\nNEXT: family-plan\n",
        encoding="utf-8",
    )
    receipt = write_receipt(
        ctx.repo_root,
        mission_id=ctx.mission_id,
        stage="S0_MATERIALIZE_INTAKE",
        execution_id=ctx.execution_id,
        head=ctx.head,
        writer=WRITER,
        body={
            "mode": "intake",
            "case_id": case,
            "finding_id": finding_id,
            "intake_id": intake_id,
        },
    )
    return {
        "STATUS": "MATERIALIZED",
        "MODE": "intake",
        "CASE_ID": case,
        "FINDING_ID": finding_id,
        "RECEIPT": receipt,
    }


def mode_update_disposition(
    ctx: MaterializeContext,
    *,
    case_id: str,
    finding_id: str,
    disposition: str = "MUST_FIX_CURRENT_CHANGE",
    plan_id: str,
    work_item_id: str,
) -> dict[str, Any]:
    finding_path = ctx.repo_root / BUG_CASES / case_id / "findings" / f"{finding_id}.md"
    if not finding_path.is_file():
        raise MaterializeError("unknown_finding", case_id=case_id, finding_id=finding_id)
    text = finding_path.read_text(encoding="utf-8")
    if f"EXECUTION_ID: {ctx.execution_id}" not in text:
        raise MaterializeError("stale_execution_identity")
    patch = "\n".join(
        [
            "DISPOSITION_RECORDED: YES",
            f"DISPOSITION: {disposition}",
            f"PLAN_ID: {plan_id}",
            f"WORK_ITEM_ID: {work_item_id}",
            "CURRENT_STAGE: disposition",
            f"LAST_COMMAND: {WRITER} mode=update disposition",
        ]
    )
    finding_path.write_text(text.rstrip() + "\n" + patch + "\n", encoding="utf-8")
    case_md = ctx.repo_root / BUG_CASES / case_id / "CASE.md"
    if case_md.is_file():
        case_md.write_text(
            case_md.read_text(encoding="utf-8").rstrip()
            + f"\nCURRENT_STAGE: disposition\nLAST_COMMAND: {WRITER} mode=update\n",
            encoding="utf-8",
        )
    materialize_receipt_path = ctx.repo_root / MATERIALIZE_DIR / f"disposition-{ctx.mission_id}-{finding_id}.json"
    materialize_receipt_path.parent.mkdir(parents=True, exist_ok=True)
    mat_payload = {
        "schema_version": "opsx-materialize-receipt-v1",
        "terminal_status": "disposition_recorded",
        "case_id": case_id,
        "finding_id": finding_id,
        "disposition": disposition,
        "mission_id": ctx.mission_id,
        "execution_id": ctx.execution_id,
        "head": ctx.head,
        "written_at": utc_now(),
    }
    materialize_receipt_path.write_text(json.dumps(mat_payload, indent=2) + "\n", encoding="utf-8")
    receipt = write_receipt(
        ctx.repo_root,
        mission_id=ctx.mission_id,
        stage="S2b_MATERIALIZE_DISPOSITION",
        execution_id=ctx.execution_id,
        head=ctx.head,
        writer=WRITER,
        body={
            "mode": "update",
            "case_id": case_id,
            "finding_id": finding_id,
            "disposition": disposition,
            "materialize_receipt": str(
                materialize_receipt_path.resolve().relative_to(ctx.repo_root.resolve())
            ).replace("\\", "/"),
        },
    )
    return {
        "STATUS": "MATERIALIZED",
        "MODE": "update",
        "CASE_ID": case_id,
        "FINDING_ID": finding_id,
        "DISPOSITION": disposition,
        "RECEIPT": receipt,
    }


def mode_update_close_fixed(
    ctx: MaterializeContext,
    *,
    case_id: str,
    finding_id: str,
) -> dict[str, Any]:
    case_root = ctx.repo_root / BUG_CASES / case_id
    green = case_root / "results" / f"GREEN-{finding_id}.md"
    if not green.is_file():
        raise MaterializeError("missing_green_evidence")
    ok, reason = validate_green_file(green, production=ctx.production)
    if not ok:
        raise MaterializeError(reason)
    finding_path = case_root / "findings" / f"{finding_id}.md"
    if not finding_path.is_file():
        raise MaterializeError("unknown_finding")
    text = finding_path.read_text(encoding="utf-8")
    if f"EXECUTION_ID: {ctx.execution_id}" not in text:
        raise MaterializeError("stale_execution_identity")
    if "DISPOSITION_RECORDED: YES" not in text:
        raise MaterializeError("disposition_not_recorded")
    completed = utc_now()
    finding_path.write_text(
        text.rstrip()
        + "\n".join(
            [
                "",
                "STATUS: closed_fixed",
                "EPISODE_TERMINAL: FIXED",
                f"COMPLETED_AT: {completed}",
                f"CLOSED_BY: {WRITER} mode=update close=fixed",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    mat_path = ctx.repo_root / MATERIALIZE_DIR / f"close-{ctx.mission_id}-{finding_id}.json"
    mat_path.parent.mkdir(parents=True, exist_ok=True)
    mat_payload = {
        "schema_version": "opsx-materialize-receipt-v1",
        "terminal_status": "closed_fixed",
        "case_id": case_id,
        "finding_id": finding_id,
        "mission_id": ctx.mission_id,
        "execution_id": ctx.execution_id,
        "head": ctx.head,
        "green_evidence": str(green.resolve().relative_to(ctx.repo_root.resolve())).replace(
            "\\", "/"
        ),
        "written_at": completed,
    }
    tmp = mat_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(mat_payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, mat_path)
    receipt = write_receipt(
        ctx.repo_root,
        mission_id=ctx.mission_id,
        stage="S5b_MATERIALIZE_CLOSE_FIXED",
        execution_id=ctx.execution_id,
        head=ctx.head,
        writer=WRITER,
        body={
            "mode": "update",
            "close": "fixed",
            "case_id": case_id,
            "finding_id": finding_id,
            "materialize_receipt": str(
                mat_path.resolve().relative_to(ctx.repo_root.resolve())
            ).replace("\\", "/"),
        },
    )
    return {
        "STATUS": "MATERIALIZED",
        "MODE": "update",
        "CLOSE": "fixed",
        "CASE_ID": case_id,
        "FINDING_ID": finding_id,
        "RECEIPT": receipt,
    }


def _mode_update_terminal(
    ctx: MaterializeContext,
    *,
    case_id: str,
    finding_id: str,
    terminal_status: str,
    episode_terminal: str,
) -> dict[str, Any]:
    finding_path = ctx.repo_root / BUG_CASES / case_id / "findings" / f"{finding_id}.md"
    if not finding_path.is_file():
        raise MaterializeError("unknown_finding")
    text = finding_path.read_text(encoding="utf-8")
    if f"EXECUTION_ID: {ctx.execution_id}" not in text:
        raise MaterializeError("stale_execution_identity")
    if "DISPOSITION_RECORDED: YES" not in text:
        raise MaterializeError("disposition_not_recorded")
    completed = utc_now()
    finding_path.write_text(
        text.rstrip()
        + "\n".join(
            [
                "",
                f"STATUS: {terminal_status}",
                f"EPISODE_TERMINAL: {episode_terminal}",
                f"COMPLETED_AT: {completed}",
                f"CLOSED_BY: {WRITER} mode=update terminal={terminal_status}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    mat_path = ctx.repo_root / MATERIALIZE_DIR / f"{terminal_status}-{ctx.mission_id}-{finding_id}.json"
    mat_path.parent.mkdir(parents=True, exist_ok=True)
    mat_payload = {
        "schema_version": "opsx-materialize-receipt-v1",
        "terminal_status": terminal_status,
        "case_id": case_id,
        "finding_id": finding_id,
        "mission_id": ctx.mission_id,
        "execution_id": ctx.execution_id,
        "head": ctx.head,
        "written_at": completed,
    }
    tmp = mat_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(mat_payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, mat_path)
    receipt = write_receipt(
        ctx.repo_root,
        mission_id=ctx.mission_id,
        stage="S6_NON_REPAIR_MATERIALIZE",
        execution_id=ctx.execution_id,
        head=ctx.head,
        writer=WRITER,
        body={
            "mode": "update",
            "terminal_status": terminal_status,
            "case_id": case_id,
            "finding_id": finding_id,
        },
    )
    return {
        "STATUS": "MATERIALIZED",
        "MODE": "update",
        "TERMINAL": terminal_status,
        "CASE_ID": case_id,
        "FINDING_ID": finding_id,
        "RECEIPT": receipt,
    }


def mode_update_defer(
    ctx: MaterializeContext,
    *,
    case_id: str,
    finding_id: str,
) -> dict[str, Any]:
    return _mode_update_terminal(
        ctx,
        case_id=case_id,
        finding_id=finding_id,
        terminal_status="deferred",
        episode_terminal="DEFER",
    )


def mode_update_false_positive(
    ctx: MaterializeContext,
    *,
    case_id: str,
    finding_id: str,
) -> dict[str, Any]:
    return _mode_update_terminal(
        ctx,
        case_id=case_id,
        finding_id=finding_id,
        terminal_status="false_positive",
        episode_terminal="FALSE_POSITIVE",
    )


def mode_update_security_route(
    ctx: MaterializeContext,
    *,
    case_id: str,
    finding_id: str,
) -> dict[str, Any]:
    return _mode_update_terminal(
        ctx,
        case_id=case_id,
        finding_id=finding_id,
        terminal_status="security_process_route",
        episode_terminal="SECURITY_PROCESS",
    )


def mode_update_global_release_blocker(
    ctx: MaterializeContext,
    *,
    case_id: str,
    finding_id: str,
) -> dict[str, Any]:
    """S6 urgent terminal для GLOBAL_RELEASE_BLOCKER (batch clear остаётся NO)."""

    return _mode_update_terminal(
        ctx,
        case_id=case_id,
        finding_id=finding_id,
        terminal_status="global_release_blocker",
        episode_terminal="GLOBAL_RELEASE_BLOCKER",
    )
