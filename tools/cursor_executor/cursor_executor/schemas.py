"""JSON-serializable contracts for Cursor executor adapter V0.1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class RuntimeKind(str, Enum):
    LOCAL = "LOCAL"
    CLOUD = "CLOUD"


class WritePolicy(str, Enum):
    READ_ONLY = "READ_ONLY"
    MUTATING = "MUTATING"


class AuthSource(str, Enum):
    PROCESS_ENV = "PROCESS_ENV"
    WINDOWS_USER_ENV = "WINDOWS_USER_ENV"
    NONE = "NONE"


class LifecyclePhase(str, Enum):
    PREPARING = "PREPARING"
    AUTH_RESOLVED = "AUTH_RESOLVED"
    MODEL_VALIDATED = "MODEL_VALIDATED"
    CREATED = "CREATED"
    RUNNING = "RUNNING"


class AdapterTerminalState(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class ReconciliationState(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    TERMINAL_FROM_LEDGER = "TERMINAL_FROM_LEDGER"
    TERMINAL_FROM_SDK = "TERMINAL_FROM_SDK"
    LIVE_OBSERVED = "LIVE_OBSERVED"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class ModelRoute:
    model_id: str
    params: dict[str, str]

    def normalized_params(self) -> dict[str, str]:
        return dict(sorted(self.params.items()))

    def to_json(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "params": self.normalized_params(),
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "ModelRoute":
        params_raw = value.get("params") or {}
        if not isinstance(params_raw, Mapping):
            raise ValueError("model_route.params must be a mapping")
        params = {str(k): str(v) for k, v in params_raw.items()}
        return cls(model_id=str(value["model_id"]), params=params)


@dataclass
class CursorExecutionRequest:
    execution_id: str
    workspace: str
    runtime: RuntimeKind
    model_route: ModelRoute
    prompt: str
    write_policy: WritePolicy = WritePolicy.READ_ONLY
    correlation_id: str | None = None
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "correlation_id": self.correlation_id,
            "workspace": self.workspace,
            "runtime": self.runtime.value,
            "model_route": self.model_route.to_json(),
            "prompt": self.prompt,
            "write_policy": self.write_policy.value,
            "timeout_seconds": self.timeout_seconds,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "CursorExecutionRequest":
        return cls(
            execution_id=str(value["execution_id"]),
            correlation_id=value.get("correlation_id"),
            workspace=str(value["workspace"]),
            runtime=RuntimeKind(str(value["runtime"])),
            model_route=ModelRoute.from_json(value["model_route"]),
            prompt=str(value["prompt"]),
            write_policy=WritePolicy(str(value.get("write_policy", "READ_ONLY"))),
            timeout_seconds=value.get("timeout_seconds"),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass(frozen=True)
class BootstrapLocalAgentResult:
    """Create-only session bootstrap (no prompt send / run wait)."""

    agent_id: str
    timings_ms: dict[str, int]

    def to_json(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "timings_ms": dict(self.timings_ms)}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "BootstrapLocalAgentResult":
        raw = value.get("timings_ms") or {}
        timings = {str(k): int(v) for k, v in raw.items()}
        return cls(agent_id=str(value["agent_id"]), timings_ms=timings)


@dataclass
class ExecutionError:
    class_name: str
    message: str
    stage: str

    def to_json(self) -> dict[str, Any]:
        return {
            "class": self.class_name,
            "message": self.message,
            "stage": self.stage,
        }


@dataclass
class CursorExecutionResult:
    execution_id: str
    correlation_id: str | None
    workspace: str
    runtime: RuntimeKind
    requested_model_route: ModelRoute
    observed_model_route: ModelRoute | None
    model_match: bool | None
    agent_id: str | None
    run_id: str | None
    sdk_status: str | None
    adapter_state: AdapterTerminalState
    lifecycle_phase: LifecyclePhase | None
    terminal_class: AdapterTerminalState
    prompt_digest_sha256: str
    write_policy: WritePolicy
    write_policy_enforcement: str
    auth_source: AuthSource
    process_env_key_present: bool
    user_env_key_present: bool
    created_at: str
    started_at: str | None
    finished_at: str | None
    duration_ms: int | None
    reconciliation_state: ReconciliationState
    result_text: str | None
    validated_model_route: ModelRoute | None = None
    error: ExecutionError | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "execution_id": self.execution_id,
            "correlation_id": self.correlation_id,
            "workspace": self.workspace,
            "runtime": self.runtime.value,
            "requested_model_route": self.requested_model_route.to_json(),
            "observed_model_route": self.observed_model_route.to_json()
            if self.observed_model_route
            else None,
            "validated_model_route": self.validated_model_route.to_json()
            if self.validated_model_route
            else None,
            "model_match": self.model_match,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "sdk_status": self.sdk_status,
            "adapter_state": self.adapter_state.value,
            "lifecycle_phase": self.lifecycle_phase.value if self.lifecycle_phase else None,
            "terminal_class": self.terminal_class.value,
            "prompt_digest_sha256": self.prompt_digest_sha256,
            "write_policy": self.write_policy.value,
            "write_policy_enforcement": self.write_policy_enforcement,
            "auth_source": self.auth_source.value,
            "process_env_key_present": self.process_env_key_present,
            "user_env_key_present": self.user_env_key_present,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "reconciliation_state": self.reconciliation_state.value,
            "result_text": self.result_text,
            "error": self.error.to_json() if self.error else None,
        }
        return payload

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "CursorExecutionResult":
        observed = value.get("observed_model_route")
        validated = value.get("validated_model_route")
        err = value.get("error")
        return cls(
            execution_id=str(value["execution_id"]),
            correlation_id=value.get("correlation_id"),
            workspace=str(value["workspace"]),
            runtime=RuntimeKind(str(value["runtime"])),
            requested_model_route=ModelRoute.from_json(value["requested_model_route"]),
            observed_model_route=ModelRoute.from_json(observed) if observed else None,
            model_match=value.get("model_match"),
            agent_id=value.get("agent_id"),
            run_id=value.get("run_id"),
            sdk_status=value.get("sdk_status"),
            adapter_state=AdapterTerminalState(str(value["adapter_state"])),
            lifecycle_phase=LifecyclePhase(str(value["lifecycle_phase"]))
            if value.get("lifecycle_phase")
            else None,
            terminal_class=AdapterTerminalState(str(value["terminal_class"])),
            prompt_digest_sha256=str(value["prompt_digest_sha256"]),
            write_policy=WritePolicy(str(value["write_policy"])),
            write_policy_enforcement=str(value["write_policy_enforcement"]),
            auth_source=AuthSource(str(value["auth_source"])),
            process_env_key_present=bool(value.get("process_env_key_present")),
            user_env_key_present=bool(value.get("user_env_key_present")),
            created_at=str(value["created_at"]),
            started_at=value.get("started_at"),
            finished_at=value.get("finished_at"),
            duration_ms=value.get("duration_ms"),
            reconciliation_state=ReconciliationState(str(value["reconciliation_state"])),
            result_text=value.get("result_text"),
            validated_model_route=ModelRoute.from_json(validated) if validated else None,
            error=ExecutionError(
                class_name=str(err["class"]),
                message=str(err["message"]),
                stage=str(err["stage"]),
            )
            if err
            else None,
        )


def canonical_request_digest(request: CursorExecutionRequest) -> str:
    """Stable digest for idempotency / conflict detection (no secrets)."""
    material = {
        "execution_id": request.execution_id,
        "workspace": request.workspace,
        "runtime": request.runtime.value,
        "model_route": request.model_route.to_json(),
        "prompt": request.prompt,
        "write_policy": request.write_policy.value,
        "timeout_seconds": request.timeout_seconds,
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def prompt_digest_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def model_routes_equal(requested: ModelRoute, observed: ModelRoute) -> bool:
    return requested.model_id == observed.model_id and (
        requested.normalized_params() == observed.normalized_params()
    )


def model_route_from_sdk_model(model: Mapping[str, Any] | None) -> ModelRoute | None:
    if not model:
        return None
    params_list = model.get("params") or []
    params: dict[str, str] = {}
    if isinstance(params_list, list):
        for item in params_list:
            if isinstance(item, Mapping):
                params[str(item.get("id", ""))] = str(item.get("value", ""))
    return ModelRoute(model_id=str(model.get("id", "")), params=params)


def sdk_model_catalog_entry_to_dict(entry: Any) -> dict[str, Any]:
    if hasattr(entry, "to_json"):
        return entry.to_json()
    if isinstance(entry, Mapping):
        return dict(entry)
    return {"id": getattr(entry, "id", ""), "parameters": []}
