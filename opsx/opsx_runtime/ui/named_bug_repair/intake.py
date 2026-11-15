"""Owner-named bug intake → bug-team repair hop (без discovery/hunt).

Текст из HTML-формы превращается в BugBot-shaped ``review`` и передаётся в
``make_bug_team_repair_hop`` — тот же repair graph, что verify check-repair после
найденного finding.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from stream_log import emit_console_line, stream_process_output

from workflows.repo_paths import resolve_opsx_repository_root
from workflows.repair.progress import emit_repair_progress, observe_repair_progress
from workflows.verify.bugbot.bugbot_pipeline.check_repair_graph import (
    make_bug_team_repair_hop,
)

_DEFAULT_CHANGE_ID = "owner-named"


def _derive_bug_title(bug_text: str) -> str:
    """Короткий заголовок для ``location`` из первой непустой строки текста Owner."""
    text = bug_text.strip()
    if not text:
        raise ValueError("bug is required")
    for line in text.splitlines():
        title = line.strip()
        if title:
            return title[:200]
    return text[:200]


def build_bugbot_review_from_owner_text(*, bug_text: str) -> dict[str, Any]:
    """Собрать ``review`` для ``bugbot_review_to_finding_report`` без поиска багов.

    Args:
        bug_text: Описание бага из формы (единственное поле Owner).

    Returns:
        Словарь ``BugBotAnswer`` (status FINDINGS + одна запись).

    Raises:
        ValueError: пустой текст.
    """
    text = bug_text.strip()
    if not text:
        raise ValueError("bug is required")
    name = _derive_bug_title(text)
    return {
        "status": "FINDINGS",
        "findings": [
            {
                "severity": "medium",
                "location": name,
                "finding": text[:8000],
            }
        ],
        "block_reason": None,
    }


def _slug_change_id(raw: str) -> str:
    text = raw.strip().lower()
    text = re.sub(r"[^a-z0-9._+-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or _DEFAULT_CHANGE_ID


async def run_owner_named_bug_repair(
    *,
    repository_path: Path,
    bug_text: str,
    change_id: str | None = None,
    timeout_seconds: int = 2700,
    on_log_line: Callable[[str], None] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    bridge_runtime: Any | None = None,
) -> dict[str, Any]:
    """Запустить live bug-team repair для текста Owner (skip discovery).

    Заголовок finding и ``change_id`` каталога run выводятся из текста бага
    и стабильного дефолта ``owner-named`` — Owner их не вводит.

    Args:
        repository_path: Корень репозитория (``run_opsx_main.py`` / ``opsx``).
        bug_text: Описание бага из формы.
        timeout_seconds: Таймаут adapter hop (секунды).
        on_log_line: Если задан — каждая строка stdout/stderr hop (live-log агентов,
            logging, traceback) дублируется в callback.
        on_progress: Семантические события repair (stage/log) для UI SSE.
        bridge_runtime: Shared ``CursorBridgeRuntime`` (sqlite); без него — ephemeral
            jsonl bridge (не использовать в UI).

    Returns:
        Patch из ``make_bug_team_repair_hop`` (status, mission_id, error, …).
    """
    from bootstrap import prepare_named_bug_repair_runtime

    repo = resolve_opsx_repository_root(repository_path)
    prepare_named_bug_repair_runtime(repo_root=repo)
    from bug_log.context import bind_current_journal, reset_current_journal
    from bug_log.file_journal import open_run_journal
    from bug_log.mint import register_text_finding
    from bug_log.projector import BugJournalProjector
    from workflows.repair.repair_adapter_port import RepairAdapter

    resolved_change = _slug_change_id(change_id or _DEFAULT_CHANGE_ID)
    change_scope = "CHANGE" if change_id and change_id.strip() else "STANDALONE"
    review = build_bugbot_review_from_owner_text(bug_text=bug_text)
    journal = open_run_journal(
        repo_root=repo,
        run_id=f"owner-named-{resolved_change}",
        change_id=resolved_change if change_scope == "CHANGE" else None,
        change_scope=change_scope,
        mission_id=f"owner-named-{resolved_change}",
    )
    token = bind_current_journal(journal)
    try:
        await register_text_finding(
            journal,
            lane="owner_report",
            local_id="owner-1",
            location=_derive_bug_title(bug_text),
            text=bug_text,
            alias_scheme="owner",
        )
        state: dict[str, Any] = {
            "repository_path": str(repo),
            "workspace": str(repo),
            "change_id": resolved_change,
            "review": review,
        }
        adapter_kwargs: dict[str, Any] = {"timeout_seconds": timeout_seconds}
        if bridge_runtime is not None:
            adapter_kwargs["bridge_runtime"] = bridge_runtime
        adapter = RepairAdapter(**adapter_kwargs)
        hop = make_bug_team_repair_hop(
            adapter,
            repair_run_segment="project-bug-repair",
            mission_prefix="owner-named",
            state_key_prefix="owner_named",
        )
        start_msg = (
            f"named_bug_repair: repair hop starting "
            f"(change_id={resolved_change}, segment=project-bug-repair)\n"
        )
        emit_console_line(start_msg.strip())
        if on_log_line is not None:
            on_log_line(start_msg)
        if on_progress is not None:
            on_progress(
                {
                    "event": "stage",
                    "stage": "INTAKE",
                    "state": "running",
                    "detail": "Принимаем описание бага",
                }
            )

        def _progress_bridge(payload: dict[str, Any]) -> None:
            if on_progress is not None:
                on_progress(payload)

        with stream_process_output(on_log_line):
            with observe_repair_progress(_progress_bridge):
                result = await hop(state)
        status = result.get("owner_named_check_repair_status", "?")
        err = (
            result.get("owner_named_check_repair_error")
            or result.get("error")
            or result.get("owner_named_error")
        )
        if status == "COMPLETE_NO_REPAIR" and not err:
            err = (
                "OPSX_STAGE_STOP code=REPAIR_QUEUE_ALREADY_SEEN stage=finish owner=verify\n"
                "evidence=Все findings из durable latest_state уже в seen без FIX; "
                "очередь не передана в classifier (reload repair_task_id).\n"
                "next_action=Повтор POST /repair должен вызвать owner_retry (см. stderr при старте); "
                "или смените заголовок бага / change_id для нового repair_task_id.\n"
                "COMPLETE_NO_REPAIR"
            )
        finish = f"named_bug_repair: repair hop finished: status={status}"
        if err:
            finish = f"{finish}\n{err}"
        emit_console_line(finish)
        if on_log_line is not None:
            on_log_line(f"\n--- {finish.strip()} ---\n")
        summary = result.get("owner_named_check_repair_summary")
        if on_progress is not None:
            payload: dict[str, Any] = {
                "status": status,
                "error": err,
            }
            if isinstance(summary, dict):
                payload.update(summary)
            on_progress({"event": "stage", "stage": "INTAKE", "state": "completed", "detail": status})
            on_progress({"event": "result", **payload})
        return result
    finally:
        BugJournalProjector(repo).rebuild()
        reset_current_journal(token)
