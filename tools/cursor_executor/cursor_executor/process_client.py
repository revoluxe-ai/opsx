"""Hard process boundary around CursorExecutor for production orchestration."""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .auth import resolve_auth
from .bridge_local import apply_windows_agent_shell_env
from .ledger import LedgerRecord, LedgerStore
from .schemas import (
    AdapterTerminalState,
    BootstrapLocalAgentResult,
    CursorExecutionRequest,
    CursorExecutionResult,
    ExecutionError,
    LifecyclePhase,
    ModelRoute,
    ReconciliationState,
    RuntimeKind,
    WritePolicy,
    canonical_request_digest,
    prompt_digest_sha256,
    utc_now_iso,
)
from .sdk_client import write_policy_enforcement_label


class ProcessHardTimeout(TimeoutError):
    pass


@dataclass
class BootstrapLocalAgentResult:
    """Create-only session bootstrap (donor ``schemas.py`` returns ``str`` agent_id only)."""

    agent_id: str
    timings_ms: dict[str, int]

    def to_json(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "timings_ms": dict(self.timings_ms)}

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> BootstrapLocalAgentResult:
        raw = value.get("timings_ms") or {}
        timings = {str(k): int(v) for k, v in raw.items()}
        return cls(agent_id=str(value["agent_id"]), timings_ms=timings)


@dataclass
class _RegisteredProcess:
    """In-flight process_worker registration for cancel_execution."""

    proc: subprocess.Popen[str]
    pid: int
    monotonic_start: float
    execution_id: str
    correlation_id: str | None


_PROCESS_REGISTRY: dict[str, _RegisteredProcess] = {}
_PROCESS_REGISTRY_LOCK = threading.Lock()


class CursorExecutorProcessClient:
    def __init__(
        self,
        *,
        ledger_root: Path | str | None = None,
        python_executable: str | None = None,
        default_timeout_seconds: float = 900.0,
        timeout_grace_seconds: float = 20.0,
        cleanup_budget_seconds: float = 15.0,
    ) -> None:
        self.ledger = LedgerStore(root=Path(ledger_root) if ledger_root else None)
        self.python_executable = python_executable or sys.executable
        self.default_timeout_seconds = default_timeout_seconds
        self.timeout_grace_seconds = timeout_grace_seconds
        self.process_root = resource_path(self.ledger.root, ".process")
        self.process_root.mkdir(parents=True, exist_ok=True)
        self.cleanup_budget_seconds = cleanup_budget_seconds

    @staticmethod
    def _register_process(
        execution_id: str,
        proc: subprocess.Popen[str],
        *,
        correlation_id: str | None,
    ) -> None:
        with _PROCESS_REGISTRY_LOCK:
            _PROCESS_REGISTRY[execution_id] = _RegisteredProcess(
                proc=proc,
                pid=proc.pid or 0,
                monotonic_start=time.monotonic(),
                execution_id=execution_id,
                correlation_id=correlation_id,
            )

    @staticmethod
    def _unregister_process(execution_id: str) -> None:
        with _PROCESS_REGISTRY_LOCK:
            _PROCESS_REGISTRY.pop(execution_id, None)

    def cancel_execution(
        self,
        execution_id: str,
        reason: str = "",
        *,
        budget_seconds: float | None = None,
    ) -> bool:
        """Idempotent kill+wait for a registered process_worker (bounded budget).

        Returns True when poll() is not None after cleanup, False if still running
        after budget (orphan — operator must reconcile).
        """

        budget = float(budget_seconds if budget_seconds is not None else self.cleanup_budget_seconds)
        with _PROCESS_REGISTRY_LOCK:
            entry = _PROCESS_REGISTRY.get(execution_id)
        if entry is None:
            return True
        proc = entry.proc
        if proc.poll() is not None:
            self._unregister_process(execution_id)
            return True
        self._kill_tree(proc)
        deadline = time.monotonic() + max(0.5, budget)
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self._unregister_process(execution_id)
                return True
            time.sleep(0.05)
        if proc.poll() is not None:
            self._unregister_process(execution_id)
            return True
        return False

    def cancel_bootstrap_executions(
        self,
        correlation_id: str,
        *,
        suffix: str = "agent-bootstrap",
    ) -> list[str]:
        """Cancel registered bootstrap attempts for a workflow correlation id."""

        cancelled: list[str] = []
        with _PROCESS_REGISTRY_LOCK:
            targets = [
                eid
                for eid, reg in _PROCESS_REGISTRY.items()
                if reg.correlation_id == correlation_id and eid.endswith(suffix)
            ]
        for eid in targets:
            if self.cancel_execution(eid, reason="bootstrap_retry_reconcile"):
                cancelled.append(eid)
        return cancelled

    def _deadline(self, request: CursorExecutionRequest | None, fallback: float) -> float:
        requested = request.timeout_seconds if request is not None else None
        base = float(requested) if requested is not None else fallback
        return max(5.0, base + self.timeout_grace_seconds)
    @staticmethod
    def _kill_tree(proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except Exception:
                pass
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    def _run_worker(self, envelope: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        token = uuid.uuid4().hex
        envelope_path = self.process_root / f"{token}.request.json"
        response_path = self.process_root / f"{token}.response.json"
        LedgerStore._atomic_write_json(envelope_path, envelope)
        apply_windows_agent_shell_env()
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        popen_kwargs: dict[str, Any] = {
            "cwd": str(Path(__file__).resolve().parent.parent),
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = flags
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            [
                self.python_executable,
                "-m",
                "cursor_executor.process_worker",
                "--envelope",
                str(envelope_path),
                "--response",
                str(response_path),
            ],
            **popen_kwargs,
        )
        request_payload = envelope.get("request") or {}
        execution_id = str(request_payload.get("execution_id") or "")
        correlation_id = request_payload.get("correlation_id")
        if execution_id:
            self._register_process(
                execution_id,
                proc,
                correlation_id=str(correlation_id) if correlation_id else None,
            )
        started = time.monotonic()
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self._kill_tree(proc)
            raise ProcessHardTimeout(
                f"executor process exceeded {timeout:.1f}s hard deadline"
            ) from exc
        finally:
            if execution_id:
                self._unregister_process(execution_id)
            try:
                envelope_path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            if not response_path.is_file():
                raise RuntimeError(
                    f"executor worker produced no response; rc={proc.returncode}; "
                    f"stdout={stdout[-1000:]!r}; stderr={stderr[-1000:]!r}"
                )
            payload = json.loads(response_path.read_text(encoding="utf-8"))
            return payload
        finally:
            try:
                response_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _request_from_record(self, execution_id: str) -> CursorExecutionRequest:
        record = self.ledger.load_record(execution_id)
        if record is None:
            return CursorExecutionRequest(
                execution_id=execution_id,
                workspace=".",
                runtime=RuntimeKind.LOCAL,
                model_route=ModelRoute(model_id="", params={}),
                prompt="",
                write_policy=WritePolicy.READ_ONLY,
            )
        return CursorExecutionRequest(
            execution_id=execution_id,
            workspace=record.workspace,
            runtime=RuntimeKind.LOCAL,
            model_route=ModelRoute.from_json(record.requested_route),
            prompt="",
            write_policy=WritePolicy.READ_ONLY,
        )
    def _unknown_result(
        self,
        request: CursorExecutionRequest,
        *,
        class_name: str,
        message: str,
        duration_ms: int | None = None,
    ) -> CursorExecutionResult:
        now = utc_now_iso()
        auth = resolve_auth()
        record = self.ledger.load_record(request.execution_id)
        if record is None:
            record = LedgerRecord(
                execution_id=request.execution_id,
                request_digest=canonical_request_digest(request),
                workspace=request.workspace,
                lifecycle_phase=LifecyclePhase.RUNNING.value,
                adapter_terminal=AdapterTerminalState.UNKNOWN.value,
                agent_id=None,
                run_id=None,
                requested_route=request.model_route.to_json(),
                validated_route=None,
                observed_route=None,
                created_at=now,
                updated_at=now,
            )
        validated = (
            ModelRoute.from_json(record.validated_route)
            if record.validated_route
            else None
        )
        observed = (
            ModelRoute.from_json(record.observed_route)
            if record.observed_route
            else None
        )
        result = CursorExecutionResult(
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            workspace=request.workspace,
            runtime=request.runtime,
            requested_model_route=request.model_route,
            validated_model_route=validated,
            observed_model_route=observed,
            model_match=None,
            agent_id=record.agent_id,
            run_id=record.run_id,
            sdk_status=None,
            adapter_state=AdapterTerminalState.UNKNOWN,
            lifecycle_phase=LifecyclePhase.RUNNING,
            terminal_class=AdapterTerminalState.UNKNOWN,
            prompt_digest_sha256=prompt_digest_sha256(request.prompt),
            write_policy=request.write_policy,
            write_policy_enforcement=write_policy_enforcement_label(request.write_policy),
            auth_source=auth.source,
            process_env_key_present=auth.process_env_key_present,
            user_env_key_present=auth.user_env_key_present,
            created_at=record.created_at or now,
            started_at=None,
            finished_at=now,
            duration_ms=duration_ms,
            reconciliation_state=ReconciliationState.INDETERMINATE,
            result_text=None,
            error=ExecutionError(class_name, message, "process_boundary"),
        )
        evidence_path = self.ledger.save_terminal_evidence(result)
        record.lifecycle_phase = LifecyclePhase.RUNNING.value
        record.adapter_terminal = AdapterTerminalState.UNKNOWN.value
        record.updated_at = now
        record.terminal_evidence_path = str(evidence_path)
        self.ledger.save_record(record)
        return result

    async def _invoke(
        self,
        envelope: dict[str, Any],
        *,
        request: CursorExecutionRequest,
        timeout: float,
    ) -> CursorExecutionResult:
        started = time.monotonic()
        try:
            payload = await asyncio.to_thread(self._run_worker, envelope, timeout=timeout)
        except ProcessHardTimeout as exc:
            return self._unknown_result(
                request,
                class_name="PROCESS_HARD_TIMEOUT",
                message=str(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            return self._unknown_result(
                request,
                class_name="PROCESS_WORKER_FAILED",
                message=str(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        if payload.get("kind") != "result":
            error = payload.get("error") or {}
            return self._unknown_result(
                request,
                class_name=str(error.get("class") or "PROCESS_WORKER_FAILED"),
                message=str(error.get("message") or "worker returned error"),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        return CursorExecutionResult.from_json(payload["result"])

    async def execute(self, request: CursorExecutionRequest) -> CursorExecutionResult:
        return await self._invoke(
            {
                "operation": "execute",
                "request": request.to_json(),
                "ledger_root": str(self.ledger.root),
            },
            request=request,
            timeout=self._deadline(request, self.default_timeout_seconds),
        )
    async def execute_on_existing_agent(
        self,
        request: CursorExecutionRequest,
        *,
        agent_id: str,
    ) -> CursorExecutionResult:
        return await self._invoke(
            {
                "operation": "execute_existing",
                "request": request.to_json(),
                "agent_id": agent_id,
                "ledger_root": str(self.ledger.root),
            },
            request=request,
            timeout=self._deadline(request, self.default_timeout_seconds),
        )

    async def reconcile(
        self,
        execution_id: str,
        *,
        request: CursorExecutionRequest | None = None,
    ) -> CursorExecutionResult:
        req = request or self._request_from_record(execution_id)
        return await self._invoke(
            {
                "operation": "reconcile",
                "execution_id": execution_id,
                "request": request.to_json() if request is not None else None,
                "ledger_root": str(self.ledger.root),
            },
            request=req,
            timeout=self._deadline(request, 120.0),
        )

    async def bootstrap_local_agent(
        self,
        request: CursorExecutionRequest,
        *,
        execution_id: str,
    ) -> BootstrapLocalAgentResult:
        """Create-only bootstrap across the process boundary (no prompt send)."""

        return await self._invoke_bootstrap(
            {
                "operation": "bootstrap_local_agent",
                "request": request.to_json(),
                "execution_id": execution_id,
                "ledger_root": str(self.ledger.root),
            },
            timeout=self._deadline(request, self.default_timeout_seconds),
        )

    async def _invoke_bootstrap(
        self,
        envelope: dict[str, Any],
        *,
        timeout: float,
    ) -> BootstrapLocalAgentResult:
        try:
            payload = await asyncio.to_thread(self._run_worker, envelope, timeout=timeout)
        except ProcessHardTimeout as exc:
            raise RuntimeError(str(exc)) from exc
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
        if payload.get("kind") != "result" or "bootstrap" not in payload:
            error = payload.get("error") or {}
            raise RuntimeError(str(error.get("message") or "worker returned error"))
        return BootstrapLocalAgentResult.from_json(payload["bootstrap"])
