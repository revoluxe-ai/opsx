"""Построчный цикл: проверка, один repair, один recheck; commit на каждую SUPPORTED.

Пакетный repair всего FAIL сюда не входит. Свежесть строки — секция spec
и test-файл. Журнал фиксирует начало и конец repair и результат recheck,
чтобы после падения не отправлять операцию с неизвестным исходом повторно.
Завершённая строка переиспользуется только пока этот token актуален.
После зелёной строки — локальный git commit через ``CommitAgent``.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import ActiveCursorAgentRunError
from agents.common.scenario_files import read_scenario_ids, scenario_test_relpath
from agents.verify.scenario_coverage_verify.delta_rows import ROW_CONTRACT, row_freshness_token
from agents.verify.scenario_coverage_verify.evidence_contract import (
    input_snapshot,
    scenario_verifier_contract_digest,
)
from agents.verify.scenario_coverage_verify.row_disposition import (
    build_scenario_coverage_row_context,
    resolve_row_work,
)
from agents.verify.scenario_coverage_verify.row_receipts import save_supported_row
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageRowReview,
    ScenarioCoverageVerifyAnswer,
)
from agents.verify.scenario_coverage_verify.input_pack import (
    assess_plan_materialization,
    load_scenario_coverage_plan_bundle,
)
from agents.adapters.abc_adapter import ABCAdapter
from agents.verify.scenario_coverage_verify.repair_lease import (
    OpenRepairDecision,
    resolve_open_repair,
)
from agents.verify.scenario_coverage_verify.row_cycle_green_commit import (
    commit_green_scenario_row,
)
from agents.repair.scenario_test_repair.workflow_pytest_defer import (
    workflow_defer_blocks_supported_verdict,
)

_LOGGER = logging.getLogger(__name__)
MAX_ROW_REPAIR_ATTEMPTS = 3

_PAUSE_MARKERS = (
    "provider_overload",
    "sdk_active_run",
    "sdk_not_terminal",
    "row_inputs_changed",
    "timeout",
    "unknown run",
)


class RowWalkJournal:
    """Журнал repair/recheck одной строки. Пишется до и после операции."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {}
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data = loaded

    def row(self, scenario_id: str) -> dict[str, Any]:
        current = self.data.get(scenario_id)
        return current if isinstance(current, dict) else {}

    def _write(self, scenario_id: str, payload: dict[str, Any]) -> None:
        self.data[scenario_id] = payload
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def repair_attempts(self, scenario_id: str) -> int:
        raw = self.row(scenario_id).get("repair_attempts")
        if isinstance(raw, int) and raw >= 0:
            return raw
        if isinstance(raw, float) and raw >= 0:
            return int(raw)
        return 0

    def reset_repair_attempts(self) -> int:
        """Обнулить ``repair_attempts`` во всех строках (CLI ``-r`` / ``--reset-sm``).

        Returns:
            Число строк, у которых счётчик был изменён.
        """
        reset_count = 0
        for scenario_id, raw in list(self.data.items()):
            if not isinstance(raw, dict):
                continue
            attempts = raw.get("repair_attempts")
            if attempts is None:
                continue
            if isinstance(attempts, (int, float)) and int(attempts) <= 0:
                continue
            self.data[scenario_id] = {**raw, "repair_attempts": 0}
            reset_count += 1
        if reset_count:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return reset_count

    def mark_repair_started(
        self,
        scenario_id: str,
        token: str,
        *,
        repair_run_uuid: str,
    ) -> None:
        current = self.row(scenario_id)
        started_at = datetime.now(timezone.utc).isoformat()
        self._write(
            scenario_id,
            {
                **current,
                "token": token,
                "repair": "started",
                "repair_run_uuid": repair_run_uuid,
                "repair_started_at": started_at,
            },
        )

    def mark_repair_aborted(
        self,
        scenario_id: str,
        token: str,
        *,
        reason: str,
    ) -> None:
        """Прерванный repair без учёта в ``repair_attempts``."""
        current = self.row(scenario_id)
        self._write(
            scenario_id,
            {
                **current,
                "token": token,
                "repair": "aborted",
                "repair_outcome": "INTERRUPTED",
                "repair_aborted_at": datetime.now(timezone.utc).isoformat(),
                "abort_reason": reason,
            },
        )

    def mark_repair_finished(
        self,
        scenario_id: str,
        token: str,
        outcome: str,
        *,
        changed_paths: list[str] | None = None,
        repair_surface: dict[str, Any] | None = None,
        pytest_receipt: dict[str, Any] | None = None,
        repair_block_reason: str | None = None,
    ) -> None:
        current = self.row(scenario_id)
        paths = (
            list(changed_paths)
            if changed_paths
            else list(current.get("changed_paths") or [])
        )
        attempts = self.repair_attempts(scenario_id) + 1
        payload: dict[str, Any] = {
            "token": token,
            "repair": "finished",
            "repair_outcome": outcome,
            "changed_paths": paths,
            "repair_attempts": attempts,
        }
        if repair_surface:
            payload["repair_surface"] = repair_surface
        if pytest_receipt:
            payload["pytest_receipt"] = pytest_receipt
        if repair_block_reason and outcome == "BLOCKED":
            payload["repair_block_reason"] = repair_block_reason[:500]
        self._write(scenario_id, payload)

    def record_recheck(self, scenario_id: str, token: str, answer: dict[str, Any]) -> None:
        current = self.row(scenario_id)
        payload: dict[str, Any] = {
            **current,
            "token": token,
            "repair": current.get("repair") or "skipped",
            "recheck": answer,
        }
        if str(current.get("green_commit_token") or "") != token:
            payload.pop("green_committed", None)
            payload.pop("green_commit_token", None)
        self._write(scenario_id, payload)

    def mark_green_committed(self, scenario_id: str, token: str) -> None:
        current = self.row(scenario_id)
        self._write(
            scenario_id,
            {**current, "green_committed": True, "green_commit_token": token},
        )

    def green_commit_valid(self, scenario_id: str, token: str) -> bool:
        row = self.row(scenario_id)
        return (
            row.get("green_committed") is True
            and str(row.get("green_commit_token") or "") == token
        )

    def row_changed_paths(self, scenario_id: str) -> list[str]:
        raw = self.row(scenario_id).get("changed_paths")
        if not isinstance(raw, list):
            return []
        return [str(path) for path in raw if str(path).strip()]


def transport_pause_reason(patch: dict[str, Any]) -> str | None:
    """SDK timeout, overload и неизвестный исход запуска. Не CONTRACT_CONFLICT."""
    answer = patch.get("scenario_coverage_answer")
    reason = ""
    if isinstance(answer, dict):
        reason = str(answer.get("block_reason") or "")
    status = str(patch.get("scenario_coverage_execution_status") or "")
    blob = f"{reason}\n{status}".lower()
    if any(marker in blob for marker in _PAUSE_MARKERS):
        return reason or status or "UNKNOWN_RUN"
    return None


def _answer_of(patch: dict[str, Any]) -> dict[str, Any]:
    answer = patch.get("scenario_coverage_answer")
    return answer if isinstance(answer, dict) else {}


def _supported(patch: dict[str, Any]) -> bool:
    answer = _answer_of(patch)
    rows = answer.get("row_reviews")
    return (
        answer.get("verdict") == "SCENARIO_TEST_COVERAGE_PASS"
        and isinstance(rows, list)
        and len(rows) == 1
        and isinstance(rows[0], dict)
        and rows[0].get("assessment") == "SUPPORTED"
    )


def _row_audit_supported(repo: Path, patch: dict[str, Any]) -> bool:
    """Структурный audit SUPPORTED; veto только без маркера на defer-tier."""
    if not _supported(patch):
        return False
    reviews = _answer_of(patch).get("row_reviews")
    if not isinstance(reviews, list) or not reviews or not isinstance(reviews[0], dict):
        return False
    node = str(reviews[0].get("test_node_id") or "")
    return not workflow_defer_blocks_supported_verdict(repo, node)


def _repair_state(
    state: dict[str, Any],
    check: dict[str, Any],
    scenario_id: str,
    *,
    repair_attempts: int = 0,
    repo: Path | None = None,
    change_id: str = "",
) -> dict[str, Any]:
    answer = dict(_answer_of(check))
    answer["verdict"] = "SCENARIO_TEST_COVERAGE_FAIL"
    gaps = answer.get("gaps")
    if not isinstance(gaps, list) or not gaps:
        answer["gaps"] = [{
            "scenario_id": scenario_id,
            "gap_class": "MISSING_OR_WEAK_SCENARIO_TEST",
            "evidence": "Строка не в актуальном SUPPORTED",
        }]
    run_uuid = str(state.get("run_uuid") or "")
    digest = str(answer.get("source_digest") or "").strip()
    prior = check.get("scenario_coverage_source_snapshot")
    if isinstance(prior, dict) and str(prior.get("source_digest") or "").strip():
        digest = str(prior.get("source_digest") or "").strip()
    if repo is not None and change_id:
        try:
            digest = str(input_snapshot(repo, change_id)["source_digest"])
            answer["source_digest"] = digest
        except (OSError, ValueError, KeyError):
            pass
    return {
        **state,
        "scenario_test_repair_authorized": True,
        "scenario_test_repair_graph_hop": True,
        "verify_scenario_repair_audit_run_uuid": run_uuid,
        "scenario_test_repair_row_attempts": repair_attempts,
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
        "scenario_coverage_answer": answer,
        "scenario_coverage_source_snapshot": {"source_digest": digest},
        "scenario_coverage_execution_id": run_uuid,
    }


def _single_row(piece: dict[str, Any], scenario_id: str) -> dict[str, Any] | None:
    reviews = piece.get("row_reviews")
    if not isinstance(reviews, list) or len(reviews) != 1:
        return None
    row = reviews[0]
    if not isinstance(row, dict) or str(row.get("scenario_id") or "") != scenario_id:
        return None
    return row


def _combine(
    change_id: str,
    order: list[str],
    pieces_by_scenario: dict[str, dict[str, Any]],
) -> ScenarioCoverageVerifyAnswer:
    """Свести построчные ответы: PASS только если каждый id из ``order`` — SUPPORTED."""
    rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    reasons: list[str] = []
    unclosed: list[str] = []
    plan_evidence_id = ""
    source_digest = ""
    for scenario_id in order:
        piece = pieces_by_scenario.get(scenario_id)
        if not piece:
            unclosed.append(scenario_id)
            continue
        if not plan_evidence_id:
            plan_evidence_id = str(piece.get("plan_evidence_id") or "")
        digest = str(piece.get("source_digest") or "")
        if digest:
            source_digest = digest
        piece_verdict = str(piece.get("verdict") or "")
        reason = str(piece.get("block_reason") or "").strip()
        if piece_verdict == "SCENARIO_TEST_COVERAGE_UNKNOWN" or reason:
            reasons.append(reason or f"ROW_UNKNOWN:{scenario_id}")
        row = _single_row(piece, scenario_id)
        if row is None:
            unclosed.append(scenario_id)
            continue
        rows.append(row)
        found = piece.get("gaps")
        if isinstance(found, list):
            gaps.extend(item for item in found if isinstance(item, dict))
    blocked = any(
        row.get("assessment") in ("CONTRACT_CONFLICT", "INSPECTION_BLOCKED") for row in rows
    )
    complete = not unclosed and len(rows) == len(order)
    if complete and all(row.get("assessment") == "SUPPORTED" for row in rows):
        verdict = "SCENARIO_TEST_COVERAGE_PASS"
    elif blocked or reasons or unclosed:
        verdict = "SCENARIO_TEST_COVERAGE_UNKNOWN"
        if unclosed and not reasons:
            reasons.append(
                "ROW_WALK_INCOMPLETE: "
                + ", ".join(unclosed[:8])
                + ("…" if len(unclosed) > 8 else ""),
            )
    else:
        verdict = "SCENARIO_TEST_COVERAGE_FAIL"
    block_reason = ""
    if verdict == "SCENARIO_TEST_COVERAGE_UNKNOWN":
        block_reason = "; ".join(dict.fromkeys(reason for reason in reasons if reason))
    return ScenarioCoverageVerifyAnswer.model_validate({
        "verdict": verdict,
        "plan_evidence_id": plan_evidence_id,
        "source_digest": source_digest or "row-walk-aggregate",
        "row_reviews": rows,
        "gaps": gaps if verdict != "SCENARIO_TEST_COVERAGE_PASS" else [],
        "summary": (
            f"Построчный обход {change_id}: {len(rows)}/{len(order)} сценариев закрыто"
        ),
        "block_reason": block_reason,
        "local_hop_copy_block": f"Change: {change_id}\nVerdict: {verdict}",
    })


def _bind_matrix(repo: Path, change_id: str, scenario_id: str) -> str | None:
    """Обновить mapping, если repair записал канонический файл с одной test-функцией."""
    import ast

    rel = scenario_test_relpath(change_id, scenario_id)
    path = resource_path(repo, rel)
    if not path.is_file():
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return None
    functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    ]
    if len(functions) != 1:
        return None
    node_id = f"{rel}::{functions[0].name}"
    from workflows.artifact_paths import scenario_machine_receipt_path

    matrix = scenario_machine_receipt_path(repo, change_id, "test_trace_matrix.json")
    if not matrix.is_file():
        return None
    payload = json.loads(matrix.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None
    changed = False
    bound = False
    for entry in entries:
        if isinstance(entry, dict) and entry.get("scenario_id") == scenario_id:
            bound = True
            if entry.get("test_node_id") != node_id:
                entry["test_node_id"] = node_id
                changed = True
    if not bound:
        entries.append({"scenario_id": scenario_id, "test_node_id": node_id})
        changed = True
    if not changed:
        return None
    matrix.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return matrix.relative_to(repo).as_posix()


def _pause_patch(change_id: str, reason: str) -> dict[str, Any]:
    answer = ScenarioCoverageVerifyAnswer(
        verdict="SCENARIO_TEST_COVERAGE_UNKNOWN",
        summary="Построчный обход остановлен: исход операции неизвестен.",
        block_reason=reason,
        local_hop_copy_block=f"Change: {change_id}\nPAUSE: {reason}",
    )
    return {
        "scenario_coverage_verdict": answer.verdict,
        "scenario_coverage_marker": "⏸ SCENARIO_COVERAGE_BLOCKED",
        "scenario_coverage_answer": answer.model_dump(mode="json"),
        "scenario_coverage_report_text": reason,
        "scenario_row_walk_settled": False,
    }


def _walk_authority(state: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    repo = Path(str(state["repository_path"]))
    model = state.get("model")
    if not isinstance(model, dict):
        model = {"id": "composer-2.5", "options": []}
    plan_id = str((bundle.get("plan_receipt") or {}).get("PLAN_EVIDENCE_ID") or "")
    verifier = str(state.get("verifier_contract_digest") or "").strip()
    if not verifier:
        try:
            verifier = scenario_verifier_contract_digest(repo, model)
        except ValueError:
            verifier = "0" * 64
    return {
        "model": model,
        "plan_evidence_id": plan_id,
        "verifier_contract_digest": verifier,
    }


def _answer_piece_from_row(
    row: ScenarioCoverageRowReview,
    *,
    plan_evidence_id: str,
    source_digest: str,
) -> dict[str, Any]:
    return {
        "verdict": "SCENARIO_TEST_COVERAGE_PASS",
        "plan_evidence_id": plan_evidence_id,
        "source_digest": source_digest,
        "row_reviews": [row.model_dump(mode="json")],
        "gaps": [],
        "block_reason": "",
        "summary": "row settled by durable supported receipt",
        "local_hop_copy_block": "",
    }


def _persist_walk_supported_row(
    state: dict[str, Any],
    *,
    repo: Path,
    change_id: str,
    ctx: Any,
    scenario_id: str,
    check: Any,
) -> None:
    """Записать PASS receipt на диск после SUPPORTED audit/recheck в walk."""
    reviews = _answer_of(check).get("row_reviews")
    if not isinstance(reviews, list) or not reviews:
        return
    try:
        row = ScenarioCoverageRowReview.model_validate(reviews[0])
    except (ValueError, TypeError, KeyError):
        return
    if row.assessment != "SUPPORTED" or row.scenario_id != scenario_id:
        return
    if workflow_defer_blocks_supported_verdict(repo, row.test_node_id):
        return
    execution_id = str(state.get("run_uuid") or "").strip()
    if not execution_id:
        return
    row_digest = ctx.row_digests.get(scenario_id)
    if not row_digest:
        return
    evidence_id = f"{execution_id}:row-walk:{scenario_id}"
    _ = save_supported_row(
        repo,
        change_id=change_id,
        row=row,
        mapping=ctx.mapping,
        row_digest=row_digest,
        plan_evidence_id=ctx.plan_evidence_id,
        origin_execution_id=execution_id,
        evidence_id=evidence_id,
        row_identity_contract=ROW_CONTRACT,
    )


async def _commit_if_supported(
    state: dict[str, Any],
    *,
    repo: Path,
    change_id: str,
    scenario_id: str,
    token: str,
    extra_paths: list[str],
    commit_adapter: ABCAdapter | None,
    row_commits: list[dict[str, Any]],
    journal: RowWalkJournal,
) -> str | None:
    """Вернуть причину PAUSE, если commit обязателен, но не удался."""
    if journal.green_commit_valid(scenario_id, token):
        return None
    outcome = await commit_green_scenario_row(
        state,
        repo=repo,
        change_id=change_id,
        scenario_id=scenario_id,
        extra_paths=extra_paths,
        commit_adapter=commit_adapter,
    )
    if not isinstance(outcome, dict):
        return f"ROW_COMMIT_FAILED:{scenario_id}:no_outcome"
    status = str(outcome.get("commit_status") or "")
    if status in ("FAILED", "BLOCKED"):
        detail = str(outcome.get("error") or status)
        return f"ROW_COMMIT_FAILED:{scenario_id}:{detail}"
    if status in ("COMMITTED", "SKIPPED_NO_DIFF", "SKIPPED_NO_GIT"):
        row_commits.append({
            "scenario_id": scenario_id,
            "commit_status": status,
            "commit_sha": outcome.get("commit_sha"),
            "commit_files": outcome.get("commit_files") or [],
            "commit_subject": outcome.get("commit_subject"),
        })
        if status == "COMMITTED":
            journal.mark_green_committed(scenario_id, token)
    return None


async def run_scenario_row_walk(
    state: dict[str, Any],
    *,
    audit_agent: Any,
    repair_agent: Any,
    commit_adapter: ABCAdapter | None = None,
) -> dict[str, Any]:
    """Обойти сценарии по порядку; строка закрыта только SUPPORTED.

    На каждую не-SUPPORTED: read-only audit → repair → read-only recheck;
    subprocess pytest в этом walk не вызывается (исполнение — отдельные gates).
    До 3 завершённых repair, затем PAUSE ``ROW_REPAIR_ATTEMPTS_EXCEEDED``.
    """
    change_id = str(state.get("change_id") or "").strip()
    repo = Path(str(state["repository_path"]))
    bundle = load_scenario_coverage_plan_bundle(change_id, repo_root=repo)
    problem = assess_plan_materialization(bundle)
    if problem:
        return _pause_patch(change_id, problem)
    coverage = resource_path(repo, "openspec", "changes", change_id, "scenario-coverage")
    try:
        order = read_scenario_ids(coverage, allow_historical=False)
    except ValueError as exc:
        return _pause_patch(change_id, str(exc))
    from agents.common.scenario_files import row_walk_journal_machine_path

    journal = RowWalkJournal(row_walk_journal_machine_path(coverage))
    pieces_by_scenario: dict[str, dict[str, Any]] = {}
    row_commits: list[dict[str, Any]] = []
    try:
        from agents.verify.scenario_coverage_verify.review_semantics import trace_mappings
        mapping = trace_mappings(repo, change_id)
    except (OSError, ValueError):
        mapping = {}
    total_rows = len(order)
    for row_index, scenario_id in enumerate(order, start=1):
        _LOGGER.info(
            "scenario_row_walk row=%s/%s scenario_id=%s change_id=%s",
            row_index,
            total_rows,
            scenario_id,
            change_id,
        )
        row_extra: list[str] = []
        node = str(mapping.get(scenario_id) or "")
        check_state = {**state, "scenario_row_focus": scenario_id}
        while True:
            token = row_freshness_token(repo, change_id, scenario_id, node)
            recorded = journal.row(scenario_id)
            if recorded.get("repair") == "started":
                current_run_uuid = str(state.get("run_uuid") or "").strip()
                resolution = resolve_open_repair(
                    recorded,
                    repo=repo,
                    change_id=change_id,
                    current_run_uuid=current_run_uuid,
                )
                if resolution.decision == OpenRepairDecision.PAUSE_STILL_ACTIVE:
                    return _pause_patch(
                        change_id,
                        resolution.pause_reason or f"REPAIR_STILL_ACTIVE: {scenario_id}",
                    )
                if resolution.decision == OpenRepairDecision.PAUSE_UNRESOLVED:
                    return _pause_patch(
                        change_id,
                        resolution.pause_reason or f"REPAIR_OUTCOME_UNKNOWN: {scenario_id}",
                    )
                journal.mark_repair_aborted(
                    scenario_id,
                    str(recorded.get("token") or token),
                    reason="reconcile_orphan_started",
                )
                recorded = journal.row(scenario_id)
            if journal.repair_attempts(scenario_id) >= MAX_ROW_REPAIR_ATTEMPTS:
                return _pause_patch(
                    change_id,
                    f"ROW_REPAIR_ATTEMPTS_EXCEEDED: {scenario_id}",
                )
            try:
                row_ctx = build_scenario_coverage_row_context(
                    repo, change_id, authority=_walk_authority(state, bundle),
                )
            except (ValueError, OSError, KeyError) as exc:
                return _pause_patch(change_id, f"ROW_CONTEXT_INVALID: {exc}")
            work = resolve_row_work(row_ctx, scenario_id, disposition_mode="walk")
            if not work.run_check_repair_cycle and work.row_review is not None:
                pieces_by_scenario[scenario_id] = _answer_piece_from_row(
                    work.row_review,
                    plan_evidence_id=row_ctx.plan_evidence_id,
                    source_digest=row_ctx.source_digest,
                )
                row_extra.extend(journal.row_changed_paths(scenario_id))
                commit_pause = await _commit_if_supported(
                    state,
                    repo=repo,
                    change_id=change_id,
                    scenario_id=scenario_id,
                    token=token,
                    extra_paths=row_extra,
                    commit_adapter=commit_adapter,
                    row_commits=row_commits,
                    journal=journal,
                )
                if commit_pause:
                    return _pause_patch(change_id, commit_pause)
                break
            check = await audit_agent.run(check_state)
            paused = transport_pause_reason(check)
            if paused:
                return _pause_patch(change_id, paused)
            mapped = ""
            reviews = _answer_of(check).get("row_reviews")
            if isinstance(reviews, list) and reviews and isinstance(reviews[0], dict):
                mapped = str(reviews[0].get("test_node_id") or "")
            token = row_freshness_token(repo, change_id, scenario_id, mapped or node)
            if _row_audit_supported(repo, check):
                answer_piece = _answer_of(check)
                _persist_walk_supported_row(
                    state,
                    repo=repo,
                    change_id=change_id,
                    ctx=row_ctx,
                    scenario_id=scenario_id,
                    check=check,
                )
                journal.record_recheck(scenario_id, token, answer_piece)
                pieces_by_scenario[scenario_id] = answer_piece
                commit_pause = await _commit_if_supported(
                    state,
                    repo=repo,
                    change_id=change_id,
                    scenario_id=scenario_id,
                    token=token,
                    extra_paths=row_extra,
                    commit_adapter=commit_adapter,
                    row_commits=row_commits,
                    journal=journal,
                )
                if commit_pause:
                    return _pause_patch(change_id, commit_pause)
                break
            _LOGGER.info(
                "scenario_row_repair_start row=%s/%s attempt=%s scenario_id=%s",
                row_index,
                total_rows,
                journal.repair_attempts(scenario_id) + 1,
                scenario_id,
            )
            repair_run_uuid = str(state.get("run_uuid") or "").strip()
            journal.mark_repair_started(
                scenario_id, token, repair_run_uuid=repair_run_uuid,
            )
            from agents.repair.scenario_test_repair.repair_context import binding_for_scenario

            repair_surface = binding_for_scenario(repo, change_id, scenario_id)
            weak_stub = None
            try:
                from agents.repair.scenario_test_repair.trace_quality import (
                    assess_weak_mapped_stub_row,
                )

                weak_stub = assess_weak_mapped_stub_row(repo, change_id, scenario_id)
            except (OSError, ValueError, ImportError):
                weak_stub = None
            if weak_stub:
                _LOGGER.warning(
                    "scenario_row_weak_mapped_stub row=%s scenario_id=%s detail=%s",
                    row_index,
                    scenario_id,
                    weak_stub,
                )
            try:
                repair_patch = await repair_agent.run(_repair_state(
                    state,
                    check,
                    scenario_id,
                    repair_attempts=journal.repair_attempts(scenario_id),
                    repo=repo,
                    change_id=change_id,
                ))
            except ActiveCursorAgentRunError as exc:
                journal.mark_repair_aborted(
                    scenario_id, token, reason=f"SDK_ACTIVE_RUN_CONFLICT: {exc}",
                )
                return _pause_patch(change_id, f"SDK_ACTIVE_RUN_CONFLICT: {exc}")
            except Exception as exc:
                journal.mark_repair_aborted(
                    scenario_id,
                    token,
                    reason=f"{type(exc).__name__}: {exc}",
                )
                return _pause_patch(
                    change_id, f"REPAIR_OUTCOME_UNKNOWN: {type(exc).__name__}: {exc}",
                )
            outcome = str(repair_patch.get("scenario_test_repair_status") or "UNKNOWN")
            if outcome not in ("CANDIDATE_PENDING_VERIFY", "BLOCKED", "NO_CHANGE", "PARTIAL"):
                journal.mark_repair_aborted(
                    scenario_id, token, reason=f"unknown_outcome:{outcome}",
                )
                return _pause_patch(change_id, f"REPAIR_OUTCOME_UNKNOWN: {outcome}")
            paths = repair_patch.get("scenario_test_repair_changed_paths")
            if isinstance(paths, list):
                row_extra.extend(str(path) for path in paths)
            matrix = _bind_matrix(repo, change_id, scenario_id)
            if matrix:
                row_extra.append(matrix)
            rebind = repair_patch.get("scenario_test_repair_matrix_rebind")
            if isinstance(rebind, dict):
                rebound_path = rebind.get("matrix_path")
                if isinstance(rebound_path, str):
                    row_extra.append(rebound_path)
            repair_block_reason = ""
            if outcome == "BLOCKED":
                raw_reason = repair_patch.get("scenario_test_repair_owner_attention")
                if not raw_reason:
                    repair_answer = repair_patch.get("scenario_test_repair_answer")
                    if isinstance(repair_answer, dict):
                        raw_reason = repair_answer.get("recovery_contract")
                repair_block_reason = str(raw_reason or "").strip()
            journal.mark_repair_finished(
                scenario_id,
                token,
                outcome,
                changed_paths=row_extra,
                repair_surface=repair_surface,
                repair_block_reason=repair_block_reason or None,
            )
            if repair_block_reason:
                _LOGGER.info(
                    "scenario_row_repair_finished row=%s/%s attempts=%s outcome=%s "
                    "scenario_id=%s block_reason=%s",
                    row_index,
                    total_rows,
                    journal.repair_attempts(scenario_id),
                    outcome,
                    scenario_id,
                    repair_block_reason[:200],
                )
            else:
                _LOGGER.info(
                    "scenario_row_repair_finished row=%s/%s attempts=%s outcome=%s scenario_id=%s",
                    row_index,
                    total_rows,
                    journal.repair_attempts(scenario_id),
                    outcome,
                    scenario_id,
                )
            recheck = await audit_agent.run(check_state)
            paused = transport_pause_reason(recheck)
            if paused:
                return _pause_patch(change_id, paused)
            reviews = _answer_of(recheck).get("row_reviews")
            mapped = ""
            if isinstance(reviews, list) and reviews and isinstance(reviews[0], dict):
                mapped = str(reviews[0].get("test_node_id") or "")
            token = row_freshness_token(repo, change_id, scenario_id, mapped)
            recheck_answer = _answer_of(recheck)
            if _row_audit_supported(repo, recheck):
                _persist_walk_supported_row(
                    state,
                    repo=repo,
                    change_id=change_id,
                    ctx=row_ctx,
                    scenario_id=scenario_id,
                    check=recheck,
                )
            journal.record_recheck(scenario_id, token, recheck_answer)
            pieces_by_scenario[scenario_id] = recheck_answer
            if _row_audit_supported(repo, recheck):
                commit_pause = await _commit_if_supported(
                    state,
                    repo=repo,
                    change_id=change_id,
                    scenario_id=scenario_id,
                    token=token,
                    extra_paths=row_extra,
                    commit_adapter=commit_adapter,
                    row_commits=row_commits,
                    journal=journal,
                )
                if commit_pause:
                    return _pause_patch(change_id, commit_pause)
                break
    answer = _combine(change_id, order, pieces_by_scenario)
    from agents.verify.scenario_coverage_verify.agent import _answer_to_state_patch

    patch = _answer_to_state_patch(change_id=change_id, answer=answer)
    patch["scenario_row_walk_settled"] = True
    if row_commits:
        patch["scenario_row_green_commits"] = row_commits
    return patch
