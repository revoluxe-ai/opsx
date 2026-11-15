"""Bugbot convergence parent for ``/opsx-orch-bugbot-converge``.

Uses the proven Temporal/Cursor SDK bugbot pipeline as the only async
transport. Does not invent Task/subagent BugBot and does not nest a
second Temporal start from inside ``/opsx-orch-bug-fix``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, cast


CONTRACT_ID = "OPSX-WB-ORCH-BUGBOT-CONVERGE-01"
TEMPORAL_RUNTIME = (
    "python tools/temporal/runtime.py run bugbot --repo <workspace> "
    "[--base <against>] --review <review> --repair <repair>"
)
CLEAN_STATUS = "BUGBOT_CLEAN"
DEFAULT_MAX_CYCLES = 5


class OrchConvergeError(Exception):
    """Convergence refusal."""


def run(repo_root: Path, request: dict[str, object]) -> dict[str, object]:
    try:
        return _seal(_run(repo_root.resolve(), request))
    except OrchConvergeError as error:
        return _seal(_blocked(str(error)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--repo-root", type=Path, required=True)
    _ = parser.add_argument("--request-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw = json.loads(args.request_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps(_seal(_blocked(f"invalid_request:{error}"))))
        return 2
    if not isinstance(raw, dict):
        print(json.dumps(_seal(_blocked("invalid_request"))))
        return 2
    result = run(args.repo_root, cast(dict[str, object], raw))
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def _run(repo_root: Path, request: dict[str, object]) -> dict[str, object]:
    blocked = _projectization_block(repo_root)
    if blocked is not None:
        return blocked
    against = _opt(request, "against")
    if not against:
        return _blocked("against_required")
    max_cycles = _int(request.get("max_cycles"), DEFAULT_MAX_CYCLES)
    if max_cycles < 1:
        return _blocked("max_cycles_invalid")
    if _opt(request, "check_transport") in {"TASK_BACKGROUND", "subagent"}:
        return _blocked("BUGBOT_SUBAGENTS_FORBIDDEN")
    mission_id = _opt(request, "mission") or f"ORCH-bugbot-converge-{against}"
    head = _opt(request, "head")
    execution_id = _opt(request, "execution_id")
    stored = _load(repo_root, mission_id)
    if stored is not None:
        if stored.get("against") != against:
            return _blocked("target_not_frozen")
        if head and stored.get("head") and stored["head"] != head:
            return _blocked("stale_mission")
        if execution_id and stored.get("execution_id") and stored["execution_id"] != execution_id:
            return _blocked("stale_execution")
    invocation = {
        "runtime": TEMPORAL_RUNTIME.replace("<against>", against),
        "pipeline_id": "bugbot",
        "clean_status": CLEAN_STATUS,
        "transport": "temporal-cursor-sdk-runtime-v1",
        "cursor_sdk": "1.0.31",
        "against": against,
        "max_cycles": max_cycles,
        "nested_opsx_orch_bug_fix_starts_temporal": False,
    }
    dry_run = request.get("dry_run") is True or _opt(request, "dry_run") == "true"
    workflow = request.get("workflow_receipt")
    if dry_run and not isinstance(workflow, dict):
        envelope = {
            "STATUS": "INVOCATION_READY",
            "MISSION_STATUS": "ACTIVE",
            "BUGBOT_CONVERGED": "NO",
            "MISSION_ID": mission_id,
            "INVOCATION": invocation,
            "OWNER_GO_AFTER_INVOCATION_REQUIRED": "NO",
            "CHECK_TRANSPORT": "TEMPORAL_CURSOR_SDK",
            "CHECK_COMMAND": "/review-bugbot",
            "REPAIR_PRODUCT": "/opsx-orch-bug-fix",
            "REPAIR_PROCESS": "/opsx-meta-fix-command-defects",
        }
        _store(repo_root, mission_id, envelope, against=against, head=head, execution_id=execution_id)
        return envelope
    if not isinstance(workflow, dict):
        return _blocked("workflow_receipt_required")
    if str(workflow.get("against") or against) != against:
        return _blocked("workflow_target_mismatch")
    if head and str(workflow.get("head") or "") and str(workflow.get("head")) != head:
        return _blocked("stale_workflow_evidence")
    if execution_id and str(workflow.get("execution_id") or "") != execution_id:
        return _blocked("stale_workflow_evidence")
    check_count = _int(workflow.get("check_count"), 0)
    status = str(workflow.get("status") or "")
    if status == CLEAN_STATUS:
        if check_count < 1:
            return _blocked("missing_native_check")
        envelope = {
            "STATUS": "BUGBOT_CONVERGED",
            "MISSION_STATUS": "BUGBOT_CONVERGED",
            "BUGBOT_CONVERGED": "YES",
            "MISSION_ID": mission_id,
            "INVOCATION": invocation,
            "WORKFLOW_ID": workflow.get("workflow_id"),
            "CHECK_COUNT": check_count,
            "CHECK_TRANSPORT": "TEMPORAL_CURSOR_SDK",
        }
        _store(repo_root, mission_id, envelope, against=against, head=head, execution_id=execution_id)
        return envelope
    if check_count > max_cycles:
        return {
            "STATUS": "ITERATION_LIMIT_REACHED",
            "MISSION_STATUS": "ITERATION_LIMIT_REACHED",
            "BUGBOT_CONVERGED": "NO",
            "MISSION_ID": mission_id,
            "CHECK_COUNT": check_count,
            "MAX_CYCLES": max_cycles,
        }
    if status.startswith("BLOCKED") or status in {"FAIL", "FAILED"}:
        return {
            "STATUS": status,
            "MISSION_STATUS": status,
            "BUGBOT_CONVERGED": "NO",
            "MISSION_ID": mission_id,
        }
    return _blocked("convergence_predicate_unmet")


def _int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return default


def _path(repo_root: Path, mission_id: str) -> Path:
    return (
        repo_root
        / "opsx"
        / "runs"
        / "orch-missions"
        / mission_id
        / "convergence.json"
    )


def _load(repo_root: Path, mission_id: str) -> dict[str, object] | None:
    path = _path(repo_root, mission_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _store(
    repo_root: Path,
    mission_id: str,
    envelope: dict[str, object],
    *,
    against: str,
    head: str,
    execution_id: str,
) -> None:
    path = _path(repo_root, mission_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(envelope)
    payload["against"] = against
    payload["head"] = head
    payload["execution_id"] = execution_id
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _projectization_block(repo_root: Path) -> dict[str, object] | None:
    from opsx_runtime_gates import projectization_and_adoption_block

    return projectization_and_adoption_block(
        repo_root,
        "python.pipeline.orch_bugbot_converge",
        blocked_factory=_blocked,
    )


def _opt(request: dict[str, object], key: str) -> str:
    value = request.get(key)
    return value.strip() if isinstance(value, str) else ""


def _blocked(reason: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "STATUS": "FAIL_CLOSED",
        "MISSION_STATUS": "FAIL_CLOSED",
        "BUGBOT_CONVERGED": "NO",
        "REASON": reason,
        "CHECK_TRANSPORT": "TEMPORAL_CURSOR_SDK",
    }
    payload.update(extra)
    return payload


def _seal(result: dict[str, object]) -> dict[str, object]:
    sealed = dict(result)
    sealed.setdefault("CONTRACT_ID", CONTRACT_ID)
    sealed.setdefault("SOURCE_PARITY", "ADAPTED")
    sealed.setdefault("GREEN_WRITER", "NO")
    sealed.setdefault("COMMIT_ALLOWED", "NO")
    sealed.setdefault("SUPERVISOR_REPLACEMENT", "NO")
    sealed.setdefault("OWNER_GO_AFTER_INVOCATION_REQUIRED", "NO")
    return sealed


if __name__ == "__main__":
    raise SystemExit(main())
