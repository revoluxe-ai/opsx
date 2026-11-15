"""In-parent bug-fix orchestration for ``/opsx-orch-bug-fix``.

Temporal/Cursor SDK remains the outer async transport. This helper does
not start a second workflow and does not invent Task/subagent transport.
AUTO_PERSIST stages invoke native bug-lifecycle primitives (materialize is the
authorized case-ledger writer). This module does not start nested Temporal.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import cast


CONTRACT_ID = "OPSX-WB-ORCH-BUG-FIX-01"
PROCESS_PREFIXES = (
    ".cursor/",
    "opsx/",
    ".agents/",
    "tools/temporal/",
)
PROCESS_ROUTE = "/opsx-meta-fix-command-defects"
PRODUCT_ROUTE = "/opsx-bug-team"
FAMILY_PLAN_ROUTE = "/opsx-bug-family-plan"
DISPOSITION_ROUTE = "/opsx-bug-disposition"
MATERIALIZE_ROUTE = "/opsx-materialize-bug-queue"
BUG_CASES_ROOT = Path("opsx/runs/bug-cases")
_FINDING_CLOSED_RE = re.compile(r"^STATUS:\s*closed_fixed\s*$", re.I | re.M)
_DISPOSITION_MARK_RE = re.compile(
    r"(DISPOSITION(?:_COMPLETE|_RECORDED)|CURRENT_STAGE:\s*disposition|"
    r"LAST_COMMAND=.*/opsx-bug-disposition)",
    re.I,
)
_DELEGATED_PERSIST_COMMANDS = {
    "/opsx-bug-family-plan GO": (
        FAMILY_PLAN_ROUTE,
        "in-turn plan output only; no family-plans/** file",
    ),
    "/opsx-bug-disposition GO": (
        DISPOSITION_ROUTE,
        "case ledger under opsx/runs/bug-cases; not dispositions/**",
    ),
    "/opsx-materialize-bug-queue GO": (
        MATERIALIZE_ROUTE,
        "durable closed_fixed / defer / FP row in bug-cases ledger",
    ),
}


class OrchBugFixError(Exception):
    """Admission/routing refusal."""


def run(repo_root: Path, request: dict[str, object]) -> dict[str, object]:
    try:
        return _seal(_run(repo_root.resolve(), request))
    except OrchBugFixError as error:
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
    if _opt(request, "transport") in {"TASK_BACKGROUND", "subagent", "legacy_cli"}:
        return _blocked("SECOND_ASYNC_TRANSPORT_FORBIDDEN")
    temporal_hop = _opt(request, "temporal_repair_hop") == "YES" or _opt(request, "require_native_mission") == "YES"
    if temporal_hop and _opt(request, "native_mission") != "YES":
        return _blocked("LEGACY_ORCH_ADAPTER_FORBIDDEN_USE_NATIVE_MISSION")
    if _opt(request, "native_mission") == "YES":
        from bug_lifecycle.orch_mission_runtime import run_to_batch_clear

        request_native = dict(request)
        request_native.setdefault("mission", _opt(request, "mission") or _mint_mission(request))
        native = run_to_batch_clear(repo_root, request_native)
        return _seal(native)
    mission_resume = _opt(request, "resume_mode") == "RUNTIME_STATE" and _opt(request, "mission")
    if mission_resume:
        state_path = (
            repo_root
            / "opsx"
            / "runs"
            / "orch-missions"
            / str(request.get("mission"))
            / "runtime_state.json"
        )
        if state_path.is_file():
            from bug_lifecycle.orch_mission_runtime import resume_after_interrupt

            native = resume_after_interrupt(repo_root, dict(request))
            return _seal(native)
    findings = _findings(request)
    change_id = _opt(request, "change")
    mission_id = _opt(request, "mission") or _mint_mission(request)
    execution_id = _opt(request, "execution_id")
    head = _opt(request, "head")
    stored = _load_mission(repo_root, mission_id)
    if stored is not None:
        if head and stored.get("head") and stored["head"] != head:
            return _blocked("stale_mission")
        if execution_id and stored.get("execution_id") and stored["execution_id"] != execution_id:
            return _blocked("stale_execution")
    product: list[dict[str, str]] = []
    process: list[dict[str, str]] = []
    for row in findings:
        location = row.get("location", "")
        if _is_process(location):
            process.append(row)
        else:
            product.append(row)
    if not product and not process:
        envelope = {
            "STATUS": "BUG_BATCH_CLEAR",
            "MISSION_STATUS": "BUG_BATCH_CLEAR",
            "BUG_BATCH_CLEAR": "YES",
            "REPAIRED_THIS_BATCH": "NO",
            "FIX_NOW": "(none)",
            "RUNNING_WORKER": "none",
            "MISSION_ID": mission_id,
            "CHANGE": change_id or "unresolved",
            "TRANSPORT": "IN_PARENT_SYNC",
            "TEMPORAL_NESTED_START": "NO",
        }
        _store_mission(repo_root, mission_id, envelope, head=head, execution_id=execution_id)
        return envelope
    if process and not product:
        return {
            "STATUS": "PARTIAL_COMPLETE",
            "MISSION_STATUS": "PARTIAL_COMPLETE",
            "BUG_BATCH_CLEAR": "NO",
            "REPAIRED_THIS_BATCH": "NO",
            "PROCESS_HANDOFF": "YES",
            "NEXT_COMMAND": PROCESS_ROUTE,
            "NEXT_REAL_ACTION": PROCESS_ROUTE,
            "PROCESS_FINDINGS": process,
            "MISSION_ID": mission_id,
            "TRANSPORT": "IN_PARENT_SYNC",
            "TEMPORAL_NESTED_START": "NO",
        }
    parallel_process = bool(process and product)
    persist = _opt(request, "auto_persist_command")
    if persist:
        from bug_lifecycle.orch_mission_runtime import execute_auto_persist

        request_with_ids = dict(request)
        request_with_ids.setdefault("mission", mission_id)
        request_with_ids.setdefault("change", change_id)
        native = execute_auto_persist(repo_root, request_with_ids)
        native.setdefault("MISSION_ID", mission_id)
        native.setdefault("PERSIST_MODE", "NATIVE")
        native.setdefault("OWNER_WAKE", "NO")
        return _seal(native)
    if product and _native_batch_clear(repo_root, mission_id, product, execution_id, head):
        envelope = {
            "STATUS": "BUG_BATCH_CLEAR",
            "MISSION_STATUS": "BUG_BATCH_CLEAR",
            "BUG_BATCH_CLEAR": "YES",
            "REPAIRED_THIS_BATCH": "YES" if _opt(request, "repaired") == "YES" else "NO",
            "FIX_NOW": "(none)",
            "RUNNING_WORKER": "none",
            "MISSION_ID": mission_id,
            "CHANGE": change_id or "unresolved",
            "TRANSPORT": "IN_PARENT_SYNC",
            "TEMPORAL_NESTED_START": "NO",
            "PRODUCT_ROUTE": PRODUCT_ROUTE,
        }
        _store_mission(repo_root, mission_id, envelope, head=head, execution_id=execution_id)
        return envelope
    if not product:
        return {
            "STATUS": "NO_INPUT",
            "MISSION_STATUS": "NO_INPUT",
            "BUG_BATCH_CLEAR": "NO",
            "REPAIRED_THIS_BATCH": "NO",
            "MISSION_ID": mission_id,
            "TRANSPORT": "IN_PARENT_SYNC",
        }
    if not change_id:
        return _blocked("BLOCKED_CHANGE_CONTEXT")
    envelope: dict[str, object] = {
        "STATUS": "REPAIR_DISPATCH",
        "MISSION_STATUS": "ACTIVE",
        "BUG_BATCH_CLEAR": "NO",
        "REPAIRED_THIS_BATCH": "NO",
        "NEXT_COMMAND": PRODUCT_ROUTE,
        "PRODUCT_FINDINGS": product,
        "CHANGE": change_id,
        "MISSION_ID": mission_id,
        "TRANSPORT": "IN_PARENT_SYNC",
        "TEMPORAL_NESTED_START": "NO",
        "DURABLE_WRITER": MATERIALIZE_ROUTE,
        "BATCH_CLEAR_AUTHORITY": "LEDGER_ONLY",
    }
    if parallel_process:
        envelope["PROCESS_HANDOFF"] = "YES"
        envelope["PARALLEL_PROCESS_ROUTE"] = PROCESS_ROUTE
        envelope["PROCESS_FINDINGS"] = process
        envelope["MIXED_BATCH_POLICY"] = "PRODUCT_PP_CONTINUES"
    _store_mission(repo_root, mission_id, envelope, head=head, execution_id=execution_id)
    return envelope


def _findings(request: dict[str, object]) -> list[dict[str, str]]:
    raw = request.get("findings")
    if isinstance(raw, list):
        rows: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            location = str(item.get("location") or "")
            finding = str(item.get("finding") or item.get("id") or "")
            if location or finding:
                rows.append({"location": location, "finding": finding})
        return rows
    block = _opt(request, "findings_text")
    rows = []
    for match in re.finditer(r"^\|\s*[^|]+?\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", block, re.M):
        rows.append({"location": match.group(1).strip(), "finding": match.group(2).strip()})
    return rows


def _is_process(location: str) -> bool:
    text = location.replace("\\", "/").lstrip("`")
    return any(text.startswith(prefix) for prefix in PROCESS_PREFIXES)


def _delegated_persist_envelope(
    persist: str,
    *,
    mission_id: str,
    change_id: str,
) -> dict[str, object] | None:
    """Return READ_ONLY persist delegation for family-plan / disposition GO."""

    route_ssca = _DELEGATED_PERSIST_COMMANDS.get(persist)
    if route_ssca is None:
        if not persist.endswith(" GO"):
            return None
        if "family-plan" in persist:
            route_ssca = (
                FAMILY_PLAN_ROUTE,
                "in-turn plan output only; no family-plans/** file",
            )
        elif "disposition" in persist:
            route_ssca = (
                DISPOSITION_ROUTE,
                "case ledger under opsx/runs/bug-cases; not dispositions/**",
            )
        elif "materialize-bug-queue" in persist:
            route_ssca = (
                MATERIALIZE_ROUTE,
                "durable closed_fixed / defer / FP row in bug-cases ledger",
            )
        else:
            return None
    next_command, ssca = route_ssca
    envelope: dict[str, object] = {
        "STATUS": "DELEGATED_PERSIST",
        "MISSION_STATUS": "ACTIVE",
        "BUG_BATCH_CLEAR": "NO",
        "REPAIRED_THIS_BATCH": "NO",
        "NEXT_COMMAND": next_command,
        "PERSIST_MODE": "READ_ONLY",
        "GO_TOKEN": "FORBIDDEN",
        "OWNER_WAKE": "NO",
        "TEMPORAL_NESTED_START": "NO",
        "TRANSPORT": "IN_PARENT_SYNC",
        "WRAPPER_PRODUCT_WRITE": "NO",
        "SSCA": ssca,
        "MISSION_ID": mission_id,
    }
    if change_id:
        envelope["CHANGE"] = change_id
    return envelope


def _finding_paths(repo_root: Path, finding_id: str) -> list[Path]:
    root = repo_root / BUG_CASES_ROOT
    if not root.is_dir():
        return []
    return sorted(root.glob(f"**/findings/{finding_id}.md"))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _finding_closed_fixed(path: Path) -> bool:
    return bool(_FINDING_CLOSED_RE.search(_read_text(path)))


def _finding_disposition_recorded(repo_root: Path, finding_id: str) -> bool:
    for path in _finding_paths(repo_root, finding_id):
        if _DISPOSITION_MARK_RE.search(_read_text(path)):
            return True
        case_md = path.parent.parent / "CASE.md"
        if case_md.is_file() and _DISPOSITION_MARK_RE.search(_read_text(case_md)):
            return True
    return False


def _native_batch_clear(
    repo_root: Path,
    mission_id: str,
    product: list[dict[str, str]],
    execution_id: str,
    head: str,
) -> bool:
    from bug_lifecycle.batch_clear import verify_batch_clear

    finding_ids = [row["finding"] for row in product if row.get("finding")]
    if not finding_ids or not execution_id or not head:
        return False
    verdict = verify_batch_clear(
        repo_root,
        mission_id=mission_id,
        execution_id=execution_id,
        head=head,
        finding_ids=finding_ids,
    )
    return verdict.get("BUG_BATCH_CLEAR") == "YES"


def _mint_mission(request: dict[str, object]) -> str:
    token = _opt(request, "now") or "local"
    safe = re.sub(r"[^A-Za-z0-9-]", "", token) or "local"
    return f"ORCH-bug-fix-{safe}"


def _mission_path(repo_root: Path, mission_id: str) -> Path:
    return repo_root / "opsx" / "runs" / "orch-missions" / mission_id / "mission.json"


def _load_mission(repo_root: Path, mission_id: str) -> dict[str, object] | None:
    path = _mission_path(repo_root, mission_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _store_mission(
    repo_root: Path,
    mission_id: str,
    envelope: dict[str, object],
    *,
    head: str,
    execution_id: str,
) -> None:
    path = _mission_path(repo_root, mission_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(envelope)
    payload["head"] = head
    payload["execution_id"] = execution_id
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _projectization_block(repo_root: Path) -> dict[str, object] | None:
    from opsx_runtime_gates import projectization_and_adoption_block

    return projectization_and_adoption_block(
        repo_root,
        "python.pipeline.orch_bug_fix",
        blocked_factory=_blocked,
    )


def _opt(request: dict[str, object], key: str) -> str:
    value = request.get(key)
    return value.strip() if isinstance(value, str) else ""


def _blocked(reason: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "STATUS": "FAIL_CLOSED",
        "MISSION_STATUS": "FAIL_CLOSED",
        "BUG_BATCH_CLEAR": "NO",
        "REPAIRED_THIS_BATCH": "NO",
        "REASON": reason,
        "TEMPORAL_NESTED_START": "NO",
        "TRANSPORT": "IN_PARENT_SYNC",
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
    return sealed


if __name__ == "__main__":
    raise SystemExit(main())
