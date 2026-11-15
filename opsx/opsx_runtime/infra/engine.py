"""Решение ADOPT / START / START_ALT_PORT / UNAVAILABLE и lease среды.

Чужой процесс на порту не убивается. Усыновлённая БД на down не останавливается.
Значения секретов в снимок не попадают.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class InfraEnvSnapshot:
    """Факты о среде без секретов."""

    profile_name: str
    docker_ok: bool
    compose_v2: bool
    port_open: bool
    db_comment: str | None
    expected_comment: str
    alembic_at_head: bool | None = None


@dataclass
class Lease:
    """Что процесс сам стартовал. Down трогает только started_by_us."""

    run_id: str
    decision: str
    started_by_us: bool = False
    stopped: bool = False
    notes: list[str] = field(default_factory=list)


def decide(snapshot: InfraEnvSnapshot) -> str:
    """Чистая политика подъёма. Порт с чужим comment — другой порт, не kill."""
    if snapshot.port_open and snapshot.db_comment == snapshot.expected_comment:
        return "ADOPT"
    if snapshot.port_open and snapshot.db_comment != snapshot.expected_comment:
        return "START_ALT_PORT"
    if snapshot.docker_ok and snapshot.compose_v2 and not snapshot.port_open:
        return "START"
    return "UNAVAILABLE"


def apply_decision(snapshot: InfraEnvSnapshot, *, run_id: str) -> Lease:
    """Зафиксировать lease. Реальный compose вызывает только вызывающий с портом процесса."""
    decision = decide(snapshot)
    lease = Lease(run_id=run_id, decision=decision)
    if decision == "START":
        lease.started_by_us = True
        lease.notes.append("compose up is owned by this run")
    elif decision == "ADOPT":
        lease.notes.append("adopted healthy disposable database; teardown will not stop it")
    elif decision == "START_ALT_PORT":
        lease.notes.append("port held by a foreign identity; process was not killed")
    else:
        lease.notes.append("environment unavailable")
    return lease


def release_lease(lease: Lease) -> Lease:
    """Остановить только то, что стартовали мы."""
    if lease.started_by_us and not lease.stopped:
        lease.stopped = True
        lease.notes.append("stopped containers started by this lease")
    elif not lease.started_by_us:
        lease.notes.append("adopted or foreign resources left running")
    return lease


def write_lease(path: Path, lease: Lease) -> None:
    """Записать lease.json без секретов."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": lease.run_id,
        "decision": lease.decision,
        "started_by_us": lease.started_by_us,
        "stopped": lease.stopped,
        "notes": list(lease.notes),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
