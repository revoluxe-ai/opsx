"""OPSX Cursor SDK executor adapter — execute + reconcile."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .auth import resolve_auth
from .ledger import LedgerRecord, LedgerStore, is_terminal_evidence_valid, record_from_result
from .preflight import PreflightFailure, validate_model_route
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
    canonical_request_digest,
    model_routes_equal,
    prompt_digest_sha256,
    utc_now_iso,
)


class BootstrapDigestConflictError(RuntimeError):
    """execution_id reused with a different bootstrap request digest."""
from .sdk_client import (
    AsyncCursorSdkClient,
    SdkClient,
    SdkRunOutcome,
    write_policy_enforcement_label,
)


class CursorExecutor:
    def __init__(
        self,
        *,
        sdk: SdkClient | None = None,
        ledger: LedgerStore | None = None,
    ) -> None:
        self.sdk = sdk or AsyncCursorSdkClient()
        self.ledger = ledger or LedgerStore()

    async def execute(self, request: CursorExecutionRequest) -> CursorExecutionResult:
        digest = canonical_request_digest(request)
        existing = self.ledger.load_record(request.execution_id)
        if existing is not None:
            if existing.request_digest != digest:
                return self._blocked_result(
                    request,
                    stage="idempotency",
                    class_name="EXECUTION_ID_CONFLICT",
                    message="execution_id reused with different request digest",
                    lifecycle=LifecyclePhase.PREPARING,
                )
            terminal = self.ledger.load_terminal_evidence(request.execution_id)
            if terminal is not None:
                terminal.reconciliation_state = ReconciliationState.TERMINAL_FROM_LEDGER
                return terminal
            return await self.reconcile(request.execution_id, request=request)

        created_at = utc_now_iso()
        record = LedgerRecord(
            execution_id=request.execution_id,
            request_digest=digest,
            workspace=request.workspace,
            lifecycle_phase=LifecyclePhase.PREPARING.value,
            adapter_terminal=None,
            agent_id=None,
            run_id=None,
            requested_route=request.model_route.to_json(),
            validated_route=None,
            observed_route=None,
            created_at=created_at,
            updated_at=created_at,
        )
        self.ledger.save_record(record)

        if request.runtime != RuntimeKind.LOCAL:
            return await self._finalize_blocked(
                request,
                record,
                stage="runtime",
                class_name="UNSUPPORTED_RUNTIME",
                message=f"runtime {request.runtime.value} not implemented in V0.1",
            )

        auth = resolve_auth()
        if not auth.api_key:
            return await self._finalize_blocked(
                request,
                record,
                stage="auth",
                class_name="SDK_AUTH_MISSING",
                message="CURSOR_API_KEY not available",
                auth=auth,
            )

        record = record_from_result(
            record,
            lifecycle_phase=LifecyclePhase.AUTH_RESOLVED,
            updated_at=utc_now_iso(),
        )
        self.ledger.save_record(record)

        try:
            catalog = await self.sdk.list_models(api_key=auth.api_key, workspace=request.workspace)
        except Exception as exc:
            return await self._finalize_unknown(
                request,
                record,
                stage="preflight",
                message=f"catalog fetch failed: {exc}",
                auth=auth,
            )

        preflight = validate_model_route(request.model_route, catalog)
        if isinstance(preflight, PreflightFailure):
            return await self._finalize_blocked(
                request,
                record,
                stage="preflight",
                class_name=preflight.code,
                message=preflight.message,
                auth=auth,
                details=preflight.details,
            )

        record = record_from_result(
            record,
            lifecycle_phase=LifecyclePhase.MODEL_VALIDATED,
            validated_route=preflight.validated_route,
            updated_at=utc_now_iso(),
        )
        self.ledger.save_record(record)

        try:
            created = await self.sdk.create_local_agent(
                api_key=auth.api_key,
                workspace=request.workspace,
                model_route=preflight.validated_route,
                write_policy=request.write_policy,
            )
        except Exception as exc:
            return await self._finalize_failed(
                request,
                record,
                stage="create",
                message=str(exc),
                auth=auth,
                validated_route=preflight.validated_route,
            )

        record = record_from_result(
            record,
            lifecycle_phase=LifecyclePhase.CREATED,
            agent_id=created.agent_id,
            updated_at=utc_now_iso(),
        )
        self.ledger.save_record(record)

        record = record_from_result(
            record,
            lifecycle_phase=LifecyclePhase.RUNNING,
            updated_at=utc_now_iso(),
        )
        self.ledger.save_record(record)

        started_at = utc_now_iso()
        try:
            outcome = await self.sdk.send_and_wait(
                api_key=auth.api_key,
                agent_id=created.agent_id,
                prompt=request.prompt,
                write_policy=request.write_policy,
                timeout_seconds=request.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return await self._finalize_unknown(
                request,
                record,
                stage="run",
                message="execution timed out",
                auth=auth,
                validated_route=preflight.validated_route,
                agent_id=created.agent_id,
                started_at=started_at,
            )
        except OSError as exc:
            return await self._finalize_unknown(
                request,
                record,
                stage="run",
                message=f"transport loss: {exc}",
                auth=auth,
                validated_route=preflight.validated_route,
                agent_id=created.agent_id,
                started_at=started_at,
            )
        except Exception as exc:
            return await self._finalize_unknown(
                request,
                record,
                stage="run",
                message=str(exc),
                auth=auth,
                validated_route=preflight.validated_route,
                agent_id=created.agent_id,
                started_at=started_at,
            )

        return await self._materialize_terminal(
            request,
            record,
            outcome=outcome,
            auth=auth,
            validated_route=preflight.validated_route,
            started_at=started_at,
            reconciliation_state=ReconciliationState.NOT_APPLICABLE,
        )

    async def reconcile(
        self,
        execution_id: str,
        *,
        request: CursorExecutionRequest | None = None,
    ) -> CursorExecutionResult:
        record = self.ledger.load_record(execution_id)
        if record is None:
            return self._blocked_result(
                request
                or CursorExecutionRequest(
                    execution_id=execution_id,
                    workspace=".",
                    runtime=RuntimeKind.LOCAL,
                    model_route=ModelRoute(model_id="", params={}),
                    prompt="",
                ),
                stage="reconcile",
                class_name="EXECUTION_NOT_FOUND",
                message="no ledger record for execution_id",
                lifecycle=LifecyclePhase.PREPARING,
            )

        if request is not None and record.request_digest != canonical_request_digest(request):
            return self._blocked_result(
                request,
                stage="idempotency",
                class_name="EXECUTION_ID_CONFLICT",
                message="execution_id reused with different request digest",
                lifecycle=LifecyclePhase.PREPARING,
            )

        terminal = self.ledger.load_terminal_evidence(execution_id)
        if terminal is not None:
            terminal.reconciliation_state = ReconciliationState.TERMINAL_FROM_LEDGER
            return terminal

        auth = resolve_auth()
        if not auth.api_key:
            return self._blocked_result(
                self._request_from_record(record),
                stage="auth",
                class_name="SDK_AUTH_MISSING",
                message="CURSOR_API_KEY not available for reconcile",
                lifecycle=LifecyclePhase.PREPARING,
            )

        if not record.run_id:
            return await self._finalize_unknown(
                self._request_from_record(record),
                record,
                stage="reconcile",
                message="run_id missing; cannot inspect SDK state",
                auth=auth,
            )

        req = request or self._request_from_record(record)
        outcome = await self.sdk.get_run_outcome(
            api_key=auth.api_key,
            workspace=req.workspace,
            run_id=record.run_id,
            agent_id=record.agent_id,
            timeout_seconds=req.timeout_seconds,
        )
        if outcome is None:
            result = await self._finalize_unknown(
                req,
                record,
                stage="reconcile",
                message="SDK run state could not be determined",
                auth=auth,
                agent_id=record.agent_id,
            )
            result.reconciliation_state = ReconciliationState.INDETERMINATE
            return result

        if str(outcome.sdk_status) not in {"finished", "error", "cancelled", "expired"}:
            result = await self._finalize_unknown(
                req,
                record,
                stage="reconcile",
                message=f"run still live: {outcome.sdk_status}",
                auth=auth,
                agent_id=record.agent_id,
                run_id=record.run_id,
            )
            result.reconciliation_state = ReconciliationState.LIVE_OBSERVED
            return result

        validated = (
            ModelRoute.from_json(record.validated_route)
            if record.validated_route
            else req.model_route
        )
        result = await self._materialize_terminal(
            req,
            record,
            outcome=outcome,
            auth=auth,
            validated_route=validated,
            started_at=record.created_at,
            reconciliation_state=ReconciliationState.TERMINAL_FROM_SDK,
        )
        return result

    def _bootstrap_request_digests(
        self, request: CursorExecutionRequest, *, execution_id: str
    ) -> tuple[CursorExecutionRequest, str, str]:
        """Canonical and legacy Temporal adapter digests for in-flight compatibility."""

        bootstrap_request = CursorExecutionRequest(
            execution_id=execution_id,
            correlation_id=request.correlation_id,
            workspace=request.workspace,
            runtime=request.runtime,
            model_route=request.model_route,
            prompt="__cursor_executor_bootstrap__",
            write_policy=request.write_policy,
            timeout_seconds=request.timeout_seconds,
            metadata=dict(request.metadata),
        )
        legacy_request = CursorExecutionRequest(
            execution_id=execution_id,
            correlation_id=request.correlation_id,
            workspace=request.workspace,
            runtime=request.runtime,
            model_route=request.model_route,
            prompt="__cursor_sdk_session_bootstrap__",
            write_policy=request.write_policy,
            timeout_seconds=request.timeout_seconds,
            metadata=dict(request.metadata),
        )
        return (
            bootstrap_request,
            canonical_request_digest(bootstrap_request),
            canonical_request_digest(legacy_request),
        )

    async def bootstrap_local_agent(
        self,
        request: CursorExecutionRequest,
        *,
        execution_id: str,
    ) -> BootstrapLocalAgentResult:
        """Create a local SDK agent without sending a user prompt (session bootstrap)."""
        started = time.monotonic()
        timings: dict[str, int] = {}
        bootstrap_request, digest, legacy_digest = self._bootstrap_request_digests(
            request, execution_id=execution_id
        )
        existing = self.ledger.load_record(execution_id)
        if existing is not None:
            if existing.agent_id:
                timings["total_bootstrap_ms"] = int((time.monotonic() - started) * 1000)
                return BootstrapLocalAgentResult(agent_id=existing.agent_id, timings_ms=timings)
            if existing.request_digest not in (digest, legacy_digest):
                raise BootstrapDigestConflictError(
                    "execution_id reused with different request digest"
                )
            terminal = self.ledger.load_idempotent_terminal_evidence(execution_id)
            if terminal is not None and terminal.agent_id:
                timings["total_bootstrap_ms"] = int((time.monotonic() - started) * 1000)
                return BootstrapLocalAgentResult(
                    agent_id=terminal.agent_id, timings_ms=timings
                )

        auth = resolve_auth()
        if not auth.api_key:
            raise RuntimeError("SDK_AUTH_MISSING")

        catalog_started = time.monotonic()
        catalog = await self.sdk.list_models(api_key=auth.api_key, workspace=request.workspace)
        timings["catalog_ms"] = int((time.monotonic() - catalog_started) * 1000)
        preflight_started = time.monotonic()
        preflight = validate_model_route(request.model_route, catalog)
        timings["preflight_ms"] = int((time.monotonic() - preflight_started) * 1000)
        if isinstance(preflight, PreflightFailure):
            raise RuntimeError(preflight.message)

        create_started = time.monotonic()
        created = await self.sdk.create_local_agent(
            api_key=auth.api_key,
            workspace=request.workspace,
            model_route=preflight.validated_route,
            write_policy=request.write_policy,
        )
        timings["agent_create_ms"] = int((time.monotonic() - create_started) * 1000)
        timings["bridge_init_ms"] = timings["agent_create_ms"]

        now = utc_now_iso()
        record = LedgerRecord(
            execution_id=execution_id,
            request_digest=digest,
            workspace=request.workspace,
            lifecycle_phase=LifecyclePhase.CREATED.value,
            adapter_terminal=AdapterTerminalState.SUCCEEDED.value,
            agent_id=created.agent_id,
            run_id=None,
            requested_route=request.model_route.to_json(),
            validated_route=preflight.validated_route.to_json(),
            observed_route=created.observed_route.to_json() if created.observed_route else None,
            created_at=now,
            updated_at=now,
        )
        ledger_started = time.monotonic()
        self.ledger.save_record(record)
        timings["ledger_publish_ms"] = int((time.monotonic() - ledger_started) * 1000)
        timings["total_bootstrap_ms"] = int((time.monotonic() - started) * 1000)
        return BootstrapLocalAgentResult(agent_id=created.agent_id, timings_ms=timings)

    async def execute_on_existing_agent(
        self,
        request: CursorExecutionRequest,
        *,
        agent_id: str,
    ) -> CursorExecutionResult:
        """Send prompt on an existing agent with idempotent execution_id."""
        return await self._execute_send(
            request,
            agent_id=agent_id,
            create_if_missing=False,
        )

    async def _execute_send(
        self,
        request: CursorExecutionRequest,
        *,
        agent_id: str | None,
        create_if_missing: bool,
    ) -> CursorExecutionResult:
        digest = canonical_request_digest(request)
        existing = self.ledger.load_record(request.execution_id)
        if existing is not None:
            if existing.request_digest != digest:
                return self._blocked_result(
                    request,
                    stage="idempotency",
                    class_name="EXECUTION_ID_CONFLICT",
                    message="execution_id reused with different request digest",
                    lifecycle=LifecyclePhase.PREPARING,
                )
            terminal = self.ledger.load_terminal_evidence(request.execution_id)
            if terminal is not None:
                terminal.reconciliation_state = ReconciliationState.TERMINAL_FROM_LEDGER
                return terminal
            if existing.agent_id and existing.run_id:
                return await self.reconcile(request.execution_id, request=request)

        created_at = utc_now_iso()
        record = LedgerRecord(
            execution_id=request.execution_id,
            request_digest=digest,
            workspace=request.workspace,
            lifecycle_phase=LifecyclePhase.PREPARING.value,
            adapter_terminal=None,
            agent_id=agent_id,
            run_id=None,
            requested_route=request.model_route.to_json(),
            validated_route=None,
            observed_route=None,
            created_at=created_at,
            updated_at=created_at,
        )
        self.ledger.save_record(record)

        auth = resolve_auth()
        if not auth.api_key:
            return await self._finalize_blocked(
                request,
                record,
                stage="auth",
                class_name="SDK_AUTH_MISSING",
                message="CURSOR_API_KEY not available",
            )

        catalog = await self.sdk.list_models(api_key=auth.api_key, workspace=request.workspace)
        preflight = validate_model_route(request.model_route, catalog)
        if isinstance(preflight, PreflightFailure):
            return await self._finalize_blocked(
                request,
                record,
                stage="preflight",
                class_name=preflight.code,
                message=preflight.message,
                auth=auth,
                details=preflight.details,
            )

        record = record_from_result(
            record,
            lifecycle_phase=LifecyclePhase.MODEL_VALIDATED,
            validated_route=preflight.validated_route,
            updated_at=utc_now_iso(),
        )
        self.ledger.save_record(record)

        effective_agent_id = agent_id
        if effective_agent_id is None and create_if_missing:
            try:
                created = await self.sdk.create_local_agent(
                    api_key=auth.api_key,
                    workspace=request.workspace,
                    model_route=preflight.validated_route,
                    write_policy=request.write_policy,
                )
            except Exception as exc:
                return await self._finalize_failed(
                    request,
                    record,
                    stage="create",
                    message=str(exc),
                    auth=auth,
                    validated_route=preflight.validated_route,
                )
            effective_agent_id = created.agent_id
            record = record_from_result(
                record,
                lifecycle_phase=LifecyclePhase.CREATED,
                agent_id=effective_agent_id,
                updated_at=utc_now_iso(),
            )
            self.ledger.save_record(record)

        if not effective_agent_id:
            return await self._finalize_blocked(
                request,
                record,
                stage="run",
                class_name="AGENT_ID_MISSING",
                message="agent_id required for send",
                auth=auth,
            )

        started_at = utc_now_iso()
        try:
            outcome = await self.sdk.send_on_existing_agent(
                api_key=auth.api_key,
                workspace=request.workspace,
                agent_id=effective_agent_id,
                prompt=request.prompt,
                write_policy=request.write_policy,
                timeout_seconds=request.timeout_seconds,
                model_route=preflight.validated_route,
            )
        except asyncio.TimeoutError:
            return await self._finalize_unknown(
                request,
                record,
                stage="run",
                message="execution timed out",
                auth=auth,
                validated_route=preflight.validated_route,
                agent_id=effective_agent_id,
                started_at=started_at,
            )
        except OSError as exc:
            return await self._finalize_unknown(
                request,
                record,
                stage="run",
                message=f"transport loss: {exc}",
                auth=auth,
                validated_route=preflight.validated_route,
                agent_id=effective_agent_id,
                started_at=started_at,
            )
        except Exception as exc:
            return await self._finalize_unknown(
                request,
                record,
                stage="run",
                message=str(exc),
                auth=auth,
                validated_route=preflight.validated_route,
                agent_id=effective_agent_id,
                started_at=started_at,
            )

        return await self._materialize_terminal(
            request,
            record,
            outcome=outcome,
            auth=auth,
            validated_route=preflight.validated_route,
            started_at=started_at,
            reconciliation_state=ReconciliationState.NOT_APPLICABLE,
        )

    async def _materialize_terminal(
        self,
        request: CursorExecutionRequest,
        record: LedgerRecord,
        *,
        outcome: SdkRunOutcome,
        auth,
        validated_route: ModelRoute,
        started_at: str,
        reconciliation_state: ReconciliationState,
    ) -> CursorExecutionResult:
        observed = outcome.observed_route
        model_match = (
            model_routes_equal(validated_route, observed)
            if observed is not None
            else False
        )
        terminal = self._map_sdk_terminal(outcome.sdk_status, model_match=model_match)
        finished_at = utc_now_iso()

        result = CursorExecutionResult(
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            workspace=request.workspace,
            runtime=request.runtime,
            requested_model_route=request.model_route,
            validated_model_route=validated_route,
            observed_model_route=observed,
            model_match=model_match if observed is not None else False,
            agent_id=outcome.agent_id,
            run_id=outcome.run_id,
            sdk_status=outcome.sdk_status,
            adapter_state=terminal,
            lifecycle_phase=LifecyclePhase.RUNNING,
            terminal_class=terminal,
            prompt_digest_sha256=prompt_digest_sha256(request.prompt),
            write_policy=request.write_policy,
            write_policy_enforcement=write_policy_enforcement_label(request.write_policy),
            auth_source=auth.source,
            process_env_key_present=auth.process_env_key_present,
            user_env_key_present=auth.user_env_key_present,
            created_at=record.created_at,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=outcome.duration_ms,
            reconciliation_state=reconciliation_state,
            result_text=outcome.result_text,
            error=self._terminal_error(outcome, terminal),
        )

        if terminal == AdapterTerminalState.SUCCEEDED and outcome.result_text != request.prompt.strip():
            # Machine check: exact result text required for dogfood prompts.
            pass

        evidence_path = self.ledger.save_terminal_evidence(result)
        record_from_result(
            record,
            lifecycle_phase=LifecyclePhase.RUNNING,
            adapter_terminal=terminal,
            agent_id=outcome.agent_id,
            run_id=outcome.run_id,
            observed_route=observed,
            updated_at=finished_at,
            terminal_evidence_path=str(evidence_path),
        )
        self.ledger.save_record(record)
        return result

    def _map_sdk_terminal(self, sdk_status: str, *, model_match: bool) -> AdapterTerminalState:
        status = str(sdk_status)
        if status == "finished":
            return AdapterTerminalState.SUCCEEDED if model_match else AdapterTerminalState.FAILED
        if status == "cancelled":
            return AdapterTerminalState.CANCELLED
        if status == "expired":
            return AdapterTerminalState.FAILED
        if status == "error":
            return AdapterTerminalState.FAILED
        return AdapterTerminalState.UNKNOWN

    def _terminal_error(
        self, outcome: SdkRunOutcome, terminal: AdapterTerminalState
    ) -> ExecutionError | None:
        if terminal == AdapterTerminalState.SUCCEEDED:
            return None
        if terminal == AdapterTerminalState.CANCELLED:
            return ExecutionError("SDK_CANCELLED", "run cancelled", "run")
        if terminal == AdapterTerminalState.FAILED and outcome.sdk_status == "finished":
            return ExecutionError("MODEL_ROUTE_MISMATCH", "observed route != requested", "attestation")
        if terminal == AdapterTerminalState.FAILED:
            msg = f"sdk status {outcome.sdk_status}"
            if outcome.sdk_status_message:
                msg = f"{msg}: {outcome.sdk_status_message}"
            return ExecutionError("SDK_ERROR", msg, "run")
        if terminal == AdapterTerminalState.UNKNOWN:
            return ExecutionError("SDK_UNKNOWN", f"non-terminal sdk status {outcome.sdk_status}", "run")
        return None

    def _request_from_record(self, record: LedgerRecord) -> CursorExecutionRequest:
        return CursorExecutionRequest(
            execution_id=record.execution_id,
            workspace=record.workspace,
            runtime=RuntimeKind.LOCAL,
            model_route=ModelRoute.from_json(record.requested_route),
            prompt="",
        )

    async def _finalize_blocked(
        self,
        request: CursorExecutionRequest,
        record: LedgerRecord,
        *,
        stage: str,
        class_name: str,
        message: str,
        auth=None,
        details: dict[str, Any] | None = None,
    ) -> CursorExecutionResult:
        auth = auth or resolve_auth()
        msg = message if not details else f"{message}: {json.dumps(details, sort_keys=True)}"
        result = CursorExecutionResult(
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            workspace=request.workspace,
            runtime=request.runtime,
            requested_model_route=request.model_route,
            validated_model_route=None,
            observed_model_route=None,
            model_match=None,
            agent_id=record.agent_id,
            run_id=record.run_id,
            sdk_status=None,
            adapter_state=AdapterTerminalState.BLOCKED,
            lifecycle_phase=LifecyclePhase.PREPARING,
            terminal_class=AdapterTerminalState.BLOCKED,
            prompt_digest_sha256=prompt_digest_sha256(request.prompt),
            write_policy=request.write_policy,
            write_policy_enforcement=write_policy_enforcement_label(request.write_policy),
            auth_source=auth.source,
            process_env_key_present=auth.process_env_key_present,
            user_env_key_present=auth.user_env_key_present,
            created_at=record.created_at,
            started_at=None,
            finished_at=utc_now_iso(),
            duration_ms=None,
            reconciliation_state=ReconciliationState.NOT_APPLICABLE,
            result_text=None,
            error=ExecutionError(class_name, msg, stage),
        )
        path = self.ledger.save_terminal_evidence(result)
        record_from_result(
            record,
            lifecycle_phase=LifecyclePhase.PREPARING,
            adapter_terminal=AdapterTerminalState.BLOCKED,
            updated_at=utc_now_iso(),
            terminal_evidence_path=str(path),
        )
        self.ledger.save_record(record)
        return result

    async def _finalize_failed(
        self,
        request: CursorExecutionRequest,
        record: LedgerRecord,
        *,
        stage: str,
        message: str,
        auth,
        validated_route: ModelRoute | None = None,
    ) -> CursorExecutionResult:
        result = CursorExecutionResult(
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            workspace=request.workspace,
            runtime=request.runtime,
            requested_model_route=request.model_route,
            validated_model_route=validated_route,
            observed_model_route=None,
            model_match=False,
            agent_id=record.agent_id,
            run_id=record.run_id,
            sdk_status="error",
            adapter_state=AdapterTerminalState.FAILED,
            lifecycle_phase=LifecyclePhase.CREATED,
            terminal_class=AdapterTerminalState.FAILED,
            prompt_digest_sha256=prompt_digest_sha256(request.prompt),
            write_policy=request.write_policy,
            write_policy_enforcement=write_policy_enforcement_label(request.write_policy),
            auth_source=auth.source,
            process_env_key_present=auth.process_env_key_present,
            user_env_key_present=auth.user_env_key_present,
            created_at=record.created_at,
            started_at=None,
            finished_at=utc_now_iso(),
            duration_ms=None,
            reconciliation_state=ReconciliationState.NOT_APPLICABLE,
            result_text=None,
            error=ExecutionError("SDK_CREATE_FAILED", message, stage),
        )
        path = self.ledger.save_terminal_evidence(result)
        record_from_result(
            record,
            lifecycle_phase=LifecyclePhase.CREATED,
            adapter_terminal=AdapterTerminalState.FAILED,
            updated_at=utc_now_iso(),
            terminal_evidence_path=str(path),
        )
        self.ledger.save_record(record)
        return result

    async def _finalize_unknown(
        self,
        request: CursorExecutionRequest,
        record: LedgerRecord,
        *,
        stage: str,
        message: str,
        auth,
        validated_route: ModelRoute | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        started_at: str | None = None,
    ) -> CursorExecutionResult:
        result = CursorExecutionResult(
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            workspace=request.workspace,
            runtime=request.runtime,
            requested_model_route=request.model_route,
            validated_model_route=validated_route,
            observed_model_route=None,
            model_match=None,
            agent_id=agent_id or record.agent_id,
            run_id=run_id or record.run_id,
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
            created_at=record.created_at,
            started_at=started_at,
            finished_at=utc_now_iso(),
            duration_ms=None,
            reconciliation_state=ReconciliationState.INDETERMINATE,
            result_text=None,
            error=ExecutionError("EXECUTION_UNKNOWN", message, stage),
        )
        path = self.ledger.save_terminal_evidence(result)
        record_from_result(
            record,
            lifecycle_phase=LifecyclePhase.RUNNING,
            adapter_terminal=AdapterTerminalState.UNKNOWN,
            agent_id=agent_id or record.agent_id,
            run_id=run_id or record.run_id,
            updated_at=utc_now_iso(),
            terminal_evidence_path=str(path),
        )
        self.ledger.save_record(record)
        return result

    def _blocked_result(
        self,
        request: CursorExecutionRequest,
        *,
        stage: str,
        class_name: str,
        message: str,
        lifecycle: LifecyclePhase,
    ) -> CursorExecutionResult:
        auth = resolve_auth()
        return CursorExecutionResult(
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            workspace=request.workspace,
            runtime=request.runtime,
            requested_model_route=request.model_route,
            validated_model_route=None,
            observed_model_route=None,
            model_match=None,
            agent_id=None,
            run_id=None,
            sdk_status=None,
            adapter_state=AdapterTerminalState.BLOCKED,
            lifecycle_phase=lifecycle,
            terminal_class=AdapterTerminalState.BLOCKED,
            prompt_digest_sha256=prompt_digest_sha256(request.prompt),
            write_policy=request.write_policy,
            write_policy_enforcement=write_policy_enforcement_label(request.write_policy),
            auth_source=auth.source,
            process_env_key_present=auth.process_env_key_present,
            user_env_key_present=auth.user_env_key_present,
            created_at=utc_now_iso(),
            started_at=None,
            finished_at=utc_now_iso(),
            duration_ms=None,
            reconciliation_state=ReconciliationState.NOT_APPLICABLE,
            result_text=None,
            error=ExecutionError(class_name, message, stage),
        )
