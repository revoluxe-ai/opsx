"""Atomic local ledger for execution identity (no secrets)."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .schemas import (
    AdapterTerminalState,
    CursorExecutionResult,
    LifecyclePhase,
    ModelRoute,
)

_DEFAULT_REPLACE_MAX_ATTEMPTS = 10
_INITIAL_REPLACE_BACKOFF_SECONDS = 0.025


def _is_transient_replace_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror in (5, 32):
            return True
        if exc.errno in (13, 16, 32):
            return True
    return False


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON via tmp + os.replace; retry transient Windows replace denials."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        _ = handle.write(data)
        handle.flush()
        _ = os.fsync(handle.fileno())

    last_error: BaseException | None = None
    backoff = _INITIAL_REPLACE_BACKOFF_SECONDS
    for attempt in range(_DEFAULT_REPLACE_MAX_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except BaseException as exc:
            if not _is_transient_replace_error(exc) or attempt >= _DEFAULT_REPLACE_MAX_ATTEMPTS - 1:
                last_error = exc
                break
            last_error = exc
            time.sleep(backoff)
            backoff = min(backoff * 2, 0.5)

    if tmp.is_file():
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"atomic_write_json failed for {path}")


@dataclass
class LedgerRecord:
    execution_id: str
    request_digest: str
    workspace: str
    lifecycle_phase: str
    adapter_terminal: str | None
    agent_id: str | None
    run_id: str | None
    requested_route: dict[str, Any]
    validated_route: dict[str, Any] | None
    observed_route: dict[str, Any] | None
    created_at: str
    updated_at: str
    terminal_evidence_path: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "request_digest": self.request_digest,
            "workspace": self.workspace,
            "lifecycle_phase": self.lifecycle_phase,
            "adapter_terminal": self.adapter_terminal,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "requested_route": self.requested_route,
            "validated_route": self.validated_route,
            "observed_route": self.observed_route,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "terminal_evidence_path": self.terminal_evidence_path,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "LedgerRecord":
        return cls(
            execution_id=str(value["execution_id"]),
            request_digest=str(value["request_digest"]),
            workspace=str(value.get("workspace", ".")),
            lifecycle_phase=str(value.get("lifecycle_phase", LifecyclePhase.PREPARING.value)),
            adapter_terminal=value.get("adapter_terminal"),
            agent_id=value.get("agent_id"),
            run_id=value.get("run_id"),
            requested_route=dict(value.get("requested_route") or {}),
            validated_route=dict(value["validated_route"])
            if value.get("validated_route")
            else None,
            observed_route=dict(value["observed_route"]) if value.get("observed_route") else None,
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
            terminal_evidence_path=value.get("terminal_evidence_path"),
        )


class LedgerStore:
    def __init__(self, root: Path | None = None) -> None:
        base = root or Path(__file__).resolve().parent.parent / ".ledger"
        self.root = base
        self.root.mkdir(parents=True, exist_ok=True)

    def _execution_dir(self, execution_id: str) -> Path:
        safe = execution_id.replace("/", "_").replace("\\", "_")
        return resource_path(self.root, safe)

    def _record_path(self, execution_id: str) -> Path:
        return self._execution_dir(execution_id) / "record.json"

    def _evidence_path(self, execution_id: str) -> Path:
        return self._execution_dir(execution_id) / "evidence.json"

    @staticmethod
    def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
        atomic_write_json(path, payload)

    def load_record(self, execution_id: str) -> LedgerRecord | None:
        path = self._record_path(execution_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return LedgerRecord.from_json(raw)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def save_record(self, record: LedgerRecord) -> None:
        self._atomic_write_json(self._record_path(record.execution_id), record.to_json())

    def save_terminal_evidence(self, result: CursorExecutionResult) -> Path:
        path = self._evidence_path(result.execution_id)
        self._atomic_write_json(path, result.to_json())
        return path

    def load_terminal_evidence(self, execution_id: str) -> CursorExecutionResult | None:
        path = self._evidence_path(execution_id)
        if not path.is_file():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            if not raw.strip():
                return None
            parsed = json.loads(raw)
            result = CursorExecutionResult.from_json(parsed)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        if not is_terminal_evidence_valid(result):
            return None
        return result

    def is_corrupt_evidence_file(self, execution_id: str) -> bool:
        path = self._evidence_path(execution_id)
        if not path.is_file():
            return False
        try:
            raw = path.read_text(encoding="utf-8")
            if not raw.strip():
                return True
            parsed = json.loads(raw)
            result = CursorExecutionResult.from_json(parsed)
            return not is_terminal_evidence_valid(result)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return True


def is_terminal_evidence_valid(result: CursorExecutionResult) -> bool:
    terminal = result.terminal_class
    if terminal == AdapterTerminalState.SUCCEEDED:
        return (
            result.sdk_status == "finished"
            and result.model_match is True
            and bool(result.agent_id)
            and bool(result.run_id)
            and result.observed_model_route is not None
        )
    if terminal in {
        AdapterTerminalState.FAILED,
        AdapterTerminalState.CANCELLED,
        AdapterTerminalState.BLOCKED,
        AdapterTerminalState.UNKNOWN,
    }:
        return bool(result.execution_id) and bool(result.created_at)
    return False


def record_from_result(
    record: LedgerRecord,
    *,
    lifecycle_phase: LifecyclePhase,
    adapter_terminal: AdapterTerminalState | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    validated_route: ModelRoute | None = None,
    observed_route: ModelRoute | None = None,
    updated_at: str,
    terminal_evidence_path: str | None = None,
) -> LedgerRecord:
    if agent_id is not None:
        record.agent_id = agent_id
    if run_id is not None:
        record.run_id = run_id
    record.lifecycle_phase = lifecycle_phase.value
    if adapter_terminal is not None:
        record.adapter_terminal = adapter_terminal.value
    if validated_route is not None:
        record.validated_route = validated_route.to_json()
    if observed_route is not None:
        record.observed_route = observed_route.to_json()
    record.updated_at = updated_at
    if terminal_evidence_path is not None:
        record.terminal_evidence_path = terminal_evidence_path
    return record
