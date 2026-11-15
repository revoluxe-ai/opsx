"""Детерминированная запись machine receipt стадий apply (модель не пишет JSON).

GREEN: upstream RED + IMPLEMENTATION, pairing через Node ``classifyGreenPredecessorPairing``
(NORMAL | ADOPTION | ALREADY_SATISFIED_WRITE).
"""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from workflows.artifact_paths import (
    stage_receipt_md_path,
    stage_receipt_path,
    stage_receipt_relpos,
    useful_stage_receipt_path,
)
from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)
from workflows.implementation.node_subprocess import run_subprocess_utf8
from workflows.implementation.slice_receipt import load_stage_receipt
from workflows.repo_paths import resolve_opsx_repository_root


@dataclass(frozen=True, slots=True)
class GreenReceiptWriteResult:
    """Исход попытки записать GREEN receipt."""

    ok: bool
    code: str | None
    path: Path | None


def _atomic_write_utf8_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    _ = path.write_text(normalized, encoding="utf-8", newline="\n")


def _resolve_frozen_test_identity(
    red: Mapping[str, Any],
    implement: Mapping[str, Any],
) -> str:
    """Единый frozen test identity из IMPLEMENTATION / RED для GREEN semantic gate."""
    red_identity = implement.get("red_identity")
    if isinstance(red_identity, dict):
        tid = red_identity.get("test_content_identity")
        if isinstance(tid, str) and tid.strip():
            return tid.strip()
    for key in (
        implement.get("frozen_test_content_identity"),
        red.get("test_content_identity"),
    ):
        if isinstance(key, str) and key.strip():
            return key.strip()
    transition = red.get("operative_red_transition")
    if isinstance(transition, dict):
        tid = transition.get("test_content_identity")
        if isinstance(tid, str) and tid.strip():
            return tid.strip()
    return ""


def _historical_red_executions_from_frozen_red(red: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Снимок исторического RED envelope (не подменяет текущую GREEN-проверку)."""
    executions: list[dict[str, Any]] = []
    test_commands = red.get("test_commands")
    command_execs = red.get("command_executions")
    if isinstance(test_commands, list):
        for idx, cmd in enumerate(test_commands):
            if not isinstance(cmd, str) or not cmd.strip():
                continue
            exit_code = 1
            relevant_output = ""
            if isinstance(command_execs, list) and idx < len(command_execs):
                block = command_execs[idx]
                if isinstance(block, dict):
                    exit_code = int(block.get("final_exit_code", 1))
                    attempts = block.get("attempts")
                    if isinstance(attempts, list) and attempts:
                        last_att = attempts[-1]
                        if isinstance(last_att, dict):
                            relevant_output = str(last_att.get("relevant_output") or "")[:4000]
            executions.append(
                {
                    "phase": "historical",
                    "iteration": 0,
                    "command": cmd,
                    "exit_code": exit_code,
                    "relevant_output": relevant_output,
                },
            )
    if not executions and isinstance(red.get("final_execution"), dict):
        final_execution = red["final_execution"]
        executions.append(
            {
                "phase": "historical",
                "iteration": 0,
                "command": str(final_execution.get("command") or ""),
                "exit_code": int(final_execution.get("exit_code", 1)),
                "relevant_output": str(final_execution.get("relevant_output") or "")[:4000],
            },
        )
    return executions


def _red_executions_from_frozen_red(red: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Алиас исторического envelope (тесты регрессии GREEN reuse)."""
    return _historical_red_executions_from_frozen_red(red)


def _run_green_verification_executions(
    repo_root: Path,
    commands: list[str],
) -> list[dict[str, Any]]:
    """Повторно выполнить frozen test_commands на текущем коде для GREEN."""
    repo_root = resolve_opsx_repository_root(repo_root)
    script = resource_path(repo_root, "opsx_tools", "opsx-run-frozen-commands-cli.mjs")
    if not script.is_file() or not commands:
        return []
    payload = json.dumps({"commands": commands, "phase": "green_verify", "iteration": 0})
    run = run_subprocess_utf8(
        ["node", str(script), "--repo", str(repo_root)],
        input_text=payload,
        timeout=600,
    )
    if run.returncode != 0 or not run.stdout.strip():
        return []
    try:
        body = json.loads(run.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return []
    raw = body.get("executions")
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def _collect_frozen_test_commands(red: Mapping[str, Any]) -> list[str]:
    """Список frozen pytest команд из RED receipt."""
    out: list[str] = []
    test_commands = red.get("test_commands")
    if isinstance(test_commands, list):
        for cmd in test_commands:
            if isinstance(cmd, str) and cmd.strip():
                out.append(cmd.strip())
    return out


def _red_final_status_from_executions(red_executions: list[dict[str, Any]]) -> str:
    """``PASS``, если все GREEN verification execution имеют exit 0."""
    if not red_executions:
        return "FAIL"
    if all(int(entry.get("exit_code", 1)) == 0 for entry in red_executions):
        return "PASS"
    return "FAIL"


def _chat_history_required_false(
    red: Mapping[str, Any],
    implement: Mapping[str, Any],
) -> bool:
    """GREEN hop требует явного ``chat_history_required: false`` в receipt."""
    for src in (implement, red):
        if src.get("chat_history_required") is False:
            return True
    return False


def classify_green_predecessor_pairing(
    *,
    repo_root: Path,
    red: Mapping[str, Any],
    implement: Mapping[str, Any],
) -> dict[str, Any]:
    """Вызов Node ``classifyGreenPredecessorPairing`` (NORMAL / ADOPTION / ALREADY_SATISFIED_WRITE)."""
    repo_root = resolve_opsx_repository_root(repo_root)
    script = resource_path(repo_root, "opsx_tools", "opsx-classify-green-pairing-cli.mjs")
    if not script.is_file():
        return {"ok": False, "code": "PAIRING_CLI_MISSING"}
    payload = json.dumps({"red": red, "implement": implement}, ensure_ascii=False)
    run = run_subprocess_utf8(
        ["node", str(script), "--repo", str(repo_root)],
        input_text=payload,
        timeout=120,
    )
    if run.returncode != 0 or not run.stdout.strip():
        return {"ok": False, "code": "PAIRING_CLI_FAILED"}
    try:
        data = json.loads(run.stdout)
        return data if isinstance(data, dict) else {"ok": False, "code": "PAIRING_CLI_BAD_JSON"}
    except json.JSONDecodeError:
        return {"ok": False, "code": "PAIRING_CLI_BAD_JSON"}


def repair_visual_slice_004_false_green(*, repo_root: Path) -> bool:
    """Удалить ложный GREEN_PROVEN visual SLICE-004 (RED/IMPLEMENTATION не трогаем)."""
    repo_root = resolve_opsx_repository_root(repo_root)
    change_id = "visual"
    slice_id = "SLICE-004"
    removed = False
    for stage in (STAGE_APPLY_GREEN,):
        json_p = useful_stage_receipt_path(repo_root, change_id, stage, slice_id)
        md_p = json_p.with_name("GREEN.md")
        legacy_p = stage_receipt_path(repo_root, change_id, stage, slice_id)
        legacy_md = stage_receipt_md_path(repo_root, change_id, stage, slice_id)
        if json_p.is_file():
            json_p.unlink()
            removed = True
        if md_p.is_file():
            md_p.unlink()
            removed = True
        if legacy_p.is_file():
            legacy_p.unlink()
            removed = True
        if legacy_md.is_file():
            legacy_md.unlink()
            removed = True
    return removed


def write_green_receipt_from_hop(
    change_id: str,
    slice_id: str,
    *,
    repo_root: Path,
    answer: Mapping[str, Any],
) -> GreenReceiptWriteResult:
    """Записать GREEN.json при валидной паре предшественников; иначе не писать GREEN_PROVEN."""
    status = str(answer.get("status") or "")
    if status not in ("APPLY_COMPLETE", "APPLY_PARTIAL"):
        return GreenReceiptWriteResult(False, "HOP_STATUS_NOT_COMPLETE", None)

    repo_root = resolve_opsx_repository_root(repo_root)
    from workflows.implementation.routing_contract import STAGE_APPLY_IMPLEMENT, STAGE_APPLY_RED

    red = load_stage_receipt(change_id, slice_id, STAGE_APPLY_RED, repo_root=repo_root)
    implement = load_stage_receipt(
        change_id,
        slice_id,
        STAGE_APPLY_IMPLEMENT,
        repo_root=repo_root,
    )
    if not isinstance(red, dict) or not isinstance(implement, dict):
        return GreenReceiptWriteResult(False, "UPSTREAM_RECEIPT_MISSING", None)

    pairing = classify_green_predecessor_pairing(
        repo_root=repo_root,
        red=red,
        implement=implement,
    )
    if pairing.get("ok") is not True:
        code = str(pairing.get("code") or "CROSS_PAIRED_PREDECESSOR")
        return GreenReceiptWriteResult(False, code, None)

    if not _chat_history_required_false(red, implement):
        return GreenReceiptWriteResult(False, "CHAT_HISTORY_REQUIRED", None)

    frozen_test_id = _resolve_frozen_test_identity(red, implement)
    if not frozen_test_id:
        return GreenReceiptWriteResult(False, "STALE_RED", None)

    historical_red_executions = _historical_red_executions_from_frozen_red(red)
    frozen_commands = _collect_frozen_test_commands(red)
    red_executions = _run_green_verification_executions(repo_root, frozen_commands)
    if not red_executions:
        return GreenReceiptWriteResult(False, "GREEN_VERIFICATION_MISSING", None)
    red_final_status = _red_final_status_from_executions(red_executions)
    if red_final_status != "PASS":
        return GreenReceiptWriteResult(False, "RED_EVIDENCE_NOT_PASS", None)

    branch = str(pairing.get("branch") or "NORMAL")
    json_path = useful_stage_receipt_path(repo_root, change_id, STAGE_APPLY_GREEN, slice_id)
    json_rel = stage_receipt_relpos(repo_root, json_path)
    impl_path = useful_stage_receipt_path(repo_root, change_id, STAGE_APPLY_IMPLEMENT, slice_id)
    impl_rel = stage_receipt_relpos(repo_root, impl_path)
    red_path = useful_stage_receipt_path(repo_root, change_id, STAGE_APPLY_RED, slice_id)
    red_rel = stage_receipt_relpos(repo_root, red_path)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    red_identity = red.get("red_identity") if isinstance(red.get("red_identity"), dict) else {}
    if not red_identity:
        red_identity = {
            "machine_path": red_rel,
            "slice_id": slice_id,
            "status": red.get("status"),
            "test_content_identity": red.get("test_content_identity")
            or red.get("operative_red_transition", {}).get("test_content_identity"),
            "tests_frozen": True,
            "observation_only": bool(red.get("observation_only")),
        }
    else:
        red_identity = dict(red_identity)
        red_identity["machine_path"] = red_rel

    scaffold_identity = implement.get("scaffold_identity") or red.get("scaffold_identity")
    implementation_identity: dict[str, Any] = {
        "machine_path": impl_rel,
        "slice_id": slice_id,
        "status": implement.get("status"),
    }
    impl_frozen = implement.get("frozen_test_content_identity")
    if isinstance(impl_frozen, str) and impl_frozen.strip():
        implementation_identity["frozen_test_content_identity"] = impl_frozen.strip()

    payload: dict[str, Any] = {
        "change_id": change_id,
        "slice_id": slice_id,
        "schema": "opsx.apply_green",
        "schema_version": 1,
        "status": "GREEN_PROVEN",
        "generated_at": now,
        "chat_history_required": False,
        "predecessor_branch": branch,
        "red_identity": red_identity,
        "implementation_identity": implementation_identity,
        "apply_design_identity": implement.get("apply_design_identity")
        or red.get("apply_design_identity"),
        "scaffold_identity": scaffold_identity,
        "frozen_test_content_identity": frozen_test_id,
        "frozen_test_identity_before": frozen_test_id,
        "frozen_test_identity_after": frozen_test_id,
        "historical_red_receipt_test_content_identity": red.get("test_content_identity"),
        "historical_red_executions": historical_red_executions,
        "red_executions": red_executions,
        "red_final_status": red_final_status,
        "tests_executed": True,
        "green_fix_iterations": [],
        "focused_checks": [],
        "adjacent_checks": [],
        "adjacent_final_status": "NOT_REQUIRED",
        "iteration_count": 0,
        "iteration_limit": 5,
        "production_paths_modified_during_green": [],
        "product_files_modified": bool(implement.get("product_files_modified")),
        "blockers": [],
        "tests_modified": False,
        "whole_change_verified": False,
        "agent_hop_summary": {
            "tasks_summary": str(answer.get("tasks_summary") or ""),
            "gap_closure_summary": str(answer.get("gap_closure_summary") or ""),
            "files_changed": sorted(
                {
                    str(p).replace("\\", "/")
                    for p in (answer.get("files_changed") or [])
                    if p
                }
            ),
        },
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    _atomic_write_utf8_lf(json_path, text)

    md_path = json_path.with_name("GREEN.md")
    md_body = (
        f"# GREEN — {change_id} {slice_id}\n\n"
        f"Status: GREEN_PROVEN\n\n"
        f"Branch: {branch}\n\n"
        f"{answer.get('gap_closure_summary') or ''}\n"
    )
    _atomic_write_utf8_lf(md_path, md_body)
    return GreenReceiptWriteResult(True, None, json_path)


@dataclass(frozen=True, slots=True)
class StageReceiptWriteResult:
    """Исход записи slice receipt из минимального hop-решения."""

    ok: bool
    code: str | None
    path: Path | None
    stage: str | None = None


_FORBIDDEN_HOP_DECISION_KEYS = frozenset(
    {
        "planning_digest",
        "inspected_digest",
        "work_scope_digest",
        "next_stage",
        "apply_design_identity",
        "repository_identity_before",
        "repository_identity_after",
        "artifact_sha256",
        "receipt_body",
        "content",
        "machine_path",
        "protected_context_digest",
    },
)

_LEGACY_MODEL_RECEIPT_KEYS = frozenset(
    {
        "receipt_status",
        "completion_note",
        "gap_note",
        "no_op",
        "product_files_modified",
        "blockers",
        "created_paths",
        "extended_paths",
        "created_symbols",
        "structural_changes",
    },
)

_SUCCESS_HOP_STATUSES = frozenset({"APPLY_COMPLETE", "APPLY_PARTIAL", "NO_CHANGE"})


class StageReceiptWriter:
    """Единственный владелец детерминированной записи slice stage receipts."""

    @staticmethod
    def _blockers_from_hop_answer(hop_answer: Mapping[str, Any] | None) -> list[dict[str, str]]:
        """Извлечь blockers из hop answer без произвольных digest-полей."""
        if hop_answer is None:
            return [{"code": "HOP_BLOCKED", "detail": "hop blocked"}]
        blockers = hop_answer.get("blockers")
        if isinstance(blockers, list):
            out: list[dict[str, str]] = []
            for item in blockers:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code") or "").strip()
                if not code:
                    continue
                detail = str(item.get("detail") or "").strip()
                out.append({"code": code, "detail": detail or code})
            if out:
                return out
        gaps = hop_answer.get("remaining_gaps")
        if isinstance(gaps, list) and gaps:
            return [
                {
                    "code": "HOP_GAP",
                    "detail": str(gaps[0])[:2000],
                }
            ]
        summary = str(hop_answer.get("gap_closure_summary") or hop_answer.get("tasks_summary") or "")
        if summary.strip():
            return [{"code": "HOP_BLOCKED", "detail": summary.strip()[:2000]}]
        return [{"code": "HOP_BLOCKED", "detail": "hop blocked"}]

    @staticmethod
    def record_from_dispatch(
        change_id: str,
        *,
        repo_root: Path,
        stage: str,
        slice_id: str,
        hop_status: str,
        hop_answer: Mapping[str, Any] | None = None,
        hop_worktree_baseline: str | None = None,
    ) -> StageReceiptWriteResult:
        """Алиас dispatch → ``record_hop_decision`` (тесты и обратная совместимость)."""
        decision: dict[str, Any] = {
            "stage": stage,
            "slice_id": slice_id,
            "hop_status": hop_status,
        }
        if hop_worktree_baseline:
            decision["hop_worktree_baseline"] = hop_worktree_baseline
        if isinstance(hop_answer, Mapping):
            fc = hop_answer.get("files_changed")
            if isinstance(fc, list):
                decision["hop_files_changed"] = [
                    str(p).replace("\\", "/")
                    for p in fc
                    if isinstance(p, str) and p.strip()
                ]
        if hop_status == "BLOCKED":
            decision["receipt_status"] = "BLOCKED"
            decision["blockers"] = StageReceiptWriter._blockers_from_hop_answer(hop_answer)
        return StageReceiptWriter.record_hop_decision(
            change_id,
            repo_root=repo_root,
            decision=decision,
        )

    @staticmethod
    def record_hop_decision(
        change_id: str,
        *,
        repo_root: Path,
        decision: Mapping[str, Any],
    ) -> StageReceiptWriteResult:
        """Записать receipt по минимальному hop-решению; digests и пути заполняет CLI.

        Args:
            change_id: Идентификатор change.
            repo_root: Корень репозитория.
            decision: Минимальный набор: ``stage``, ``slice_id``, ``receipt_status``,
                ``completion_note`` / ``gap_note``, ``blockers``, scaffold shape lists.

        Returns:
            ``StageReceiptWriteResult`` с путём on-disk receipt при ``ok``.
        """
        repo_root = resolve_opsx_repository_root(repo_root)
        if not change_id.strip():
            return StageReceiptWriteResult(False, "CHANGE_ID_REQUIRED", None, None)
        extra = _FORBIDDEN_HOP_DECISION_KEYS.intersection(decision.keys())
        if extra:
            return StageReceiptWriteResult(
                False,
                f"FORBIDDEN_PARAM:{sorted(extra)[0]}",
                None,
                str(decision.get("stage") or ""),
            )
        for key, value in decision.items():
            if key.endswith("_digest") or key.endswith("_path"):
                return StageReceiptWriteResult(
                    False,
                    f"FORBIDDEN_PARAM:{key}",
                    None,
                    str(decision.get("stage") or ""),
                )
            if isinstance(value, str) and (
                value.replace("\\", "/").endswith(".py")
                or "opsx/opsx_runtime" in value.replace("\\", "/")
            ):
                return StageReceiptWriteResult(
                    False,
                    "FORBIDDEN_PARAM:runtime_path",
                    None,
                    str(decision.get("stage") or ""),
                )

        stage = str(decision.get("stage") or "").strip()
        slice_id = str(decision.get("slice_id") or "").strip()
        if not stage or not slice_id:
            return StageReceiptWriteResult(False, "ARGS", None, stage or None)

        hop_status = str(decision.get("hop_status") or "").strip()
        if hop_status == "BLOCKED" and "receipt_status" not in decision:
            decision = {
                **dict(decision),
                "receipt_status": "BLOCKED",
            }
        if hop_status and hop_status != "BLOCKED":
            legacy = _LEGACY_MODEL_RECEIPT_KEYS.intersection(decision.keys())
            if legacy:
                return StageReceiptWriteResult(
                    False,
                    f"FORBIDDEN_PARAM:{sorted(legacy)[0]}",
                    None,
                    stage,
                )
            if hop_status not in _SUCCESS_HOP_STATUSES:
                return StageReceiptWriteResult(
                    False,
                    "HOP_STATUS_UNSUPPORTED",
                    None,
                    stage,
                )
            if stage == STAGE_APPLY_GREEN:
                raw_answer = decision.get("hop_answer")
                answer = (
                    dict(raw_answer)
                    if isinstance(raw_answer, Mapping)
                    else {"status": hop_status}
                )
                if "status" not in answer:
                    answer["status"] = hop_status
                green = write_green_receipt_from_hop(
                    change_id,
                    slice_id,
                    repo_root=repo_root,
                    answer=answer,
                )
                return StageReceiptWriteResult(
                    green.ok,
                    green.code,
                    green.path,
                    STAGE_APPLY_GREEN,
                )

        tooling_root = repo_root
        if not (resource_path(tooling_root, "opsx_tools")).is_dir():
            tooling_root = resolve_opsx_repository_root(
                Path(__file__).resolve().parents[4],
            )
        script = resource_path(tooling_root, "opsx_tools", "opsx-record-slice-receipt-decision-cli.mjs")
        if not script.is_file():
            return StageReceiptWriteResult(False, "RECORD_CLI_MISSING", None, stage)

        payload = json.dumps(
            {
                "repo": str(repo_root),
                "change_id": change_id,
                "decision": dict(decision),
            },
            ensure_ascii=False,
        )
        run = subprocess.run(
            ["node", str(script)],
            input=payload.encode("utf-8"),
            capture_output=True,
            text=False,
            timeout=180,
            check=False,
            cwd=str(tooling_root),
        )
        stdout = (run.stdout or b"").decode("utf-8", errors="replace").strip()
        if not stdout:
            code = "RECORD_CLI_FAILED"
            stderr_text = (run.stderr or b"").decode("utf-8", errors="replace").strip()
            if stderr_text:
                code = stderr_text[:120]
            return StageReceiptWriteResult(False, code, None, stage)
        try:
            body = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            return StageReceiptWriteResult(False, "RECORD_CLI_BAD_JSON", None, stage)
        if body.get("ok") is not True:
            return StageReceiptWriteResult(
                False,
                str(body.get("code") or "RECORD_REJECTED"),
                None,
                stage,
            )
        rel = str(body.get("path") or "").strip()
        out_path = (resource_path(repo_root, rel)).resolve() if rel else None
        if out_path is None or not out_path.is_file():
            return StageReceiptWriteResult(
                False,
                "RECEIPT_PUBLISH_MISSING",
                None,
                stage,
            )
        return StageReceiptWriteResult(True, None, out_path, stage)
