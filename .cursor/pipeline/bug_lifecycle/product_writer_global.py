"""Cross-mission product writer isolation (MAX_PRODUCT_WRITERS=1), atomic acquire."""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

LOCK_PATH = Path("opsx/runs/orch-missions/GLOBAL_PRODUCT_WRITER.lock")
GUARD_PATH = Path("opsx/runs/orch-missions/GLOBAL_PRODUCT_WRITER.guard")
RECOVERY_PATH = Path("opsx/runs/orch-missions/GLOBAL_PRODUCT_WRITER.recovery.json")
MAX_RECLAIM_ATTEMPTS = 8
GUARD_LOCK_TIMEOUT_S = 30.0


class ProductWriterError(Exception):
    def __init__(self, reason: str, **extra: Any) -> None:
        self.reason = reason
        self.extra = extra
        super().__init__(reason)


def _lock_file(repo_root: Path) -> Path:
    return repo_root / LOCK_PATH


def _guard_file(repo_root: Path) -> Path:
    return repo_root / GUARD_PATH


def _recovery_file(repo_root: Path) -> Path:
    return repo_root / RECOVERY_PATH


def read_lock(repo_root: Path) -> dict[str, Any] | None:
    path = _lock_file(repo_root)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _holder_snapshot(holder: dict[str, Any]) -> dict[str, str]:
    return {
        "mission_id": str(holder.get("mission_id") or ""),
        "execution_id": str(holder.get("execution_id") or ""),
        "finding_id": str(holder.get("finding_id") or ""),
        "status": str(holder.get("status") or ""),
        "head": str(holder.get("head") or ""),
        "worker_liveness": str(holder.get("worker_liveness") or ""),
        "pid": str(holder.get("pid") or ""),
        "acquired_at": str(holder.get("acquired_at") or ""),
    }


def _holder_matches(current: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    if current is None:
        return False
    return _holder_snapshot(current) == _holder_snapshot(expected)


def _terminal_confirmed(
    repo_root: Path,
    mission_id: str,
    execution_id: str,
    *,
    expected_head: str | None = None,
) -> bool:
    from .bug_team_transport import harvest_terminal_marker_path

    marker = harvest_terminal_marker_path(repo_root, mission_id, execution_id)
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if str(payload.get("execution_terminal") or "") != "CONFIRMED":
        return False
    if str(payload.get("mission_id") or "") != mission_id:
        return False
    if str(payload.get("execution_id") or "") != execution_id:
        return False
    if expected_head is not None and expected_head != "":
        if str(payload.get("head") or "") != expected_head:
            return False
    return True


def _lock_reclaimable(repo_root: Path, existing: dict[str, Any]) -> bool:
    if str(existing.get("worker_liveness") or "CONFIRMED") != "CONFIRMED":
        return False
    mission = str(existing.get("mission_id") or "")
    execution = str(existing.get("execution_id") or "")
    if not mission or not execution:
        return False
    expected_head = str(existing.get("head") or "") or None
    if str(existing.get("status") or "") != "ACTIVE":
        return _terminal_confirmed(
            repo_root,
            mission,
            execution,
            expected_head=expected_head,
        )
    return _terminal_confirmed(
        repo_root,
        mission,
        execution,
        expected_head=expected_head,
    )


def _atomic_create_lock(path: Path, payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _write_recovery_contract(repo_root: Path, body: dict[str, Any]) -> None:
    path = _recovery_file(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "opsx-product-writer-recovery-v1",
        "recorded_at": time.time(),
        **body,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _acquire_os_guard(fd: int, *, block: bool) -> None:
    if sys.platform == "win32":
        import msvcrt

        mode = msvcrt.LK_LOCK if block else msvcrt.LK_NBLCK
        msvcrt.locking(fd, mode, 1)
        return
    import fcntl

    flags = fcntl.LOCK_EX
    if not block:
        flags |= fcntl.LOCK_NB
    fcntl.flock(fd, flags)


def _release_os_guard(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _exclusive_transition(repo_root: Path, *, block: bool = True) -> Iterator[None]:
    guard = _guard_file(repo_root)
    guard.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(guard), os.O_CREAT | os.O_RDWR)
    deadline = time.monotonic() + GUARD_LOCK_TIMEOUT_S
    acquired = False
    try:
        while True:
            try:
                _acquire_os_guard(fd, block=not acquired and block)
                acquired = True
                break
            except (OSError, BlockingIOError):
                if not block or time.monotonic() >= deadline:
                    raise ProductWriterError("LOCK_TRANSITION_CONTENTION") from None
                time.sleep(0.01)
        yield
    finally:
        if acquired:
            try:
                _release_os_guard(fd)
            except OSError:
                pass
        os.close(fd)


def acquire(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    finding_id: str,
    head: str | None = None,
    worker_liveness: str = "CONFIRMED",
) -> None:
    if worker_liveness != "CONFIRMED":
        raise ProductWriterError("WORKER_LIVENESS_UNCONFIRMED_NO_LOCK_ACQUIRE")
    path = _lock_file(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mission_id": mission_id,
        "execution_id": execution_id,
        "finding_id": finding_id,
        "status": "ACTIVE",
        "worker_liveness": worker_liveness,
        "pid": os.getpid(),
        "acquired_at": time.time(),
    }
    if head:
        payload["head"] = head
    for attempt in range(MAX_RECLAIM_ATTEMPTS):
        try:
            with _exclusive_transition(repo_root):
                try:
                    _atomic_create_lock(path, payload)
                    return
                except FileExistsError:
                    existing = read_lock(repo_root)
                    if existing is None:
                        if attempt + 1 >= MAX_RECLAIM_ATTEMPTS:
                            raise ProductWriterError("LOCK_RACE_RETRY")
                        continue
                    if (
                        existing.get("mission_id") == mission_id
                        and existing.get("execution_id") == execution_id
                    ):
                        if existing.get("status") == "ACTIVE" and existing.get("finding_id") != finding_id:
                            raise ProductWriterError("DUPLICATE_PRODUCT_WRITER")
                        return
                    if _lock_reclaimable(repo_root, existing):
                        if not _holder_matches(read_lock(repo_root), existing):
                            raise ProductWriterError("LOCK_OWNERSHIP_AMBIGUOUS")
                        if path.is_file():
                            path.unlink()
                        if read_lock(repo_root) is not None:
                            raise ProductWriterError("LOCK_OWNERSHIP_AMBIGUOUS")
                        _atomic_create_lock(path, payload)
                        return
                    raise ProductWriterError(
                        "CROSS_MISSION_PRODUCT_WRITER",
                        holder_mission=existing.get("mission_id"),
                    )
        except ProductWriterError as error:
            if error.reason == "LOCK_OWNERSHIP_AMBIGUOUS":
                if attempt + 1 >= MAX_RECLAIM_ATTEMPTS:
                    raise ProductWriterError("LOCK_RACE_RETRY") from error
                continue
            raise
        except FileExistsError:
            if attempt + 1 >= MAX_RECLAIM_ATTEMPTS:
                raise ProductWriterError("LOCK_RACE_RETRY")
    raise ProductWriterError("LOCK_RACE_RETRY")


def release_on_terminal(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    execution_terminal: str,
    head: str | None = None,
) -> None:
    if execution_terminal != "CONFIRMED":
        raise ProductWriterError("WORKER_LIVENESS_UNCONFIRMED_NO_LOCK_RELEASE")
    if not _terminal_confirmed(
        repo_root,
        mission_id,
        execution_id,
        expected_head=head,
    ):
        raise ProductWriterError("TERMINAL_MARKER_MISSING")
    with _exclusive_transition(repo_root):
        existing = read_lock(repo_root)
        if existing is None:
            return
        expected_snapshot = _holder_snapshot(existing)
        if existing.get("mission_id") != mission_id or existing.get("execution_id") != execution_id:
            raise ProductWriterError("LOCK_MISSION_MISMATCH")
        if head and str(existing.get("head") or "") and str(existing.get("head") or "") != head:
            raise ProductWriterError("LOCK_HEAD_MISMATCH")
        path = _lock_file(repo_root)
        if not path.is_file():
            return
        current = read_lock(repo_root)
        if not _holder_matches(current, existing):
            _write_recovery_contract(
                repo_root,
                {
                    "event": "RELEASE_AMBIGUOUS",
                    "reason": "LOCK_OWNERSHIP_AMBIGUOUS",
                    "expected_holder": expected_snapshot,
                    "observed_holder": _holder_snapshot(current or {}),
                    "recovery": "RETAIN_LOCK_FAIL_CLOSED",
                },
            )
            raise ProductWriterError("LOCK_OWNERSHIP_AMBIGUOUS")
        path.unlink()


def recover_lock_state(repo_root: Path) -> dict[str, Any]:
    """Deterministic recovery contract after failed or ambiguous ownership transition."""
    try:
        with _exclusive_transition(repo_root):
            lock = read_lock(repo_root)
            if lock is None:
                return {"STATUS": "NO_LOCK", "RECOVERY": "NONE"}
            mission = str(lock.get("mission_id") or "")
            execution = str(lock.get("execution_id") or "")
            head = str(lock.get("head") or "") or None
            terminal_ok = _terminal_confirmed(
                repo_root,
                mission,
                execution,
                expected_head=head,
            )
            body = {
                "STATUS": "LOCK_RETAINED",
                "holder": _holder_snapshot(lock),
                "terminal_confirmed": terminal_ok,
                "RECOVERY": "RELEASE_ON_TERMINAL_OR_OWNER_REVIEW",
            }
            _write_recovery_contract(repo_root, {"event": "RECOVERY_PROBE", **body})
            return body
    except ProductWriterError as error:
        out = {
            "STATUS": "FAIL_CLOSED",
            "REASON": error.reason,
            "RECOVERY": "RETRY_AFTER_TRANSITION_CONTENTION",
        }
        _write_recovery_contract(repo_root, {"event": "RECOVERY_PROBE", **out})
        return out
