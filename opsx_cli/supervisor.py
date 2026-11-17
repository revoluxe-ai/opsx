"""While/try-except контроллер workflow implementation→verify.

Контракт orchestration: ``opsx/contract_prompts/process_supervisor/SUPERVISOR.md``
(``loader.load_supervisor_orchestration_contract``). Fix-hop грузит ``FIX_AGENT.md``
через тот же loader; §27.0.0.2 в ``opsx/opsx.mdc``.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .context import OpsxError
from .supervisor_classify import FailureClassification
from .supervisor_qualifier import QualifierVerdict, qualify_failure, verdict_to_dict
from .supervisor_events import format_opsx_event
from .supervisor_journal import JournalEntry, SupervisorJournal, supervisor_journal_path
from .supervisor_meta import (
    diff_fingerprints,
    git_dirty_fingerprints,
    validate_meta_write_paths,
)
from .supervisor_subprocess import kill_process_tree, read_log_tail, run_worker_with_watchdog
from .supervisor_timeouts import SupervisorTimeouts
from .site_status import keep_site_running
from .storage import read_json, write_json

if TYPE_CHECKING:
    from .service import RunService


MAX_FIX_ROUNDS = 3
"""Сколько раз подряд fix-агент пробует починить один и тот же отказ процесса."""

SOLVER_ACTION = "REPAIR_AGENT"
"""Единственный solver-hop при несданном workflow: усилить существующий агент."""


def normalize_solver_choice(fix_result: dict[str, Any]) -> dict[str, str] | None:
    """Извлечь ``REPAIR_AGENT`` и имя агента из ``ProcessFixReport``."""
    action = str(fix_result.get("solver_action") or "").upper().strip()
    if action == "CREATE_AGENT":
        return None
    if action and action != SOLVER_ACTION:
        return None
    agent = str(
        fix_result.get("repair_agent")
        or fix_result.get("agent_name")
        or ""
    ).strip()
    if not agent:
        return None
    return {"solver_action": SOLVER_ACTION, "repair_agent": agent}


def infer_solver_choice_from_files(fix_result: dict[str, Any]) -> dict[str, str] | None:
    """Эвристика по allowlisted путям, если отчёт не заполнил ``solver_action``."""
    paths = [str(p).replace("\\", "/") for p in fix_result.get("files_changed") or []]
    agents_root = "opsx/opsx_runtime/agents/"
    touched: list[str] = []
    for raw in paths:
        text = raw
        if text.startswith("opsx/core/"):
            text = text[len("opsx/core/") :]
        idx = text.find(agents_root)
        if idx < 0:
            continue
        rest = text[idx + len(agents_root) :]
        parts = rest.split("/")
        if parts and parts[0]:
            touched.append(parts[0])
    if not touched:
        return None
    unique = sorted(set(touched))
    if len(unique) == 1 and unique[0] != "process_repair":
        return {
            "solver_action": "REPAIR_AGENT",
            "repair_agent": unique[0],
        }
    return None


def journal_solver_choice(
    journal: SupervisorJournal,
    *,
    fix_result: dict[str, Any],
    change_id: str,
    run_id: str,
    attempt: int,
    entry_id: str,
) -> dict[str, str] | None:
    """Записать в ``journal.sqlite`` выбор ``REPAIR_AGENT`` (имя существующего агента)."""
    declared = str(fix_result.get("solver_action") or "").upper().strip()
    if declared == "CREATE_AGENT":
        journal.note(
            kind="SOLVER_CHOICE",
            what="CREATE_AGENT rejected",
            why="solver-hop only strengthens an existing agent (REPAIR_AGENT)",
            change_id=change_id,
            run_id=run_id,
            attempt=attempt,
            entry_id=entry_id,
        )
        return None
    choice = normalize_solver_choice(fix_result) or infer_solver_choice_from_files(fix_result)
    if choice is None:
        journal.note(
            kind="SOLVER_CHOICE",
            what="solver_action not recorded",
            why="fix report must declare solver_action REPAIR_AGENT and repair_agent",
            change_id=change_id,
            run_id=run_id,
            attempt=attempt,
            entry_id=entry_id,
        )
        return None
    what = f"{choice['solver_action']} agent={choice['repair_agent']}"
    journal.note(
        kind="SOLVER_CHOICE",
        what=what,
        why=str(fix_result.get("root_cause") or fix_result.get("error") or "")[:2000],
        change_id=change_id,
        run_id=run_id,
        attempt=attempt,
        entry_id=entry_id,
        **choice,
    )
    return choice


def describe_fix_round(
    *,
    verdict: str,
    root_cause: str,
    error: str,
    files: list[str],
    status: str,
) -> tuple[str, str]:
    """Строка журнала раунда: что вышло и почему, в том числе при пустом diff.

    ``why`` никогда не пустой: если агент ничего не изменил и не назвал
    причину, в журнал пишется именно это.
    """
    shown = verdict or "NONE"
    what = f"verdict={shown} status={status or 'UNKNOWN'} files={len(files)}"
    reason = (root_cause or error or "").strip()
    if not files:
        if reason:
            why = f"ничего не изменено: {reason}"
        else:
            why = "ничего не изменено: агент не назвал причину"
    elif reason:
        why = reason
    else:
        why = "причина в отчёте не указана"
    return what, why[:4000]


class WorkflowSupervisor:
    """Один supervised run: попытки workflow, META fix, journal, commit, restart."""

    def __init__(
        self,
        service: RunService,
        *,
        timeouts: SupervisorTimeouts,
        change_id: str,
        run_id: str,
        echo: bool = False,
    ) -> None:
        self._service = service
        self._timeouts = timeouts
        self._change_id = change_id
        self._run_id = run_id
        self._echo = echo
        self._project = service.project
        self._controller_dir = self._project.runs / "controller" / run_id
        self._log_path = self._controller_dir / "output.log"
        self._journal = SupervisorJournal(
            supervisor_journal_path(self._project.runs / "supervisor" / run_id),
        )
        self._started_mono = time.monotonic()
        self._deadline = self._started_mono + timeouts.supervisor_timeout_seconds
        self._dirty_at_start = set(git_dirty_fingerprints(self._project.root, self._project.core).keys())
        self._fixed_progress_keys: set[str] = set()
        self._seen_fail_progress_keys: set[str] = set()
        self._process: Any | None = None

    @staticmethod
    def _progress_key(classification: FailureClassification) -> str:
        """Стабильный ключ no-progress: code + stage (не растущий хвост лога)."""
        return f"{classification.code}|{classification.stage}"

    def _remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def _supervisor_timeout(self, reason: str, record: dict[str, Any]) -> dict[str, Any]:
        record.update(
            status="FAILED",
            workflow_status="SUPERVISOR_TIMEOUT",
            verification_status="FAIL",
            exit_code=1,
            error=reason,
            site_error=True,
            supervisor_stop="SUPERVISOR_TIMEOUT",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return record

    async def _append_log_line(self, text: str) -> None:
        def append() -> None:
            with self._log_path.open("a", encoding="utf-8") as stream:
                stream.write(text + "\n")

        import asyncio

        await asyncio.to_thread(append)

    async def _emit(self, event: str, **payload: Any) -> None:
        body = {"event": event, "run_id": self._run_id, "change_id": self._change_id, **payload}
        await self._append_log_line(format_opsx_event(body))

    async def _say(self, text: str) -> None:
        """Строка супервизора в общем output.log, рядом с логами остальных агентов."""
        await self._append_log_line(f"supervisor: {text}")

    async def _load_runtime(self) -> tuple[str, dict[str, str]]:
        return await self._service.resolve_worker_runtime()

    async def _run_worker_mode(
        self,
        *,
        mode: str,
        request_payload: dict[str, Any],
        request_name: str,
        wall_clock: float,
        log_path: Path | None = None,
    ) -> tuple[int, str | None]:
        interpreter, env = await self._load_runtime()
        target_log = log_path or self._log_path
        request_path = Path(request_name) if not isinstance(request_name, Path) else request_name
        if not request_path.is_absolute():
            request_path = self._controller_dir / request_path
        await write_json(request_path, request_payload)
        return await run_worker_with_watchdog(
            interpreter=interpreter,
            request_path=request_path,
            log_path=target_log,
            cwd=self._project.root,
            env=env,
            wall_clock_limit=wall_clock,
            silence_limit=self._timeouts.silence_limit_seconds,
            echo=self._echo,
        )

    async def _workflow_attempt(self, attempt: int) -> tuple[dict[str, Any], str | None]:
        remaining = self._remaining()
        if remaining < self._timeouts.workflow_timeout_seconds:
            raise OpsxError("SUPERVISOR_TIMEOUT", "Недостаточно бюджета супер-агента для новой попытки workflow.")
        wall = min(self._timeouts.workflow_timeout_seconds, remaining)
        request = {
            "schema_version": 1,
            "mode": "workflow",
            "change_id": self._change_id,
            "bug": "",
            "timeout_seconds": wall,
            "project_root": str(self._project.root),
            "core_root": str(self._project.core),
            "supervised_run_id": self._run_id,
            "attempt": attempt,
        }
        code, hang = await self._run_worker_mode(
            mode="workflow",
            request_payload=request,
            request_name=f"attempt-{attempt}-workflow.json",
            wall_clock=wall,
        )
        record = await read_json(self._controller_dir / "status.json", {})
        outcome = await read_json(
            self._controller_dir / f"attempt-{attempt}-workflow.outcome.json",
            {},
        )
        if outcome:
            record["verification_status"] = outcome.get("verification_status", record.get("verification_status"))
            record["workflow_status"] = outcome.get("workflow_status", record.get("workflow_status"))
            record["exit_code"] = outcome.get("exit_code", record.get("exit_code"))
            if outcome.get("error"):
                record["error"] = outcome["error"]
        if hang:
            record["error"] = hang
            record.setdefault("workflow_status", "HANG")
            record.setdefault("verification_status", "FAIL")
        if code != 0 and record.get("verification_status") != "PASS":
            record.setdefault("exit_code", code)
        passed = record.get("verification_status") == "PASS" and int(record.get("exit_code") or 1) == 0
        if not passed:
            visible = keep_site_running(await read_json(self._controller_dir / "status.json", {}))
            await write_json(self._controller_dir / "status.json", visible)
        return record, hang

    async def _run_qualify_pipeline(
        self,
        attempt: int,
        record: dict[str, Any],
        hang: str | None,
    ) -> QualifierVerdict:
        """Qualifier-hop: классификация без правок; журнал ``QUALIFY``."""
        remaining = self._remaining()
        if remaining < 30.0:
            raise OpsxError("SUPERVISOR_TIMEOUT", "Недостаточно бюджета для qualifier-hop.")
        attempt_dir = self._controller_dir / "attempts" / str(attempt)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        tail = read_log_tail(self._log_path)
        qualify_request = {
            "schema_version": 1,
            "mode": "supervise_qualify",
            "change_id": self._change_id,
            "project_root": str(self._project.root),
            "core_root": str(self._project.core),
            "attempt_dir": str(attempt_dir),
            "worker_record": record,
            "hang_reason": hang,
            "log_tail": tail,
        }
        _code, _hang = await self._run_worker_mode(
            mode="supervise_qualify",
            request_payload=qualify_request,
            request_name=attempt_dir / "qualify_request.json",
            wall_clock=min(120.0, remaining),
            log_path=self._log_path,
        )
        payload = await read_json(attempt_dir / "qualify_result.json", {})
        if not payload:
            payload = verdict_to_dict(qualify_failure(record, log_tail=tail, hang_reason=hang))
        verdict = QualifierVerdict(
            owner=str(payload.get("owner") or "PRODUCT"),
            code=str(payload.get("code") or "UNKNOWN"),
            stage=str(payload.get("stage") or "UNKNOWN_STAGE"),
            fingerprint=str(payload.get("fingerprint") or ""),
            root_cause=str(payload.get("root_cause") or ""),
            message=str(payload.get("message") or ""),
            fix_hop_allowed=bool(payload.get("fix_hop_allowed")),
        )
        await self._emit(
            "QUALIFY",
            attempt=attempt,
            owner=verdict.owner,
            code=verdict.code,
            fix_hop_allowed=verdict.fix_hop_allowed,
        )
        self._note(
            kind="QUALIFY",
            what=f"qualifier owner={verdict.owner} code={verdict.code}",
            why=verdict.root_cause or verdict.message,
            attempt=attempt,
            owner=verdict.owner,
            code=verdict.code,
            stage=verdict.stage,
            fingerprint=verdict.fingerprint,
            fix_hop_allowed=verdict.fix_hop_allowed,
        )
        return verdict

    async def _run_fix_pipeline(
        self,
        attempt: int,
        classification: FailureClassification,
    ) -> JournalEntry:
        remaining = self._remaining()
        if remaining < self._timeouts.min_fix_budget_seconds:
            raise OpsxError("SUPERVISOR_TIMEOUT", "Недостаточно бюджета для fix-hop.")
        entry_id = self._journal.new_entry_id()
        attempt_dir = self._controller_dir / "attempts" / str(attempt)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        before = git_dirty_fingerprints(self._project.root, self._project.core)
        await self._emit(
            "SOLVE",
            attempt=attempt,
            entry_id=entry_id,
            fingerprint=classification.fingerprint,
        )
        self._note(
            kind="SOLVE",
            what=f"solver hop for {classification.code}",
            why=classification.message[:2000],
            attempt=attempt,
            entry_id=entry_id,
            owner=classification.owner.value,
            code=classification.code,
        )
        await self._emit("FIX", attempt=attempt, entry_id=entry_id, fingerprint=classification.fingerprint)
        await self._say(f"чиню процесс ({classification.code}), страница этого запуска не закрывается")
        fix_request = {
            "schema_version": 1,
            "mode": "supervise_fix",
            "change_id": self._change_id,
            "project_root": str(self._project.root),
            "core_root": str(self._project.core),
            "timeout_seconds": remaining,
            "entry_id": entry_id,
            "classification": asdict(classification),
            "log_tail": read_log_tail(self._log_path),
            "log_path": str(self._log_path),
            "journal_path": str(self._journal.path),
            "attempt_dir": str(attempt_dir),
            "prior_dead_ends": self._journal.prior_dead_ends(self._change_id),
        }
        code, hang = await self._run_worker_mode(
            mode="supervise_fix",
            request_payload=fix_request,
            request_name=attempt_dir / "fix_request.json",
            wall_clock=remaining,
            log_path=self._log_path,
        )
        fix_result = await read_json(attempt_dir / "fix_result.json", {})
        if hang and not fix_result:
            fix_result = {"verdict": "CANNOT_FIX", "error": hang}
        after = git_dirty_fingerprints(self._project.root, self._project.core)
        import importlib

        meta_mod = importlib.reload(importlib.import_module("opsx_cli.supervisor_meta"))
        changed, scope_error = meta_mod.select_fix_commit_files(fix_result, before, after)
        files_changed = [
            {"repo": fp.repo, "path": fp.path, "sha256": fp.sha256} for fp in changed
        ]
        entry = JournalEntry(
            entry_id=entry_id,
            attempt=attempt,
            fingerprint=classification.fingerprint,
            classification=asdict(classification),
            status="FIX_PENDING",
            created_at=SupervisorJournal.now_iso(),
            fix_report=fix_result,
            files_changed=files_changed,
            change_id=self._change_id,
        )
        verdict = str(fix_result.get("verdict") or "").upper()
        _ = journal_solver_choice(
            self._journal,
            fix_result=fix_result,
            change_id=self._change_id,
            run_id=self._run_id,
            attempt=attempt,
            entry_id=entry_id,
        )
        if verdict == "FIXED" and normalize_solver_choice(fix_result) is None:
            if infer_solver_choice_from_files(fix_result) is None:
                entry.status = "FIX_REJECTED"
                entry.error = "FIXED requires journal solver_action REPAIR_AGENT"
                self._journal.append(entry)
                await write_json(attempt_dir / f"{entry_id}.json", asdict(entry))
                self._journal_fix_round(attempt=attempt, entry=entry, fix_result=fix_result)
                return entry
        if scope_error and verdict == "FIXED":
            entry.status = "FIX_REJECTED"
            entry.error = scope_error
            self._journal.append(entry)
            await write_json(attempt_dir / f"{entry_id}.json", asdict(entry))
            self._journal_fix_round(attempt=attempt, entry=entry, fix_result=fix_result)
            return entry
        if code != 0 or verdict not in {"FIXED"}:
            entry.status = "FIX_REJECTED" if verdict != "FIX_INTERRUPTED" else "FIX_INTERRUPTED"
            entry.error = str(
                fix_result.get("error")
                or fix_result.get("root_cause")
                or f"fix worker exit {code}; verdict={verdict or 'NONE'}"
            )
            self._journal.append(entry)
            await write_json(attempt_dir / f"{entry_id}.json", asdict(entry))
            self._journal_fix_round(attempt=attempt, entry=entry, fix_result=fix_result)
            return entry
        import importlib

        meta = importlib.reload(importlib.import_module("opsx_cli.supervisor_meta"))
        ok, reason = meta.validate_meta_write_paths(
            changed,
            dirty_at_start=self._dirty_at_start,
            core_root=self._project.core,
        )
        if not ok:
            entry.status = "FIX_REJECTED"
            entry.error = reason
            self._journal.append(entry)
            await write_json(attempt_dir / f"{entry_id}.json", asdict(entry))
            self._journal_fix_round(attempt=attempt, entry=entry, fix_result=fix_result)
            return entry
        budget = min(remaining, 600.0)
        rerun_request = {
            "schema_version": 1,
            "mode": "supervise_rerun_checks",
            "project_root": str(self._project.root),
            "core_root": str(self._project.core),
            "attempt_dir": str(attempt_dir),
            "fix_report": fix_result,
            "timeout_seconds": budget,
        }
        _rc, _hang = await self._run_worker_mode(
            mode="supervise_rerun_checks",
            request_payload=rerun_request,
            request_name=attempt_dir / f"{entry_id}-rerun-checks.json",
            wall_clock=budget + 30.0,
            log_path=self._log_path,
        )
        rerun_payload = await read_json(attempt_dir / "rerun_checks_result.json", {})
        checks = list(rerun_payload.get("checks_run") or [])
        if not checks:
            checks = await self._service.rerun_fix_checks(
                fix_result, timeout_seconds=budget
            )
        entry.checks_run = checks
        if any(row.get("exit_code", 1) != 0 for row in checks):
            entry.status = "FIX_REJECTED"
            entry.error = "named pytest checks failed after fix"
            self._journal.append(entry)
            await write_json(attempt_dir / f"{entry_id}.json", asdict(entry))
            self._journal_fix_round(attempt=attempt, entry=entry, fix_result=fix_result)
            return entry
        entry.status = "FIXED"
        self._journal.append(entry)
        await write_json(attempt_dir / f"{entry_id}.json", asdict(entry))
        await self._emit("COMMIT", attempt=attempt, entry_id=entry_id)
        commit_request = {
            "schema_version": 1,
            "mode": "supervise_commit",
            "project_root": str(self._project.root),
            "core_root": str(self._project.core),
            "entry_id": entry_id,
            "attempt_dir": str(attempt_dir),
            "journal_path": str(self._journal.path),
            "timeout_seconds": self._remaining(),
        }
        _c_code, _c_hang = await self._run_worker_mode(
            mode="supervise_commit",
            request_payload=commit_request,
            request_name=attempt_dir / "commit_request.json",
            wall_clock=self._remaining(),
            log_path=self._log_path,
        )
        commit_result = await read_json(attempt_dir / "commit_result.json", {})
        if str(commit_result.get("status") or "").upper() != "COMMITTED":
            entry.status = "COMMIT_FAILED"
            entry.commit_status = str(commit_result.get("status") or "FAILED")
            entry.error = str(commit_result.get("error") or "commit failed")
        else:
            entry.commit_status = "COMMITTED"
            entry.commit_sha = commit_result.get("commit_sha")
            self._fixed_progress_keys.add(self._progress_key(classification))
        self._journal.append(entry)
        await write_json(attempt_dir / f"{entry_id}.json", asdict(entry))
        self._journal_fix_round(attempt=attempt, entry=entry, fix_result=fix_result)
        return entry

    def _journal_fix_round(
        self,
        *,
        attempt: int,
        entry: JournalEntry,
        fix_result: dict[str, Any],
    ) -> None:
        """Журнал раунда: вердикт и причина, даже если файлы не менялись."""
        files = [str(row.get("path") or "") for row in entry.files_changed]
        rejected = entry.status in {"FIX_REJECTED", "COMMIT_FAILED", "FIX_INTERRUPTED"}
        cause = "" if rejected else str(fix_result.get("root_cause") or "")
        err = str(entry.error or fix_result.get("root_cause") or fix_result.get("error") or "")
        what, why = describe_fix_round(
            verdict=str(fix_result.get("verdict") or ""),
            root_cause=cause,
            error=err,
            files=files,
            status=entry.status,
        )
        self._note(
            kind="FIX_ROUND",
            what=what,
            why=why,
            attempt=attempt,
            entry_id=entry.entry_id,
            files=files[:30],
        )

    async def _run_fix_rounds(
        self,
        attempt: int,
        classification: FailureClassification,
    ) -> JournalEntry:
        """Несколько раундов ремонта процесса для одного отказа.

        Отклонённый раунд (FIX_REJECTED: агент сдался, вышел за allowlist или
        провалил проверки) не завершает supervisor: следующий раунд получает его
        причину в ``prior_dead_ends`` и пробует другой путь. Бюджет раундов
        ограничен ``MAX_FIX_ROUNDS``; ``COMMIT_FAILED`` и ``FIX_INTERRUPTED``
        повтором не лечатся.
        """
        entry = await self._run_fix_pipeline(attempt, classification)
        for round_no in range(2, MAX_FIX_ROUNDS + 1):
            if entry.status != "FIX_REJECTED":
                break
            self._note(
                kind="FIX_RETRY",
                what=f"fix round {round_no - 1} rejected; starting round {round_no}",
                why=entry.error or "fix rejected",
                attempt=attempt,
                entry_id=entry.entry_id,
            )
            entry = await self._run_fix_pipeline(attempt, classification)
        return entry

    async def run(self) -> dict[str, Any]:
        """Главный цикл до PASS или terminal failure."""
        record: dict[str, Any] = {
            "run_id": self._run_id,
            "status": "RUNNING",
            "mode": "workflow",
            "change_id": self._change_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "log": str(self._log_path),
            "supervisor": True,
            "supervisor_journal": str(self._journal.path),
        }
        await write_json(self._controller_dir / "status.json", record)
        self._note(
            kind="SESSION_START",
            what="supervised workflow started",
            why="panel or CLI asked for implementation→verify under the supervisor",
            attempt=0,
        )
        attempt = 0
        try:
            while attempt < self._timeouts.max_attempts:
                if self._remaining() < self._timeouts.workflow_timeout_seconds:
                    reason = "SUPERVISOR_TIMEOUT: недостаточно бюджета для новой попытки"
                    self._note(
                        kind="STOP",
                        what="no new workflow attempt",
                        why=reason,
                        attempt=attempt,
                    )
                    await self._say(f"таймаут, запуск остановлен. {reason}")
                    return await self._finalize(self._supervisor_timeout(reason, record))
                attempt += 1
                await self._emit("SUPERVISOR_ATTEMPT", attempt=attempt, remaining_sec=round(self._remaining(), 1))
                await self._say(f"попытка {attempt} в том же запуске")
                self._note(
                    kind="ATTEMPT_START",
                    what=f"workflow attempt {attempt}",
                    why="previous attempt did not PASS or this is the first attempt",
                    attempt=attempt,
                    remaining_sec=round(self._remaining(), 1),
                )
                record, hang = await self._workflow_attempt(attempt)
                if record.get("verification_status") == "PASS" and int(record.get("exit_code", 1)) == 0:
                    record.update(
                        status="PASS",
                        workflow_status="SUPERVISED_PASS",
                        exit_code=0,
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                    await write_json(self._controller_dir / "status.json", record)
                    await self._emit("SUPERVISOR_PASS", attempt=attempt)
                    self._note(
                        kind="PASS",
                        what="worker returned verification PASS",
                        why="exit_code 0 and verification_status PASS",
                        attempt=attempt,
                    )
                    return record
                qualifier = await self._run_qualify_pipeline(attempt, record, hang)
                classification = qualifier.as_classification()
                self._note(
                    kind="ATTEMPT_FAIL",
                    what=f"attempt {attempt} did not PASS",
                    why=f"{qualifier.owner}: {qualifier.message}",
                    attempt=attempt,
                    owner=qualifier.owner,
                    code=qualifier.code,
                    stage=qualifier.stage,
                    fingerprint=qualifier.fingerprint,
                    repair_eligible=qualifier.fix_hop_allowed,
                    hang=hang,
                )
                if not qualifier.fix_hop_allowed:
                    await self._say(
                        f"попытка {attempt} не сдана ({qualifier.code}). "
                        f"супервизор останавливается: {qualifier.owner}"
                    )
                    record.update(
                        status="FAILED",
                        workflow_status=qualifier.code,
                        verification_status="FAIL",
                        exit_code=1,
                        supervisor_stop=qualifier.owner,
                        site_error=False,
                        error=qualifier.message,
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                    self._note(
                        kind="STOP",
                        what="supervisor will not start a solver hop",
                        why=(
                            f"qualifier owner {qualifier.owner} fix_hop_allowed=false; "
                            f"{qualifier.root_cause or qualifier.message}"
                        ),
                        attempt=attempt,
                        owner=qualifier.owner,
                        code=qualifier.code,
                    )
                    await write_json(self._controller_dir / "status.json", record)
                    return record
                await self._say(
                    f"попытка {attempt} не сдана ({qualifier.code}). "
                    f"тот же запуск продолжается — solver hop"
                )
                progress_key = self._progress_key(classification)
                if progress_key in self._fixed_progress_keys:
                    reason = "NO_PROGRESS: повтор того же fingerprint после META fix"
                    record = self._supervisor_timeout(reason, record)
                    record["supervisor_stop"] = "NO_PROGRESS"
                    record["site_error"] = False
                    self._note(kind="STOP", what="same failure after a committed fix", why=reason, attempt=attempt)
                    await write_json(self._controller_dir / "status.json", record)
                    return record
                self._seen_fail_progress_keys.add(progress_key)
                try:
                    entry = await self._run_fix_rounds(attempt, classification)
                except OpsxError as exc:
                    if exc.code == "SUPERVISOR_TIMEOUT":
                        record = self._supervisor_timeout(str(exc), record)
                        record["supervisor_stop"] = "SUPERVISOR_TIMEOUT"
                        self._note(kind="STOP", what="fix hop not finished", why=str(exc), attempt=attempt)
                        await self._say(f"таймаут, запуск остановлен. {exc}")
                        await write_json(self._controller_dir / "status.json", record)
                        return record
                    raise
                if entry.commit_status != "COMMITTED":
                    if entry.status == "FIX_INTERRUPTED":
                        record = self._supervisor_timeout("SUPERVISOR_TIMEOUT during fix", record)
                        await self._say("таймаут, запуск остановлен. SUPERVISOR_TIMEOUT during fix")
                    else:
                        record.update(
                            status="FAILED",
                            workflow_status=entry.status,
                            verification_status="FAIL",
                            exit_code=1,
                            error=entry.error,
                            site_error=False,
                            supervisor_entry_id=entry.entry_id,
                            finished_at=datetime.now(timezone.utc).isoformat(),
                        )
                    self._note(
                        kind="STOP",
                        what=f"fix/commit ended as {entry.status}",
                        why=entry.error or entry.commit_status or entry.status,
                        attempt=attempt,
                        entry_id=entry.entry_id,
                    )
                    await write_json(self._controller_dir / "status.json", record)
                    return record
                await self._emit("RESTART", attempt=attempt, entry_id=entry.entry_id)
                await self._say("правка процесса принята, тот же запуск продолжается")
                self._note(
                    kind="RESTART",
                    what="committed META fix; next attempt is a new process",
                    why=f"journal entry {entry.entry_id} commit {entry.commit_sha}",
                    attempt=attempt,
                    entry_id=entry.entry_id,
                )
            record.update(
                status="FAILED",
                workflow_status="ATTEMPTS_EXHAUSTED",
                verification_status="FAIL",
                exit_code=1,
                site_error=False,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            self._note(
                kind="STOP",
                what="attempt budget exhausted",
                why=f"max_attempts={self._timeouts.max_attempts}",
                attempt=attempt,
            )
            await write_json(self._controller_dir / "status.json", record)
            return record
        except BaseException as exc:
            record.update(
                status="FAILED",
                error=f"{type(exc).__name__}: {exc}",
                site_error=True,
                supervisor_stop="EXCEPTION",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            await self._say(f"процесс супервизора упал. {type(exc).__name__}: {exc}")
            self._note(
                kind="EXCEPTION",
                what=type(exc).__name__,
                why=str(exc),
                attempt=attempt,
            )
            await write_json(self._controller_dir / "status.json", record)
            raise

    def _note(self, *, kind: str, what: str, why: str, attempt: int, **extra: Any) -> None:
        """Строка журнала: что, почему, путь к полному output.log и хвост для разбора."""
        self._journal.note(
            kind=kind,
            what=what,
            why=why,
            change_id=self._change_id,
            run_id=self._run_id,
            attempt=attempt,
            log_path=str(self._log_path),
            log_tail=read_log_tail(self._log_path, max_bytes=12_000),
            **extra,
        )

    async def _finalize(self, record: dict[str, Any]) -> dict[str, Any]:
        record["supervisor_journal"] = str(self._journal.path)
        await write_json(self._controller_dir / "status.json", record)
        return record