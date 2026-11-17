"""Durable repair_task_id, reload RepairState, repair_attempts journal (verify nested PBH)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflows.repair.control import claim_next, had_code_repair, materialize_review
from workflows.repair.schemas import FindingReport, RepairState
from workflows.stop_errors import OpsxStageStop
from workflows.verify.check_repair_outcome import compute_repair_task_id

_OWNER_RETRY_CAP = 5


def _meta_path(output_dir: Path) -> Path:
    return output_dir / "repair_task_meta.json"


def _latest_state_path(output_dir: Path) -> Path:
    return output_dir / "latest_state.json"


def _attempts_path(output_dir: Path) -> Path:
    return output_dir / "repair_attempts.jsonl"


def write_repair_task_meta(
    output_dir: Path,
    *,
    repair_task_id: str,
    harness_profile: str = "production_like",
) -> None:
    """Записать стабильный repair_task_id (не загружать state только по каталогу)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _meta_path(output_dir).write_text(
        json.dumps(
            {
                "repair_task_id": repair_task_id,
                "harness_profile": harness_profile,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _green_only_stage_error(error: str | None) -> bool:
    """True, когда единственные ошибки стадии — ``GREEN_STATUS_*``."""
    if not error:
        return False
    parts = [part.strip() for part in str(error).split(";") if part.strip()]
    return bool(parts) and all(part.startswith("GREEN_STATUS_") for part in parts)


def _is_fix_committed_green_blocked(state: RepairState) -> bool:
    """FIX+commit на wire, GREEN не подтверждён — не делать полный owner_retry reset."""
    if state.fix is None or state.fix.status != "FIXED":
        return False
    if not state.commit_sha and str(state.commit_status or "") in {"", "PENDING"}:
        return False
    if state.green is not None and state.green.status == "BLOCKED":
        return True
    return _green_only_stage_error(state.error)


def _load_repair_task_meta(output_dir: Path) -> dict[str, Any]:
    path = _meta_path(output_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _bump_owner_retry_count(output_dir: Path, *, repair_task_id: str) -> int:
    """Увеличить счётчик owner_retry в ``repair_task_meta.json``; вернуть новое значение."""
    output_dir.mkdir(parents=True, exist_ok=True)
    meta = _load_repair_task_meta(output_dir)
    meta["repair_task_id"] = repair_task_id
    count = int(meta.get("owner_retry_count") or 0) + 1
    meta["owner_retry_count"] = count
    _meta_path(output_dir).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return count


def persist_repair_attempt(
    output_dir: Path,
    *,
    repair_task_id: str,
    stage: str,
    outcome: dict[str, Any],
    product_at_red_digest: str | None = None,
) -> None:
    """Append-only журнал попыток repair в verify output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "repair_task_id": repair_task_id,
        "stage": stage,
        "product_at_red_digest": product_at_red_digest,
        **outcome,
    }
    with _attempts_path(output_dir).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _maybe_owner_retry_reset(
    loaded: RepairState,
    report: FindingReport,
    *,
    owner_go: bool,
) -> tuple[RepairState, str | None]:
    """Повтор Owner POST при том же ``repair_task_id``: не выходить в NO_REPAIR мгновенно.

    Если durable ``latest_state.json`` уже в terminal/finish, все пункты очереди в
    ``seen``, а продуктовый FIX не было — перематериализуем очередь из review и
    снимаем ``seen`` для повторной claim/classifier hop.
    """
    if not owner_go:
        return loaded, None
    if had_code_repair(loaded):
        return loaded, None
    next_finding, _ = claim_next(loaded)
    if next_finding is not None:
        return loaded, None

    terminal = str(loaded.status or "")
    at_finish = (
        loaded.current_stage == "finish"
        or terminal.startswith("COMPLETE_")
        or terminal in {"BLOCKED", "BLOCKED_INCONCLUSIVE", "QUEUE_EMPTY"}
    )
    if not at_finish:
        return loaded, None

    if _is_fix_committed_green_blocked(loaded):
        return loaded, None

    fresh_queue = materialize_review(report)
    if not fresh_queue:
        return loaded, None

    fixed_ids = {
        o.finding.id for o in loaded.finished if o.terminal_state == "FIXED"
    }
    retry_ids = {finding.id for finding in fresh_queue if finding.id not in fixed_ids}
    if not retry_ids:
        return loaded, None

    new_seen = [finding_id for finding_id in loaded.seen if finding_id not in retry_ids]
    repair_task_id = compute_repair_task_id(report)
    output_dir = Path(loaded.output_dir)
    prior_count = int(_load_repair_task_meta(output_dir).get("owner_retry_count") or 0)
    if prior_count >= _OWNER_RETRY_CAP:
        raise OpsxStageStop(
            "owner_retry cap exhausted",
            code="OWNER_RETRY_CAP",
            stage="finish",
            evidence=(
                f"repair_task_id={repair_task_id}; owner_retry_count={prior_count}; "
                f"terminal={terminal!r}"
            ),
            owner="verify",
            next_action=(
                "opsx stop; opsx start после META "
                "и повторить POST /repair change_id=visual"
            ),
        )
    _ = _bump_owner_retry_count(output_dir, repair_task_id=repair_task_id)
    reason = (
        f"owner_retry: сброшен terminal={terminal!r} "
        f"(все findings в seen, FIX не было); повтор hop для {sorted(retry_ids)}"
    )
    refreshed = loaded.model_copy(
        update={
            "queue": fresh_queue,
            "seen": new_seen,
            "active": None,
            "status": "CREATED",
            "current_stage": "materialize",
            "error": None,
            "source_review": report,
        },
    )
    return refreshed, reason


def save_latest_repair_state(state: RepairState) -> None:
    """Сохранить wire-state для reload при том же repair_task_id."""
    out = Path(state.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    _latest_state_path(out).write_text(
        state.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def try_reload_repair_state(
    output_dir: Path,
    report: FindingReport,
    *,
    harness_profile: str = "production_like",
    base: RepairState,
) -> RepairState:
    """Reload только если repair_task_id в meta совпадает; иначе fresh base."""
    repair_task_id = compute_repair_task_id(report, harness_profile=harness_profile)
    meta_file = _meta_path(output_dir)
    latest = _latest_state_path(output_dir)
    if not meta_file.is_file() or not latest.is_file():
        write_repair_task_meta(output_dir, repair_task_id=repair_task_id)
        return base

    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        write_repair_task_meta(output_dir, repair_task_id=repair_task_id)
        return base

    if str(meta.get("repair_task_id") or "") != repair_task_id:
        write_repair_task_meta(output_dir, repair_task_id=repair_task_id)
        return base

    try:
        wire = json.loads(latest.read_text(encoding="utf-8"))
        loaded = RepairState.model_validate(wire)
    except (OSError, json.JSONDecodeError, ValueError):
        return base

    # Merge current discovery report; keep runs/finished/ledger history
    merged = loaded.model_copy(
        update={
            "source_review": report,
            "owner_go": base.owner_go,
            "model": base.model,
            "repository_path": base.repository_path,
            "workspace": base.workspace,
        },
    )
    refreshed, notice = _maybe_owner_retry_reset(
        merged,
        report,
        owner_go=base.owner_go,
    )
    if notice:
        sys.stderr.write(f"named_bug_repair: {notice}\n")
        sys.stderr.flush()
    return refreshed
