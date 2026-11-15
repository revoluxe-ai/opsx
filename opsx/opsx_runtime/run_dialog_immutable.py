"""Append-only запись legacy run_dialogs без перезаписи канонических файлов."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class RunDialogImmutableViolation(OSError):
    """Попытка изменить уже зафиксированный диалог без revision snapshot."""


_INDEX_SCHEMA = "opsx.run_dialog_revisions.v1"
_LOCK_RETRIES = 100
_LOCK_SLEEP_S = 0.005


def _digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _sanitize_revision(label: str) -> str:
    text = (label or "revision").strip()
    text = re.sub(r"[^a-zA-Z0-9._+-]+", "-", text)
    return text[:120] or "revision"


@contextmanager
def _revision_index_lock(agent_dir: Path):
    """Эксклюзивная блокировка index для детерминированной concurrent-записи."""
    agent_dir.mkdir(parents=True, exist_ok=True)
    lock_dir = agent_dir / ".revision_index.lock"
    acquired = False
    for _ in range(_LOCK_RETRIES):
        try:
            lock_dir.mkdir(exist_ok=False)
            acquired = True
            break
        except FileExistsError:
            time.sleep(_LOCK_SLEEP_S)
    if not acquired:
        raise RunDialogImmutableViolation("RUN_DIALOG_REVISION_INDEX_LOCK_TIMEOUT")
    try:
        yield
    finally:
        if acquired and lock_dir.exists():
            lock_dir.rmdir()


def _load_revision_index(agent_dir: Path) -> dict[str, object]:
    index_path = agent_dir / "revision_index.json"
    if not index_path.is_file():
        return {"schema": _INDEX_SCHEMA, "revisions": []}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunDialogImmutableViolation("RUN_DIALOG_REVISION_INDEX_CORRUPT") from exc
    if payload.get("schema") != _INDEX_SCHEMA:
        raise RunDialogImmutableViolation("RUN_DIALOG_REVISION_INDEX_CORRUPT")
    revisions = payload.get("revisions")
    if not isinstance(revisions, list):
        raise RunDialogImmutableViolation("RUN_DIALOG_REVISION_INDEX_CORRUPT")
    return payload


def _atomic_write_revision_index(agent_dir: Path, payload: dict[str, object]) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    index_path = agent_dir / "revision_index.json"
    staging = agent_dir / f".revision_index.staging-{uuid.uuid4().hex}"
    blob = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    staging.write_bytes(blob)
    os.replace(staging, index_path)


def _revision_entry_for(
    revisions: list[object],
    revision_id: str,
    filename: str,
) -> dict[str, object] | None:
    for item in revisions:
        if not isinstance(item, dict):
            raise RunDialogImmutableViolation("RUN_DIALOG_REVISION_INDEX_CORRUPT")
        if item.get("revision_id") == revision_id and item.get("filename") == filename:
            return item
    return None


def _record_revision_snapshot(
    agent_dir: Path,
    *,
    revision_id: str,
    filename: str,
    blob: bytes,
) -> str:
    """Зафиксировать revision snapshot идемпотентно по (revision_id, filename, sha256)."""
    digest = _digest(blob)
    with _revision_index_lock(agent_dir):
        payload = _load_revision_index(agent_dir)
        revisions = payload["revisions"]
        assert isinstance(revisions, list)

        existing = _revision_entry_for(revisions, revision_id, filename)
        if existing is not None:
            recorded = str(existing.get("sha256") or "")
            if recorded == digest:
                return digest
            raise RunDialogImmutableViolation(f"RUN_DIALOG_REVISION_CONFLICT:{filename}")

        revision_dir = agent_dir / "revisions" / revision_id
        snapshot = revision_dir / filename
        if snapshot.is_file():
            if snapshot.read_bytes() != blob:
                raise RunDialogImmutableViolation(f"RUN_DIALOG_REVISION_CONFLICT:{filename}")
        else:
            revision_dir.mkdir(parents=True, exist_ok=True)
            try:
                with snapshot.open("xb") as handle:
                    handle.write(blob)
            except FileExistsError:
                if snapshot.read_bytes() != blob:
                    raise RunDialogImmutableViolation(f"RUN_DIALOG_REVISION_CONFLICT:{filename}")

        revisions.append(
            {
                "revision_id": revision_id,
                "filename": filename,
                "sha256": digest,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _atomic_write_revision_index(agent_dir, payload)
    return digest


def write_immutable_bytes(path: Path, blob: bytes, *, revision_id: str) -> str:
    """Записать файл один раз; при изменении — snapshot в ``revisions/<id>/``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_bytes(blob)
        return _digest(blob)
    existing = path.read_bytes()
    if existing == blob:
        return _digest(blob)
    rev_id = _sanitize_revision(revision_id)
    return _record_revision_snapshot(
        path.parent,
        revision_id=rev_id,
        filename=path.name,
        blob=blob,
    )


def write_immutable_text(path: Path, text: str, *, revision_id: str) -> str:
    return write_immutable_bytes(path, text.encode("utf-8"), revision_id=revision_id)
