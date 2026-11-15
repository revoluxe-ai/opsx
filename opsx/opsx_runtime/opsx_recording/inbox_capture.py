"""Атомарная запись каждого SDK-hop в ``opsx/tests/inbox/<agent>/<execution_id>/``.

Append-only: существующие каталоги и файлы не перезаписываются; коллизия ID → FAIL.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from agents.adapters.abc_adapter import AgentResult

_EXECUTION_ID = re.compile(r"^[a-z][a-z0-9._-]{7,127}$")
_SENSITIVE = (
    re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:ghp_|gho_|ghu_|ghs_|github_pat_)[A-Za-z0-9_]{12,}"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}"),
)


class CaptureCollisionError(OSError):
    """Каталог или файл записи уже существует — перезапись запрещена."""


@dataclass(frozen=True)
class OpsxCaptureSession:
    """Зарезервированный каталог одного SDK-вызова."""

    repo_root: Path
    agent_slug: str
    execution_id: str
    capture_dir: Path
    reserved_at: str
    parent_execution_id: str | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _repo_root(state: Mapping[str, Any]) -> Path:
    from workflows.repo_paths import resolve_opsx_repository_root

    for key in ("repository_path", "workspace"):
        raw = state.get(key)
        if raw and str(raw).strip():
            candidate = Path(str(raw))
            try:
                return resolve_opsx_repository_root(candidate)
            except ValueError:
                return candidate.resolve()
    return Path.cwd().resolve()


def inbox_root(repo_root: Path) -> Path:
    return resource_path(repo_root, "opsx", "runs", "recordings", "inbox")


def _scan_secrets(blob: bytes, *, label: str) -> None:
    for pattern in _SENSITIVE:
        if pattern.search(blob) is not None:
            raise CaptureCollisionError(f"OPSX_CAPTURE_POSSIBLE_SECRET:{label}")


def _write_exclusive(path: Path, blob: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(blob)
    return _digest(blob)


def _append_event(events_path: Path, event: dict[str, Any]) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


_TERMINAL_STATUSES = frozenset({"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"})
_SUCCESS_TERMINAL = frozenset({"COMPLETED", "PARTIAL"})
_FAILURE_TERMINAL = frozenset({"FAILED", "CANCELLED"})


def _planned_terminal_status(
    *,
    error: str | None,
    terminal_code: str | None,
    agent_result: AgentResult | None,
) -> tuple[str, str]:
    if terminal_code == "CANCELLED":
        return "CANCELLED", "CANCELLED"
    if error:
        code = terminal_code or "FAILED"
        status = "CANCELLED" if code == "CANCELLED" else "FAILED"
        return status, code
    status = "COMPLETED"
    if agent_result is not None:
        sdk_status = str(agent_result.status).upper()
        if sdk_status not in {"COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED"}:
            status = "PARTIAL"
    code = terminal_code or status
    return status, code


def _assert_terminal_receipt_compatible(
    existing: dict[str, Any],
    *,
    capture_dir: Path,
    error: str | None,
    terminal_code: str | None,
    agent_result: AgentResult | None,
) -> None:
    """Существующий terminal receipt принимается только при полном совпадении identity."""
    planned_status, planned_code = _planned_terminal_status(
        error=error,
        terminal_code=terminal_code,
        agent_result=agent_result,
    )
    existing_status = str(existing.get("status") or "")
    existing_code = str(existing.get("terminal_code") or existing_status)
    existing_error = existing.get("error")
    incoming_failure = error is not None or terminal_code == "CANCELLED"

    if not incoming_failure:
        if existing_status in _FAILURE_TERMINAL:
            raise CaptureCollisionError("OPSX_CAPTURE_TERMINAL_TRANSITION_FORBIDDEN")
        if existing_status not in _SUCCESS_TERMINAL:
            raise CaptureCollisionError("OPSX_CAPTURE_TERMINAL_STATUS_UNKNOWN")
    elif existing_status in _SUCCESS_TERMINAL:
        raise CaptureCollisionError("OPSX_CAPTURE_TERMINAL_TRANSITION_FORBIDDEN")

    if existing_status != planned_status:
        raise CaptureCollisionError("OPSX_CAPTURE_TERMINAL_STATUS_COLLISION")
    if existing_code != planned_code:
        raise CaptureCollisionError("OPSX_CAPTURE_TERMINAL_CODE_COLLISION")

    if incoming_failure:
        if existing_error != error:
            raise CaptureCollisionError("OPSX_CAPTURE_FINAL_ERROR_COLLISION")
        return

    if agent_result is None:
        return

    for key, new_val in (
        ("sdk_run_id", agent_result.run_id),
        ("sdk_status", agent_result.status),
    ):
        old_val = existing.get(key)
        if new_val is None:
            continue
        if old_val is not None and str(old_val) != str(new_val):
            raise CaptureCollisionError("OPSX_CAPTURE_FINAL_IDENTITY_COLLISION")

    assistant_text = agent_result.text or ""
    if not assistant_text:
        return
    assistant_path = capture_dir / "assistant.txt"
    if not assistant_path.is_file():
        raise CaptureCollisionError("OPSX_CAPTURE_TERMINAL_PAYLOAD_MISSING")
    if assistant_path.read_text(encoding="utf-8") != assistant_text:
        raise CaptureCollisionError("OPSX_CAPTURE_TERMINAL_PAYLOAD_MISMATCH")


def _normalize_capture_execution_slug(slug: str) -> str:
    """Собрать префикс execution_id по regex ``_EXECUTION_ID`` (первая буква [a-z]).

    Repair hop-имена вида ``001-implementation-executor-abcdef`` нормализуются в
    ``hop-001-implementation-executor-abcdef``; каталог inbox по ``agent_slug`` не меняется.
    """
    token = slug.strip().lower()
    if not token:
        return "hop-agent"
    if re.match(r"^[a-z]", token):
        return token
    return f"hop-{token.lstrip('-')}"


def begin_opsx_sdk_capture(
    state: Mapping[str, Any],
    *,
    agent_slug: str,
    stream_label: str,
    stream_log: str | Path | None,
    resume_agent_id: str | None = None,
    resume_run_id: str | None = None,
) -> OpsxCaptureSession | None:
    """Резервировать inbox **до** обращения к модели.

    Returns:
        ``None`` если запись отключена env или нет stream_log.
    """
    if os.environ.get("OPSX_SKIP_SDK_INBOX_CAPTURE", "").strip() == "1":
        return None
    if os.environ.get("OPSX_SKIP_CONVERSATION_PERSIST", "").strip() == "1":
        return None
    if stream_log is None or not str(stream_log).strip():
        return None

    slug = re.sub(r"[^a-z0-9._+-]+", "-", (agent_slug or "agent").strip().lower()).strip("-")
    if not slug:
        slug = "agent"

    repo_root = _repo_root(state)
    execution_slug = _normalize_capture_execution_slug(slug)
    execution_id = f"{execution_slug}-{uuid.uuid4().hex}"
    if not _EXECUTION_ID.fullmatch(execution_id):
        raise CaptureCollisionError("OPSX_CAPTURE_EXECUTION_ID_INVALID")

    agent_inbox = inbox_root(repo_root) / slug
    agent_inbox.mkdir(parents=True, exist_ok=True)
    capture_dir = agent_inbox / execution_id
    try:
        capture_dir.mkdir(mode=0o700, parents=False)
    except FileExistsError as exc:
        raise CaptureCollisionError("OPSX_CAPTURE_DIRECTORY_COLLISION") from exc

    parent_execution_id = str(state.get("opsx_capture_execution_id") or "").strip() or None
    meta = {
        "schema": "opsx.sdk_inbox.v1",
        "status": "RESERVED",
        "agent": slug,
        "execution_id": execution_id,
        "stream_label": stream_label,
        "stream_log": str(Path(stream_log).resolve()),
        "reserved_at": _utc_now_iso(),
        "change_id": state.get("change_id"),
        "run_uuid": state.get("run_uuid"),
        "model": state.get("model"),
        "resume_agent_id": resume_agent_id,
        "resume_run_id": resume_run_id,
        "parent_execution_id": parent_execution_id,
    }
    meta_blob = (json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    _write_exclusive(capture_dir / "capture_meta.json", meta_blob)
    return OpsxCaptureSession(
        repo_root=repo_root,
        agent_slug=slug,
        execution_id=execution_id,
        capture_dir=capture_dir,
        reserved_at=meta["reserved_at"],
        parent_execution_id=parent_execution_id,
    )


def complete_opsx_sdk_capture(
    session: OpsxCaptureSession | None,
    *,
    state: Mapping[str, Any],
    stream_log: str | Path | None,
    agent_result: AgentResult | None,
    run_dialog_agent_dir: Path | None,
    run_dialog_artifact_paths: Mapping[str, Path] | None = None,
    error: str | None = None,
    terminal_code: str | None = None,
) -> Path | None:
    """Завершить запись: копии sidecars, assistant bytes, events append-only."""
    if session is None:
        return None

    capture_dir = session.capture_dir
    final_meta_path = capture_dir / "capture_meta.final.json"
    # rebase synthesis: incoming terminal idempotency + dev assert (не skip при error:
    # иначе FAILED повтор ломается на FINAL_META_COLLISION из‑за finished_at)
    if final_meta_path.is_file():
        try:
            existing = json.loads(final_meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict) and existing.get("status") in _TERMINAL_STATUSES:
            _assert_terminal_receipt_compatible(
                existing,
                capture_dir=capture_dir,
                error=error,
                terminal_code=terminal_code,
                agent_result=agent_result,
            )
            return capture_dir

    events_path = capture_dir / "capture_events.ndjson"
    artifacts: dict[str, dict[str, Any]] = {}

    def _copy_sidecar(name: str, source: Path) -> None:
        if not source.is_file():
            return
        blob = source.read_bytes()
        _scan_secrets(blob, label=name)
        dest = capture_dir / name
        if dest.exists():
            if dest.read_bytes() != blob:
                raise CaptureCollisionError(f"OPSX_CAPTURE_IMMUTABLE_VIOLATION:{name}")
            artifacts[name] = {"sha256": _digest(blob), "bytes": len(blob), "source": str(source)}
            return
        digest = _write_exclusive(dest, blob)
        artifacts[name] = {"sha256": digest, "bytes": len(blob), "source": str(source)}

    try:
        sidecar_sources: dict[str, Path] = {}
        if run_dialog_artifact_paths:
            for fname in ("conversation.json", "conversation.md", "meta.json"):
                raw = run_dialog_artifact_paths.get(fname)
                if raw is not None:
                    sidecar_sources[fname] = Path(raw)
        elif run_dialog_agent_dir is not None and run_dialog_agent_dir.is_dir():
            for fname in ("conversation.json", "conversation.md", "meta.json"):
                sidecar_sources[fname] = run_dialog_agent_dir / fname
        for fname, source in sidecar_sources.items():
            _copy_sidecar(fname, source)

        if stream_log is not None:
            stream_path = Path(str(stream_log))
            if stream_path.is_file():
                _copy_sidecar("stream.ndjson", stream_path)
            conv_legacy = stream_path.with_suffix(".conversation.json")
            if conv_legacy.is_file():
                _copy_sidecar("legacy.conversation.json", conv_legacy)

        assistant_text = (agent_result.text if agent_result else "") or ""
        if assistant_text:
            assistant_blob = assistant_text.encode("utf-8")
            _scan_secrets(assistant_blob, label="assistant.txt")
            assistant_path = capture_dir / "assistant.txt"
            if assistant_path.exists():
                if assistant_path.read_bytes() != assistant_blob:
                    raise CaptureCollisionError("OPSX_CAPTURE_IMMUTABLE_VIOLATION:assistant.txt")
            else:
                digest = _write_exclusive(assistant_path, assistant_blob)
                artifacts["assistant.txt"] = {"sha256": digest, "bytes": len(assistant_blob)}

        status, resolved_terminal_code = _planned_terminal_status(
            error=error,
            terminal_code=terminal_code,
            agent_result=agent_result,
        )

        meta = json.loads((capture_dir / "capture_meta.json").read_text(encoding="utf-8"))
        meta.update(
            {
                "status": status,
                "finished_at": _utc_now_iso(),
                "sdk_run_id": agent_result.run_id if agent_result else None,
                "sdk_status": agent_result.status if agent_result else None,
                "artifacts": artifacts,
                "error": error,
                "terminal_code": resolved_terminal_code,
            }
        )
        updated = (
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        _append_event(
            events_path,
            {
                "kind": "capture.finalize",
                "execution_id": session.execution_id,
                "status": status,
            },
        )
        if final_meta_path.exists():
            if final_meta_path.read_bytes() != updated:
                raise CaptureCollisionError("OPSX_CAPTURE_FINAL_META_COLLISION")
        else:
            final_meta_path.write_bytes(updated)
    except (OSError, ValueError, CaptureCollisionError):
        raise

    return capture_dir


def abort_opsx_sdk_capture(session: OpsxCaptureSession | None, *, error: str) -> None:
    """Диагностическая запись при прерванном hop."""
    if session is None:
        return
    events_path = session.capture_dir / "capture_events.ndjson"
    _append_event(
        events_path,
        {"kind": "capture.abort", "execution_id": session.execution_id, "error": error},
    )


def find_stale_reserved_captures(repo_root: Path) -> list[Path]:
    """Каталоги inbox без terminal meta — не считаются успешной записью."""
    root = inbox_root(repo_root)
    if not root.is_dir():
        return []
    stale: list[Path] = []
    for agent_dir in root.iterdir():
        if not agent_dir.is_dir():
            continue
        for capture_dir in agent_dir.iterdir():
            if not capture_dir.is_dir():
                continue
            reserved = capture_dir / "capture_meta.json"
            final = capture_dir / "capture_meta.final.json"
            if not reserved.is_file() or final.is_file():
                continue
            try:
                meta = json.loads(reserved.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                stale.append(capture_dir)
                continue
            if meta.get("status") == "RESERVED":
                stale.append(capture_dir)
    return sorted(stale)
